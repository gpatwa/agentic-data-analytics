"""Dry run by default: prints the plan and makes no call. `--execute` needs the owner's recorded approval (never inferred) and the owner's own read-only service principal, whose
client id and secret are read ONLY from the environment variables COMPASS_CHECK_CLIENT_ID and COMPASS_CHECK_CLIENT_SECRET. They are never printed, logged or written to the report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from reference_stack.deployment_check.runner import (
    DEFAULT_LEDGER,
    Ledger,
    RegistrationRunner,
    RunnerStop,
    pick_warehouse,
)
from reference_stack.loaders.approval import ApprovalError, check_approval

MAX_CAP_MINUTES = 20.0
VIEWS = ("orders_current", "orders_usd_gross")
PROVISION_NEEDLES = ("warmup_provision_check", *VIEWS)
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
    p.add_argument("--provision", action="store_true", help="create the temporary read-only principal itself with --profile (no environment variables), and remove it in the same run")
    p.add_argument("--sweep", action="store_true", help="remove what a crashed --provision run recorded in the ledger (by id, never by name), then stop")
    p.add_argument("--ledger", default=str(DEFAULT_LEDGER), help="where --provision records the ids it creates (never a secret)")
    return p


def _sdk_admin(profile: str, host: str) -> Any:
    """The owner's own signed-in CLI profile; this program never reads a token from it."""
    import databricks.sdk as sdk

    return sdk.WorkspaceClient(profile=profile)


def _admin_for(args: argparse.Namespace, admin_factory: Callable[[str, str], Any] | None) -> Any:
    admin = (admin_factory or _sdk_admin)(args.profile, args.host)
    if args.host not in str(getattr(getattr(admin, "config", None), "host", "")):
        raise RunnerStop("profile_host_mismatch")
    return admin


def _drivers_ok(connector: Any | None) -> bool:
    if connector is not None:
        return True
    try:
        import databricks.sdk.core  # noqa: F401  (the real connector and OAuth helper: pip install -r requirements-warehouses.txt)
        import databricks.sql  # noqa: F401
    except ImportError:
        return False
    return True


def _dry_run(args: argparse.Namespace) -> None:
    print(f"DRY RUN: nothing was executed and no network call was made. Local instance of the app, test identity key, SQLite control database, in-memory secret store; target {args.host}, {args.catalog}.{args.schema}, warehouse {args.warehouse_name!r}")
    print(f"wall-clock cap {args.cap_minutes:g} min; the connection may read only {args.catalog}.{args.schema}.orders_current and .orders_usd_gross")
    if args.provision:
        print("--provision: with the owner's signed-in CLI profile this run would create ONE temporary service principal, ONE OAuth secret held only in memory, USE CATALOG, USE SCHEMA and SELECT on the two views, "
              "and CAN_USE on the named warehouse (the product's own generated registration script), verify the grants, run the check, and remove all of it in the same run")
        print("no environment variables, no copied id or secret; a crash is cleaned up with --sweep, which removes only the ids this tool recorded in its ledger")
    else:
        print(f"the owner's read-only service principal comes only from the environment variables {ID_VAR} and {SECRET_VAR} (never printed or recorded); the check refuses to run if either is missing")
    for i, step in enumerate(STEPS, 1):
        print(f"  {i}. {step}")
    print("no model is called, nothing is deployed, no Azure object and no warehouse is created, resized, stopped or deleted" + (" (the one principal above is the only thing created, and it is removed)" if args.provision else ""))


def _sweep(args: argparse.Namespace, admin_factory: Callable[[str, str], Any] | None) -> int:
    from app.connect.grants import ConnectScope

    if not args.profile:
        print("refused: --sweep needs --profile", file=sys.stderr)
        return 2
    try:
        admin = _admin_for(args, admin_factory)
        runner = RegistrationRunner(admin, ceiling=ConnectScope(args.catalog, tuple((args.schema, v) for v in VIEWS)), warehouse_id="", warehouse_name=args.warehouse_name, ledger=Ledger(Path(args.ledger)))
        result = runner.sweep()
    except RunnerStop as stop:
        print(f"STOPPED: {stop.code}", file=sys.stderr)
        return 3
    print(f"sweep: {result['runs']} recorded run(s), {result['removed']} object(s) removed, left: {result['left'] or 'nothing'}")
    return 0 if not result["left"] else 1


