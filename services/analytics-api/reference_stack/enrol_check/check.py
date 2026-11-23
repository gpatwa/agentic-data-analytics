"""The steps of the enrol-file check: the customer path, end to end, with the product's own generated files (docs/design/SELF_SERVE_CONNECT_DESIGN.md, S5).

A local instance of the real app (real routes, Connect wiring, Entra verifier with a locally generated key) is served over HTTP. As the admin it asks for the enrol file, RUNS THAT FILE
under the owner's signed-in profile (the file creates the real read-only principal, its secret and the grants, and posts them to the local app), checks the connection through the real
Databricks session, runs the warm-up path on it, rotates the secret with the rotate file, then removes the principal with the enrol file's own `--remove` and checks it is gone.

Nothing here reads a credential: the principal's secret travels from the file to the app's custody, and this module only asks the custody for it to watch for leaks. A crash or a failed
step still runs a cleanup that removes the principal by its EXACT name (never a prefix), so nothing is left in the workspace. Tested here with fakes only."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.connect.e2e.leak import LeakScanner
from app.connect.grants import ConnectScope
from reference_stack.deployment_check.check import TENANT, LocalApp, run_check
from reference_stack.deployment_check.runner import Ledger, RegistrationRunner, RunnerStop

VIEWS = ("orders_current", "orders_usd_gross")
APP_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
API = "/api/v2/connect/databricks"

# serve(app) -> (client, base_url, stop); run_file(path, args) -> (exit code, printed text)
Serve = Callable[[Any], tuple[Any, str, Callable[[], None]]]
RunFile = Callable[[Path, list[str]], tuple[int, str]]


def principal_gone(admin: Any, name: str) -> bool:
    return not any(getattr(p, "display_name", None) == name for p in admin.service_principals.list())


def remove_by_exact_name(admin: Any, *, name: str, catalog: str, schema: str, warehouse_id: str, warehouse_name: str, ledger: Ledger, run_id: str) -> dict[str, Any]:
    """Remove the principal with exactly this name and its grants, by id. Never matches by prefix, so a customer's own `compass-conn-*` principal is never touched."""
    found = [p for p in admin.service_principals.list() if getattr(p, "display_name", None) == name]
    if not found:
        ledger.drop(run_id)
        return {"removed": 0, "left": []}
    ceiling = ConnectScope(catalog, tuple((schema, v) for v in VIEWS))
    runner = RegistrationRunner(admin, ceiling=ceiling, warehouse_id=warehouse_id, warehouse_name=warehouse_name, ledger=ledger, suffix=name[-8:])
    entry = {"run_id": run_id, "suffix": name[-8:], "principal_id": str(found[0].id), "application_id": str(found[0].application_id), "secret_id": "", "catalog": catalog,
             "tables": [[schema, v] for v in VIEWS], "warehouse_id": warehouse_id, "warehouse": warehouse_name}
    return runner.remove_recorded(entry)


