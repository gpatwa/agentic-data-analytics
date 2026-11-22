"""Dry run by default: prints the plan and the worst case, makes no call. `--execute` needs the owner's recorded approval (never inferred)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

from reference_stack.loaders.approval import ApprovalError, check_approval
from reference_stack.scale_test import report as report_mod
from reference_stack.scale_test import workload
from reference_stack.scale_test.history import fetch
from reference_stack.scale_test.runner import Budget, Plan, Stop, run
from reference_stack.scale_test.warehouses import DBU_PER_HOUR, PREFIX, SIZES, WarehouseAdmin

MAX_CAP_MINUTES = 45.0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reference_stack.scale_test", description=__doc__)
    p.add_argument("--tier", default="M", help="the local tier used to compute the expected answers (must be the tier loaded in the warehouse)")
    p.add_argument("--catalog", default="dev")
    p.add_argument("--schema", default="compass_synthetic_food_delivery_m")
    p.add_argument("--sizes", default=",".join(SIZES))
    p.add_argument("--warm-runs", type=int, default=3)
    p.add_argument("--cap-minutes", type=float, default=MAX_CAP_MINUTES)
    p.add_argument("--max-statements", type=int, default=600)
    p.add_argument("--usd-per-dbu", type=float, help="the assumed price; never defaulted")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--profile")
    p.add_argument("--host")
    p.add_argument("--approval-id")
    p.add_argument("--out", help="execute: directory for the JSON and markdown report")
    return p


def _dry_run(plan: Plan, statements: list[workload.Statement], args: argparse.Namespace) -> None:
    n = plan.statement_count(len(statements))
    hours = args.cap_minutes / 60
    print(f"DRY RUN: nothing was executed and no network call was made. Target {args.catalog}.{args.schema} (tier {args.tier} expected answers)")
    print(f"statements in the workload: {len(statements)}; warehouse statements planned: {n} (budget {plan.max_statements}); wall-clock cap {args.cap_minutes:g} min")
    print(f"temporary warehouses: {', '.join(f'{PREFIX}{s.lower()}-<run>' for s in plan.sizes)} (auto-stop 5 min, deleted at the end)")
    print(f"worst-case uptime if every warehouse ran the whole cap: {sum(DBU_PER_HOUR[s] for s in plan.sizes) * hours:.1f} DBU (assumed rates: {DBU_PER_HOUR})")
    print("only SELECT statements on the target schema leave the process; a cache-busting comment is added to each\n")
    for st in statements:
        print(f"  {st.shape:22} {st.metric_id:20} by {', '.join(st.dimension_ids) or '-':32} groups={len(st.expected):4} sha256={st.text_hash[:12]}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    sizes = tuple(s.strip() for s in args.sizes.split(",") if s.strip())
    if any(s not in DBU_PER_HOUR for s in sizes):
        print("refused: unknown size", file=sys.stderr)
        return 2
    plan = Plan(sizes, args.warm_runs, max_statements=args.max_statements, max_minutes=args.cap_minutes)
    from reference_stack.synthetic.config import GeneratorConfig
    from reference_stack.synthetic.food_delivery import generate
    with tempfile.TemporaryDirectory() as tmp:
        generate(GeneratorConfig(tier=args.tier), Path(tmp))
        contract = workload.load_contract()
        statements = workload.build(contract, workload.local_connection(Path(tmp)), args.catalog, args.schema)
    n = plan.statement_count(len(statements))
    if not args.execute:
        _dry_run(plan, statements, args)
        return 0
    if not (args.profile and args.host and args.approval_id and args.out):
        print("refused: --execute needs --profile, --host, --approval-id and --out", file=sys.stderr)
        return 2
    if n > plan.max_statements or args.cap_minutes > MAX_CAP_MINUTES:
        print("refused: the plan exceeds the statement budget or the cap", file=sys.stderr)
        return 2
    try:
        check_approval(args.approval_id, args.host, args.catalog, args.schema, PREFIX, args.cap_minutes, also=("create_warehouses", "orders_current", "orders_usd_gross"), max_cap_minutes=MAX_CAP_MINUTES)
    except ApprovalError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return _execute(args, plan, statements)


def _execute(args: argparse.Namespace, plan: Plan, statements: list[workload.Statement]) -> int:
    from reference_stack.loaders.databricks_cli import DatabricksCliSession, EngineError
    run_id = uuid.uuid4().hex[:8]
    cap = args.cap_minutes * 60
    admin_session = DatabricksCliSession(args.profile, "", cap)
    admin = WarehouseAdmin(admin_session)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, history, uptime, code = [], {}, {}, 0
    try:
        ids = {size: admin.create(size, run_id) for size in plan.sizes}
        for wid in ids.values():
            admin.wait_running(wid)
        sessions = {size: DatabricksCliSession(args.profile, wid, cap) for size, wid in ids.items()}
        first = sessions[plan.sizes[0]]
        for view in ("orders_current", "orders_usd_gross"):  # read-only preflight: the views must already exist in this schema
            first.query_with_id(f"SELECT COUNT(*) FROM `{args.catalog}`.`{args.schema}`.`{view}`")
        budget = Budget(plan.max_statements, time.monotonic() + cap)
        try:
            rows = run(plan, statements, sessions, args.catalog, args.schema, run_id, budget)
        except Stop as exc:
            print(f"STOPPED: {exc}", file=sys.stderr)
            code = 3
        finally:
            uptime = {size: time.monotonic() - at for _wid, (size, at) in admin.created.items()}
        history = fetch(admin_session, [r.statement_id for r in rows])
    except EngineError as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        code = 3
    finally:
        left = admin.teardown()
        if left:
            print(f"WARNING: scale-test warehouses still present, delete them yourself: {', '.join(left)}", file=sys.stderr)
            code = 4
    rep = report_mod.build(rows, history, usd_per_dbu=args.usd_per_dbu, uptime_seconds=uptime)
    rep["run_id"], rep["stopped"] = run_id, code != 0
    (out / f"scale-{run_id}.json").write_text(json.dumps(rep, indent=1))
    (out / f"scale-{run_id}.md").write_text(report_mod.markdown(rep))
    print(f"report written to {out}/scale-{run_id}.(json|md); teardown {'clean' if code != 4 else 'INCOMPLETE'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
