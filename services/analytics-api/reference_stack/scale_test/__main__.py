"""Dry run by default: prints the plan and the worst case, makes no call. `--execute` needs the owner's recorded approval (never inferred).

Sizes run one at a time, smallest first; a larger size is tried only while the last step still cut the warm median by `--min-gain`."""

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
from reference_stack.scale_test.runner import Budget, Outcome, Plan, Stop, run
from reference_stack.scale_test.warehouses import DBU_PER_HOUR, PREFIX, SIZES, WarehouseAdmin

MAX_CAP_MINUTES = 45.0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reference_stack.scale_test", description=__doc__)
    p.add_argument("--tier", default="M", help="the local tier used to compute the expected answers (must be the tier loaded in the warehouse)")
    p.add_argument("--catalog", default="dev")
    p.add_argument("--schema", default="compass_synthetic_food_delivery_m")
    p.add_argument("--sizes", default=",".join(SIZES))
    p.add_argument("--warm-runs", type=int, default=2)
    p.add_argument("--min-gain", type=float, default=0.15, help="try a larger size only if the last step cut the warm median by this fraction")
    p.add_argument("--auto-stop-mins", type=int, default=2)
    p.add_argument("--baseline-warehouse-name", help="use this existing warehouse for the smallest size (read-only use; never resized or deleted); the approval must name it")
    p.add_argument("--cap-minutes", type=float, default=30.0)
    p.add_argument("--max-statements", type=int, default=450)
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
    print(f"statements in the workload: {len(statements)}; at most {n} warehouse statements (budget {plan.max_statements}); wall-clock cap {args.cap_minutes:g} min")
    print(f"sizes, one warehouse at a time, smallest first: {', '.join(plan.sizes)}; a larger size is tried only if the last step cut the warm median by {plan.min_gain:.0%}")
    print(f"temporary warehouses: {PREFIX}<size>-<run> (auto-stop {args.auto_stop_mins} min, each deleted before the next opens)" + (f"; {args.baseline_warehouse_name!r} is borrowed for {plan.sizes[0]}, never resized or deleted" if args.baseline_warehouse_name else ""))
    worst = max(DBU_PER_HOUR[s] for s in plan.sizes) * hours
    even = sum(DBU_PER_HOUR[s] for s in plan.sizes) / len(plan.sizes) * hours
    print(f"cost ceiling: {worst:.1f} DBU if the whole cap were spent on the largest size; {even:.1f} DBU if it were split evenly (assumed rates: {DBU_PER_HOUR})")
    print("only SELECT statements on the target schema leave the process; a cache-busting comment is added to each\n")
    for st in statements:
        print(f"  {st.shape:22} {st.metric_id:20} by {', '.join(st.dimension_ids) or '-':32} groups={len(st.expected):4} sha256={st.text_hash[:12]}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    sizes = tuple(s.strip() for s in args.sizes.split(",") if s.strip())
    if any(s not in DBU_PER_HOUR for s in sizes):
        print("refused: unknown size", file=sys.stderr)
        return 2
    if args.baseline_warehouse_name and sizes[0] != "2X-Small":
        print("refused: a borrowed baseline warehouse is 2X-Small, so the smallest size must be 2X-Small", file=sys.stderr)
        return 2
    plan = Plan(sizes, args.warm_runs, max_statements=args.max_statements, max_minutes=args.cap_minutes, min_gain=args.min_gain)
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
        check_approval(args.approval_id, args.host, args.catalog, args.schema, PREFIX, args.cap_minutes, also=("create_warehouses", "orders_current", "orders_usd_gross", *((args.baseline_warehouse_name,) if args.baseline_warehouse_name else ())), max_cap_minutes=MAX_CAP_MINUTES)
    except ApprovalError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return _execute(args, plan, statements)


class LiveProvider:
    """One warehouse at a time. A refused size is skipped; a borrowed baseline is only read from."""

    def __init__(self, admin: WarehouseAdmin, args: argparse.Namespace, run_id: str, cap: float, baseline: str | None) -> None:
        from reference_stack.loaders.databricks_cli import DatabricksCliSession
        self._admin, self._args, self._run_id, self._cap, self._baseline = admin, args, run_id, cap, baseline
        self._session_cls, self._ids, self._started = DatabricksCliSession, {}, {}

    def open(self, size: str):  # noqa: ANN201
        from reference_stack.loaders.databricks_cli import EngineError, find_warehouse_id
        try:
            if self._baseline and not self._ids:
                wid = find_warehouse_id(self._args.profile, self._baseline)
            else:
                wid = self._admin.create(size, self._run_id)
                self._admin.wait_running(wid)
                self._started[size] = wid
        except EngineError as exc:
            print(f"NOTE: no {size} warehouse ({exc}); skipped", file=sys.stderr)
            return None
        self._ids[size] = wid
        return self._session_cls(self._args.profile, wid, self._cap)

    def close(self, size: str) -> float:
        wid = self._started.pop(size, None)
        return self._admin.release(wid) if wid else 0.0


def _execute(args: argparse.Namespace, plan: Plan, statements: list[workload.Statement]) -> int:
    from reference_stack.loaders.databricks_cli import DatabricksCliSession, EngineError
    run_id = uuid.uuid4().hex[:8]
    cap = args.cap_minutes * 60
    admin_session = DatabricksCliSession(args.profile, "", cap)
    admin = WarehouseAdmin(admin_session, auto_stop_mins=args.auto_stop_mins)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    outcome, code = Outcome(), 0
    provider = LiveProvider(admin, args, run_id, cap, args.baseline_warehouse_name)
    try:
        budget = Budget(plan.max_statements, time.monotonic() + cap)
        history_for = lambda rows: fetch(admin_session, [r.statement_id for r in rows])  # noqa: E731
        try:
            run(plan, statements, _Preflighted(provider, args, budget), args.catalog, args.schema, run_id, budget, history_for, outcome)
        except Stop as exc:
            print(f"STOPPED: {exc}", file=sys.stderr)
            code = 3
        if not outcome.rows and not outcome.skipped and code == 0:
            raise EngineError("nothing_ran")
        if not outcome.rows and code == 0:
            raise EngineError("no_warehouse_could_be_created")
    except EngineError as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        code = 3
    finally:
        left = admin.teardown()
        if left:
            print(f"WARNING: scale-test warehouses still present, delete them yourself: {', '.join(left)}", file=sys.stderr)
            code = 4
    rep = report_mod.build(outcome.rows, outcome.history, usd_per_dbu=args.usd_per_dbu, uptime_seconds=outcome.uptime)
    rep |= {"run_id": run_id, "stopped": code != 0, "sizes_run": sorted({r.size for r in outcome.rows}, key=lambda s: plan.sizes.index(s)), "sizes_skipped": outcome.skipped,
            "stopped_escalating_after": outcome.stopped_escalating_after, "warm_median_ms_by_size": outcome.warm_median_ms}
    (out_dir / f"scale-{run_id}.json").write_text(json.dumps(rep, indent=1))
    (out_dir / f"scale-{run_id}.md").write_text(report_mod.markdown(rep))
    print(f"report written to {out_dir}/scale-{run_id}.(json|md); teardown {'clean' if code != 4 else 'INCOMPLETE'}")
    return code


class _Preflighted:
    """Wraps the provider so the first session also proves the two views exist (two read-only SELECTs) before any measured statement."""

    def __init__(self, inner: LiveProvider, args: argparse.Namespace, budget: Budget) -> None:
        self._inner, self._args, self._budget, self._done = inner, args, budget, False

    def open(self, size: str):  # noqa: ANN201
        session = self._inner.open(size)
        if session is not None and not self._done:
            for view in ("orders_current", "orders_usd_gross"):
                self._budget.take()
                session.query_with_id(f"SELECT COUNT(*) FROM `{self._args.catalog}`.`{self._args.schema}`.`{view}`")
            self._done = True
        return session

    def close(self, size: str) -> float:
        return self._inner.close(size)


if __name__ == "__main__":
    raise SystemExit(main())
