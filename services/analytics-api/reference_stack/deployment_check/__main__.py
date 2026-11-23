"""Dry run by default: prints the plan and makes no call. `--execute` needs the owner's recorded approval (never inferred) and the owner's own read-only service principal, whose
client id and secret are read ONLY from the environment variables COMPASS_CHECK_CLIENT_ID and COMPASS_CHECK_CLIENT_SECRET. They are never printed, logged or written to the report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

from reference_stack.loaders.approval import ApprovalError, check_approval

MAX_CAP_MINUTES = 20.0
ID_VAR, SECRET_VAR = "COMPASS_CHECK_CLIENT_ID", "COMPASS_CHECK_CLIENT_SECRET"
STEPS = (
    "POST /warm with the switch off: must answer skipped_disabled and record nothing",
    "PUT the switch on: the connect.warm_up_changed audit event must exist",
    "POST /warm: must answer 202 warming at once; the background warm-up (at most 4 certified SELECTs on the real OAuth gateway, each with the gateway's own EXPLAIN COST and two session SETs) must record connect.warm_up_ran with a code, a count and a duration only",
    "POST /warm again: must answer skipped_recent",
    "POST /analyze (no model: it fails at once): must not trigger a second warm-up",
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reference_stack.deployment_check", description=__doc__)
    p.add_argument("--host", required=True)
    p.add_argument("--catalog", default="dev")
    p.add_argument("--schema", default="compass_synthetic_food_delivery")
    p.add_argument("--warehouse-name", required=True)
    p.add_argument("--cap-minutes", type=float, default=20.0)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--profile", help="execute: the owner's databricks CLI profile, used only to look up the warehouse's id (a read-only listing)")
    p.add_argument("--approval-id")
    p.add_argument("--out")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cap_minutes > MAX_CAP_MINUTES:
        print("refused: the cap is above the maximum", file=sys.stderr)
        return 2
    if not args.execute:
        print(f"DRY RUN: nothing was executed and no network call was made. Local instance of the app, test identity key, SQLite control database, in-memory secret store; target {args.host}, {args.catalog}.{args.schema}, warehouse {args.warehouse_name!r}")
        print(f"wall-clock cap {args.cap_minutes:g} min; the connection may read only {args.catalog}.{args.schema}.orders_current and .orders_usd_gross")
        print(f"the owner's read-only service principal comes only from the environment variables {ID_VAR} and {SECRET_VAR} (never printed or recorded); the check refuses to run if either is missing")
        for i, step in enumerate(STEPS, 1):
            print(f"  {i}. {step}")
        print("no model is called, nothing is deployed, no Azure or warehouse object is created, resized, stopped or deleted")
        return 0
    if not (args.profile and args.approval_id and args.out):
        print("refused: --execute needs --profile, --approval-id and --out", file=sys.stderr)
        return 2
    client_id, client_secret = os.environ.get(ID_VAR, ""), os.environ.get(SECRET_VAR, "")
    if not client_id or not client_secret:
        print(f"refused: set {ID_VAR} and {SECRET_VAR} in your own terminal first (they are never printed)", file=sys.stderr)
        return 2
    try:
        check_approval(args.approval_id, args.host, args.catalog, args.schema, args.warehouse_name, args.cap_minutes,
                       also=("warmup_deployment_check", "orders_current", "orders_usd_gross"), max_cap_minutes=MAX_CAP_MINUTES)
    except ApprovalError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    try:
        import databricks.sdk.core  # noqa: F401  (the real connector and OAuth helper: pip install -r requirements-warehouses.txt)
        import databricks.sql  # noqa: F401
    except ImportError:
        print("refused: the Databricks connector is not installed (pip install -r requirements-warehouses.txt)", file=sys.stderr)
        return 2
    from fastapi.testclient import TestClient

    from reference_stack.deployment_check.check import build_local_app, register, run_check
    from reference_stack.loaders.databricks_cli import EngineError, find_warehouse_id

    try:
        warehouse_id = find_warehouse_id(args.profile, args.warehouse_name)
    except EngineError as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 3
    run_id = uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory() as tmp:
        local = build_local_app(Path(tmp))
        register(local, host=args.host, http_path=f"/sql/1.0/warehouses/{warehouse_id}", catalog=args.catalog, schema=args.schema, client_id=client_id, client_secret=client_secret,
                 warehouse=args.warehouse_name)
        result = run_check(TestClient(local.app, raise_server_exceptions=False), local, wait_seconds=args.cap_minutes * 60)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"deployment-check-{run_id}.json").write_text(json.dumps({"run_id": run_id, **result}, indent=1))
    print(f"report written to {out}/deployment-check-{run_id}.json; verdict: {result['verdict']}")
    return 0 if result["wired_path_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