def run_enrol_check(*, host: str, catalog: str, schema: str, warehouse_name: str, profile: str, admin: Any, local: LocalApp, serve: Serve, run_file: RunFile, work: Path, ledger: Ledger,
                    warehouse_id: str, wait_seconds: float = 90.0, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    scanner = LeakScanner()
    steps: dict[str, Any] = {}
    outputs: list[str] = []
    headers = {"Authorization": f"Bearer {local.token}"}
    client, _base, stop = serve(local.app)
    name = connection = run_id = ""
    verdict_ok = False
    try:
        r = client.post(f"{API}/setups", headers=headers, json={"host": host, "catalog": catalog, "warehouse_name": warehouse_name, "tables": [{"schema": schema, "table": v} for v in VIEWS]})
        steps["begin"] = {"status": r.status_code}
        view = r.json() if r.status_code == 201 else {}
        if not view:
            raise RunnerStop("begin_refused")
        name, connection = view["principal_name"], view["connection_id"]
        run_id = f"enrol-{name[-8:]}"
        ledger.put({"run_id": run_id, "suffix": name[-8:], "principal_name": name, "catalog": catalog, "tables": [[schema, v] for v in VIEWS], "warehouse": warehouse_name, "warehouse_id": warehouse_id,
                    "application_id": "", "principal_id": "", "secret_id": ""})  # recorded BEFORE the file runs, so a crash can be swept by exact name
        r = client.post(f"{API}/setups/{view['setup_id']}/enrol-file", headers=headers)
        steps["issue_file"] = {"status": r.status_code}
        if r.status_code != 200:
            raise RunnerStop("issue_file_refused")
        file = r.json()
        enrol_path = work / file["filename"]
        enrol_path.write_text(file["content"])

        code, out = run_file(enrol_path, ["--profile", profile])
        outputs.append(out)
        found = APP_ID.search(out)
        steps["run_file"] = {"exit_code": code, "enrolled": "enrolled:" in out and code == 0, "printed_application_id": bool(found)}
        status = client.get(f"{API}/setups/{view['setup_id']}/status", headers=headers).json()
        steps["status"] = {"state": status.get("state"), "tables_read": [c["table"] for c in status.get("checks", []) if c["passed"]]}
        listed = [c for c in client.get(f"{API}/connections", headers=headers).json() if c["connection_id"] == connection]
        steps["connection"] = {"status": listed[0]["status"] if listed else "missing", "has_application_id": bool(listed and listed[0]["application_id"])}
        if not (steps["run_file"]["enrolled"] and status.get("state") == "connected" and steps["connection"]["status"] == "active"):
            raise RunnerStop("not_enrolled")
        record_name = f"databricks/{connection}"
        before = local.wiring.setups.secrets.read(TENANT, record_name)
        scanner.watch("principal_secret", before)

        steps["warm_path"] = run_check(client, local, wait_seconds=wait_seconds, sleep=sleep, connection=connection)

        r = client.post(f"{API}/connections/{connection}/rotate-file", headers=headers)
        steps["rotate_issue"] = {"status": r.status_code}
        if r.status_code != 200:
            raise RunnerStop("rotate_file_refused")
        rotate_path = work / r.json()["filename"]
        rotate_path.write_text(r.json()["content"])
        code, out = run_file(rotate_path, ["--profile", profile])
        outputs.append(out)
        after = local.wiring.setups.secrets.read(TENANT, record_name)
        scanner.watch("principal_secret_after_rotation", after)
        rotated = client.get(f"{API}/connections/{connection}/rotate-status", headers=headers).json()
        health = client.post(f"{API}/connections/{connection}/check", headers=headers).json()
        steps["rotate"] = {"exit_code": code, "state": rotated.get("state"), "secret_changed": after != before, "health_ok": bool(health.get("ok")), "status_after": health.get("connection_status")}

        steps["remove_compass_side"] = {"status": client.delete(f"{API}/connections/{connection}", headers=headers).status_code}
        code, out = run_file(enrol_path, ["--profile", profile, "--remove", found.group(0) if found else ""])
        outputs.append(out)
        steps["remove_file"] = {"exit_code": code, "principal_gone": principal_gone(admin, name)}
        verdict_ok = (steps["warm_path"]["wired_path_ok"] and steps["rotate"]["state"] == "connected" and steps["rotate"]["secret_changed"] and steps["rotate"]["health_ok"]
                      and steps["remove_file"]["principal_gone"] and steps["remove_compass_side"]["status"] == 200)
    except RunnerStop as stop_:
        steps["stopped"] = stop_.code
    except Exception as exc:  # noqa: BLE001 - the type only, never a message
        steps["stopped"] = f"unexpected:{type(exc).__name__[:40]}"
    finally:
        try:
            stop()
        except Exception:  # noqa: BLE001
            pass
        cleanup = {"removed": 0, "left": []}
        if name:
            try:
                cleanup = remove_by_exact_name(admin, name=name, catalog=catalog, schema=schema, warehouse_id=warehouse_id, warehouse_name=warehouse_name, ledger=ledger, run_id=run_id)
            except Exception as exc:  # noqa: BLE001
                cleanup = {"removed": 0, "left": [f"cleanup_failed:{type(exc).__name__[:40]}"]}
            cleanup["principal_gone"] = principal_gone(admin, name)
    for i, text in enumerate(outputs):
        scanner.scan(f"output_{i}", text)
    result: dict[str, Any] = {"steps": steps, "cleanup": cleanup, "principal": name, "connection": connection}
    scanner.scan("report", json.dumps(result))
    ok = verdict_ok and not steps.get("stopped") and cleanup.get("principal_gone", True) and not cleanup["left"] and scanner.clean
    result.update({"leaks_clean": scanner.clean, "ok": bool(ok),
                   "verdict": "the enrol file path worked end to end and left nothing behind" if ok else "NOT ok: read the steps and the cleanup"})
    return result
