"""`python -m reference_stack.loaders ... --dry-run` prints a plan. There is deliberately no way to execute one from here: running against a
warehouse is G5, which needs the owner's approval, and no connector is wired."""

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
    p.add_argument("--dry-run", action="store_true", help="required: print the plan and run nothing")
    args = p.parse_args(argv)
    if not args.dry_run:
        print("refused: only --dry-run is available. Loading a warehouse needs the owner's approval of G5 and a connector that is not wired.", file=sys.stderr)
        return 2
    try:
        if args.dataset == "synthetic":
            from reference_stack.loaders.synthetic import synthetic_plan

            plan = synthetic_plan(args.data, args.engine, args.catalog, args.schema)
        else:
            from reference_stack.loaders.online_retail import retail_plan

            plan = retail_plan(args.data, args.engine, args.catalog, args.schema)
    except (PlanError, OSError, KeyError) as exc:
        print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(render_dry_run(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
