"""`python -m reference_stack.loaders ... --dry-run` prints a plan. `--execute` runs it against Databricks through the owner's own `databricks`
command line, and only when the program file records an approved G5 whose scope names exactly this host, catalog and schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reference_stack.loaders.engines import PlanError
from reference_stack.loaders.plan import render_dry_run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reference_stack.loaders")
    p.add_argument("dataset", choices=("synthetic", "online_retail"))
    p.add_argument("--data", required=True, type=Path, help="the generator or converter output directory")
    p.add_argument("--engine", required=True, choices=("databricks", "snowflake"))
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print the plan and run nothing")
    mode.add_argument("--execute", action="store_true", help="run the plan (Databricks only; needs a recorded G5 approval)")
    p.add_argument("--with-curated-view", action="store_true", help="synthetic only: also create the orders_current view (needs its own approval to execute)")
    p.add_argument("--view-only", action="store_true", help="synthetic only: create just the orders_current view on a schema that already holds the tables")
    p.add_argument("--usd-view-only", action="store_true", help="synthetic only: create just the orders_usd_gross view (needs orders_current to exist)")
    p.add_argument("--max-statements", type=int, help="execute: stop after this many statements")
    p.add_argument("--profile", help="execute: the databricks CLI profile")
    p.add_argument("--host", help="execute: the workspace host, which must be named in the approval")
    p.add_argument("--warehouse-name", help="execute: the SQL warehouse to use")
    p.add_argument("--approval-id", help="execute: the id of the approval in the program file")
    p.add_argument("--cap-minutes", type=float, default=15.0, help="execute: wall-clock cap standing in for the warehouse-time cap")
    args = p.parse_args(argv)
    try:
        if args.dataset == "synthetic":
            from reference_stack.loaders.synthetic import synthetic_plan

            plan = synthetic_plan(args.data, args.engine, args.catalog, args.schema, with_curated_view=args.with_curated_view or args.view_only, view_only=args.view_only, usd_view_only=args.usd_view_only)
        else:
            from reference_stack.loaders.online_retail import retail_plan

            plan = retail_plan(args.data, args.engine, args.catalog, args.schema)
    except (PlanError, OSError, KeyError) as exc:
        print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(render_dry_run(plan))
    if args.dry_run:
        return 0
    return _execute(plan, args)


def _execute(plan, args) -> int:  # noqa: ANN001
    from reference_stack.loaders.approval import ApprovalError, check_approval
    from reference_stack.loaders.databricks_cli import DatabricksCliSession, EngineError, find_warehouse_id
    from reference_stack.loaders.plan import execute

    if args.engine != "databricks" or not (args.profile and args.host and args.warehouse_name and args.approval_id):
        print("refused: --execute needs --engine databricks, --profile, --host, --warehouse-name and --approval-id", file=sys.stderr)
        return 2
    try:
        check_approval(args.approval_id, args.host, args.catalog, args.schema, args.warehouse_name, args.cap_minutes, also=("orders_usd_gross",) if args.usd_view_only else (("orders_current",) if (args.with_curated_view or args.view_only) else ()))
        session = DatabricksCliSession(args.profile, find_warehouse_id(args.profile, args.warehouse_name), args.cap_minutes * 60, max_statements=args.max_statements)
    except (ApprovalError, EngineError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    report = execute(plan, session, session.upload)
    print(f"\nDONE {len(report.done)} steps and checks; stopped={report.stopped or 'no'}; failures={report.failures or 'none'}; statements={session.statements}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
