"""The REAL start-up (`app.main` lifespan) with Connect on Databricks enabled, in a fresh process: the glue that no other test runs. It checks what an installation actually serves: the public
address reaches the enrol routes, the drift monitor starts once per start-up and stops on shutdown (also on a second start-up in the same process), the Databricks routes are live, and
what the generic Connect route answers when only Databricks Connect is wired (503), because the Databricks tab must not depend on it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SERVICE = Path(__file__).resolve().parent.parent
SCRIPT = textwrap.dedent("""
    import json, os, shutil, tempfile, threading
    from pathlib import Path
    work = Path(tempfile.mkdtemp())
    from reference_stack import synthetic_views_contract as sv
    shutil.copytree(sv.CERTIFIED.parents[1], work / "tenants" / "synthetic")
    ENTRA = "00000000-0000-0000-0000-00000000c0de"
    os.environ.update({
        "ENV": "dev", "ANALYTICS_DB_URL": f"sqlite:///{work/'data.db'}", "ANALYTICS_CONTROL_DB_URL": f"sqlite:///{work/'control.db'}",
        "ANALYTICS_SEMANTIC_REGISTRY_PATH": str(sv.CERTIFIED.parents[1]), "ANALYTICS_V2_ENABLED": "true", "ANALYTICS_V2_CONTRACTS": f"{sv.CONTRACT_ID}@{sv.VERSION}",
        "ANALYTICS_CONNECT_DATABRICKS_ENABLED": "true", "ANALYTICS_CONNECT_REGISTRY_ROOT": str(work / "tenants"), "ANALYTICS_TENANT_MAP": '{"%s": "synthetic"}' % ENTRA,
        "ENTRA_TENANT_ID": ENTRA, "ENTRA_AUDIENCE": "api://x", "ANALYTICS_ROLE_MAP": '{"Analytics.Admin": [[], ["connection-manager"]]}',
        "ANALYTICS_CONNECT_PUBLIC_URL": os.environ["PUB"], "ANALYTICS_CONNECT_DRIFT_CHECK_HOURS": os.environ["HOURS"],
    })
    from fastapi.testclient import TestClient
    import app.main as m
    out = []
    for _ in range(2):  # two start-ups in one process
        with TestClient(m.app) as c:
            alive = [t for t in threading.enumerate() if t.name == "connect-drift-monitor"]
            out.append({"health": c.get("/health").status_code, "enrol_url": m.databricks_runtime.enrol_url, "monitors": len(m.drift_monitor), "threads": len(alive),
                        "interval": m.drift_monitor[0].read_check_interval, "databricks": c.get("/api/v2/connect/databricks/capabilities").status_code,
                        "enrol": c.post("/api/v2/connect/databricks/enrol", json={}).status_code, "rotate": c.post("/api/v2/connect/databricks/enrol/rotate", json={}).status_code,
                        "generic": c.get("/api/v2/connect/capabilities", headers={"Authorization": "Bearer x"}).status_code})
        out[-1]["threads_after"] = len([t for t in threading.enumerate() if t.name == "connect-drift-monitor"])
    print("RESULT " + json.dumps(out))
""")


def start(public, hours):
    env = {**os.environ, "PUB": public, "HOURS": str(hours), "PYTHONPATH": f"{SERVICE}:{SERVICE.parent.parent}"}
    done = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, cwd=SERVICE, env=env, timeout=180, check=False)
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")), "")
    assert line, done.stderr[-1500:]
    return json.loads(line[7:])


@pytest.fixture(scope="module")
def configured():
    return start("https://compass.example.com/", 24)


def test_a_real_start_up_serves_the_enrol_routes_and_the_address_from_the_setting(configured):
    for run in configured:
        assert run["health"] == 200 and run["enrol_url"] == "https://compass.example.com/api/v2/connect/databricks/enrol"
        assert (run["databricks"], run["enrol"], run["rotate"]) == (401, 401, 401)  # live and asking for credentials, not 503 and not 404


def test_the_drift_monitor_starts_with_the_app_stops_on_shutdown_and_takes_its_interval_from_the_setting(configured):
    first, second = configured
    assert first["monitors"] == 1 and first["threads"] == 1 and first["threads_after"] == 0 and first["interval"] == 24 * 3600
    # a second start-up in the same process skips the wiring (the v2 runtime is already built): no new monitor, and no stray thread left behind by either
    assert second["monitors"] == 1 and second["threads"] == 0 and second["threads_after"] == 0


def test_without_a_public_address_the_enrol_file_is_not_offered_and_the_read_check_defaults_off():
    run = start("", 0)[0]
    assert run["enrol_url"] == "" and run["interval"] == 0 and run["threads"] == 1  # the free expiry sweep still runs


def test_the_generic_connect_route_is_503_when_only_databricks_is_wired_so_the_databricks_tab_must_not_use_it(configured):
    assert all(run["generic"] == 503 for run in configured)