def _provision(args: argparse.Namespace, admin_factory: Callable[[str, str], Any] | None, connector: Any | None) -> int:
    from fastapi.testclient import TestClient

    from app.connect.grants import ConnectScope
    from reference_stack.deployment_check.check import build_local_app, register, run_check

    run_id = uuid.uuid4().hex[:8]
    stopped, result, verify = "", {}, {}
    runner = None
    try:
        admin = _admin_for(args, admin_factory)
        ceiling = ConnectScope(args.catalog, tuple((args.schema, v) for v in VIEWS))
        runner = RegistrationRunner(admin, ceiling=ceiling, warehouse_id=pick_warehouse(admin, args.warehouse_name), warehouse_name=args.warehouse_name, ledger=Ledger(Path(args.ledger)))
        with runner.provision() as principal:
            report = runner.verify()
            verify = {"ok": report.ok, "missing": len(report.missing), "unexpected": len(report.unexpected), "dangerous": len(report.dangerous), "warehouse_problems": len(report.warehouse_problems)}
            if not report.ok:
                raise RunnerStop(report.code or "registration_not_verified")
            with tempfile.TemporaryDirectory() as tmp:
                local = build_local_app(Path(tmp), connector=connector)
                register(local, host=args.host, http_path=f"/sql/1.0/warehouses/{runner.warehouse_id}", catalog=args.catalog, schema=args.schema, client_id=principal.application_id,
                         client_secret=principal.secret, warehouse=args.warehouse_name)
                result = run_check(TestClient(local.app, raise_server_exceptions=False), local, wait_seconds=args.cap_minutes * 60)
    except RunnerStop as stop:
        stopped = stop.code
    except Exception as exc:  # noqa: BLE001 - the type only, never a message
        stopped = f"unexpected:{type(exc).__name__[:40]}"
    teardown = runner.teardown_result if runner is not None else {}
    report_body: dict[str, Any] = {"run_id": run_id, "mode": "provision", "stopped": stopped, "verify": verify, "teardown": teardown, **result}
    leaks_clean = True
    if runner is not None:
        runner.scanner.scan("report", json.dumps(report_body))
        runner.scanner.scan("ledger", Path(args.ledger).read_text() if Path(args.ledger).exists() else "")
        leaks_clean = runner.scanner.clean
    report_body["leaks_clean"] = leaks_clean
    ok = bool(result.get("wired_path_ok")) and not stopped and not teardown.get("left") and leaks_clean
    report_body["wired_path_ok"] = ok
    report_body["verdict"] = result.get("verdict", "did not run") if ok else f"NOT ok: stopped={stopped or 'no'}, left={teardown.get('left', 'n/a')}, leaks_clean={leaks_clean}"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"deployment-check-{run_id}.json").write_text(json.dumps(report_body, indent=1))
    print(f"report written to {out}/deployment-check-{run_id}.json; verdict: {report_body['verdict']}")
    if teardown.get("left"):
        print(f"WARNING: left behind: {teardown['left']}; run again with --sweep", file=sys.stderr)
    return 0 if ok else 1


def main(argv: list[str] | None = None, *, admin_factory: Callable[[str, str], Any] | None = None, connector: Any | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cap_minutes > MAX_CAP_MINUTES:
        print("refused: the cap is above the maximum", file=sys.stderr)
        return 2
    if args.sweep:
        return _sweep(args, admin_factory)
    if not args.execute:
        _dry_run(args)
        return 0
    if not (args.profile and args.approval_id and args.out):
        print("refused: --execute needs --profile, --approval-id and --out", file=sys.stderr)
        return 2
    if not args.provision:
        client_id, client_secret = os.environ.get(ID_VAR, ""), os.environ.get(SECRET_VAR, "")
        if not client_id or not client_secret:
            print(f"refused: set {ID_VAR} and {SECRET_VAR} in your own terminal first (they are never printed), or use --provision so this run creates and removes its own principal", file=sys.stderr)
            return 2
    try:
        check_approval(args.approval_id, args.host, args.catalog, args.schema, args.warehouse_name, args.cap_minutes,
                       also=PROVISION_NEEDLES if args.provision else ("warmup_deployment_check", *VIEWS), max_cap_minutes=MAX_CAP_MINUTES)
    except ApprovalError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if not _drivers_ok(connector):
        print("refused: the Databricks connector is not installed (pip install -r requirements-warehouses.txt)", file=sys.stderr)
        return 2
    if args.provision:
        return _provision(args, admin_factory, connector)
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
        local = build_local_app(Path(tmp), connector=connector)
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
