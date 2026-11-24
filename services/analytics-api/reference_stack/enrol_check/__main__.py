"""The live check of the enrol file. Dry run by default: prints the plan and makes no call. `--execute` needs the owner's recorded approval (never inferred), the owner's signed-in
Databricks profile, and installed drivers. See check.py for what it does; `--sweep` removes what a crashed run recorded, by exact principal name."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from reference_stack.deployment_check.runner import DEFAULT_LEDGER, Ledger, RunnerStop, pick_warehouse
from reference_stack.loaders.approval import ApprovalError, check_approval

MAX_CAP_MINUTES = 20.0
NEEDLES = ("enrol_file_live_check", "orders_current", "orders_usd_gross")
STEPS = (
    "begin a setup for the two views and ask for the enrol file (a local instance of the app, served on localhost)",
    "RUN the enrol file under the owner's profile: it creates ONE service principal, its secret (held in memory), CAN_USE on the warehouse and read grants on the two views, posts them to the local app, and removes everything if anything fails",
    "the local app verifies the read-back and reads both views as the new principal (OAuth M2M); the status says connected",
    "the warm-up path on that connection: switch off, switch on, 202 warming, recorded warmed, skipped_recent",
    "ROTATE with the rotate file: a new secret, accepted only after both views read, the older secrets deleted; the health check passes",
    "REMOVE: the connection, then the enrol file's own --remove deletes the principal and its grants; the check lists the workspace and confirms it is gone",
    "in a `finally`: whatever happened, the principal with this run's EXACT name is removed by id (never a prefix), and the ledger entry dropped",
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reference_stack.enrol_check", description=__doc__)
    p.add_argument("--host", required=True)
    p.add_argument("--catalog", default="dev")
    p.add_argument("--schema", default="compass_synthetic_food_delivery")
    p.add_argument("--warehouse-name", required=True)
    p.add_argument("--cap-minutes", type=float, default=20.0)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--sweep", action="store_true", help="remove what a crashed run recorded in the ledger, by exact principal name, then stop")
    p.add_argument("--profile", help="the owner's databricks CLI profile; the enrol file and the rotate file run under it")
    p.add_argument("--approval-id")
    p.add_argument("--out")
    p.add_argument("--ledger", default=str(DEFAULT_LEDGER.with_name("enrol-check-ledger.json")))
    return p


def real_serve(app: Any) -> tuple[Any, str, Callable[[], None]]:
    """The app on a free localhost port in a thread, and a client for it."""
    import httpx
    import uvicorn

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RunnerStop("server_did_not_start")
    base = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base, timeout=120)

    def stop() -> None:
        client.close()
        server.should_exit = True
        thread.join(timeout=10)

    return client, base, stop


def real_run_file(path: Path, args: list[str]) -> tuple[int, str]:
    """The generated file, as the admin would run it: a separate Python process (no secret is passed to it; it uses the owner's profile)."""
    done = subprocess.run([sys.executable, "-I", str(path), *args], capture_output=True, text=True, timeout=600, check=False)  # noqa: S603
    return done.returncode, done.stdout


def _sdk_admin(profile: str, host: str) -> Any:
    import databricks.sdk as sdk

    return sdk.WorkspaceClient(profile=profile)


def _drivers_ok() -> bool:
    try:
        import databricks.sdk.core  # noqa: F401
        import databricks.sql  # noqa: F401
        import httpx  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        return False
    return True


def main(argv: list[str] | None = None, *, admin_factory: Callable[[str, str], Any] | None = None, serve: Callable[..., Any] | None = None, run_file: Callable[..., Any] | None = None,
         connector: Any | None = None, program: Path | None = None, dns: Callable[[str], list[str]] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cap_minutes > MAX_CAP_MINUTES:
        print("refused: the cap is above the maximum", file=sys.stderr)
        return 2
    if args.sweep:
        return _sweep(args, admin_factory)
    if not args.execute:
        print(f"DRY RUN: nothing was executed and no network call was made. Target {args.host}, {args.catalog}.{args.schema}, warehouse {args.warehouse_name!r}; wall-clock cap {args.cap_minutes:g} min")
        print(f"the connection may read only {args.catalog}.{args.schema}.orders_current and .orders_usd_gross")
        for i, step in enumerate(STEPS, 1):
            print(f"  {i}. {step}")
        print("no model is called, nothing is deployed, no Azure object and no warehouse is created, resized, stopped or deleted; the one principal above is the only thing created and it is removed")
        return 0
    if not (args.profile and args.approval_id and args.out):
        print("refused: --execute needs --profile, --approval-id and --out", file=sys.stderr)
        return 2
    try:
        extra = {"program": program} if program else {}
        check_approval(args.approval_id, args.host, args.catalog, args.schema, args.warehouse_name, args.cap_minutes, also=NEEDLES, max_cap_minutes=MAX_CAP_MINUTES, **extra)
    except ApprovalError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if connector is None and not _drivers_ok():
        print("refused: the Databricks connector, SDK, httpx or uvicorn is not installed (pip install -r requirements-warehouses.txt)", file=sys.stderr)
        return 2
    from reference_stack.deployment_check.check import build_local_app
    from reference_stack.enrol_check.check import run_enrol_check

    run_id = uuid.uuid4().hex[:8]
    ledger = Ledger(Path(args.ledger))
    if ledger.entries():
        print("refused: the ledger holds a crashed run; run with --sweep first", file=sys.stderr)
        return 2
    try:
        admin = (admin_factory or _sdk_admin)(args.profile, args.host)
        if args.host not in str(getattr(getattr(admin, "config", None), "host", "")):
            raise RunnerStop("profile_host_mismatch")
        warehouse_id = pick_warehouse(admin, args.warehouse_name)
    except RunnerStop as stop:
        print(f"STOPPED: {stop.code}", file=sys.stderr)
        return 3
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        local = build_local_app(work / "app", connector=connector, enrol_url="http://127.0.0.1:0")  # the real port is only known once served: patched below
        (work / "files").mkdir()
        if dns is not None:  # tests only: the real run resolves the real workspace host
            local.dbx.dns = dns

        def serve_and_set(app: Any):
            client, base, stop = (serve or real_serve)(app)
            _set_enrol_url(local, base)
            return client, base, stop

        result = run_enrol_check(host=args.host, catalog=args.catalog, schema=args.schema, warehouse_name=args.warehouse_name, profile=args.profile, admin=admin, local=local,
                                 serve=serve_and_set, run_file=run_file or real_run_file, work=work / "files", ledger=ledger, warehouse_id=warehouse_id, wait_seconds=args.cap_minutes * 60)
    path = out / f"enrol-check-{run_id}.json"
    path.write_text(json.dumps({"run_id": run_id, **result}, indent=1))
    print(f"report written to {path}; verdict: {result['verdict']}")
    return 0 if result["ok"] else 1


def _set_enrol_url(local: Any, base: str) -> None:
    local.dbx.enrol_url = f"{base}/api/v2/connect/databricks/enrol"


def _sweep(args: argparse.Namespace, admin_factory: Callable[[str, str], Any] | None) -> int:
    from reference_stack.enrol_check.check import remove_by_exact_name

    if not args.profile:
        print("refused: --sweep needs --profile", file=sys.stderr)
        return 2
    ledger = Ledger(Path(args.ledger))
    admin = (admin_factory or _sdk_admin)(args.profile, args.host)
    if args.host not in str(getattr(getattr(admin, "config", None), "host", "")):
        print("STOPPED: profile_host_mismatch", file=sys.stderr)
        return 3
    left: list[str] = []
    entries = ledger.entries()
    for entry in entries:
        result = remove_by_exact_name(admin, name=entry["principal_name"], catalog=entry["catalog"], schema=entry["tables"][0][0], warehouse_id=entry["warehouse_id"],
                                      warehouse_name=entry["warehouse"], ledger=ledger, run_id=entry["run_id"])
        left += result["left"]
    print(f"sweep: {len(entries)} recorded run(s), left: {left or 'nothing'}")
    return 0 if not left else 1


if __name__ == "__main__":
    raise SystemExit(main())
