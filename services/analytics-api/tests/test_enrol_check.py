"""The live check of the enrol file (S5), with FAKES: a fake admin client, a fake Databricks connector, and the generated files' own code run in-process against the real routes.
Nothing here claims a run against a workspace; it shows the harness does what it says, cleans up on every path, and refuses what it must."""

from __future__ import annotations

import io
import json
import runpy
import sys
import types
import urllib.error
import urllib.parse
import urllib.request

import pytest
import yaml
from fastapi.testclient import TestClient
from reference_stack.deployment_check.runner import Ledger
from reference_stack.enrol_check.__main__ import main
from test_connect_rotation_drift import RotatingAdmin
from test_deployment_check import FakeConnector, fake_sdk  # noqa: F401  (the fixture is used by name)
from test_deployment_check_provision import SECRET

HOST = "dbc-1234abcd-5678.cloud.databricks.com"
BASE = ["--host", HOST, "--warehouse-name", "Serverless Starter Warehouse"]


class Harness:
    """`serve` and `run_file` for the check, sharing one in-process client."""

    def __init__(self, admin, *, crash_after_file=False):
        self.admin, self.client, self.crash_after_file, self.runs = admin, None, crash_after_file, 0

    def serve(self, app):
        self.client = TestClient(app)
        return self.client, "http://localhost:8000", lambda: None

    def run_file(self, path, args, monkeypatch):
        sdk = types.ModuleType("databricks.sdk")
        sdk.WorkspaceClient = lambda **kw: self.admin
        monkeypatch.setitem(sys.modules, "databricks.sdk", sdk)

        class Response:
            def __init__(self, r):
                self.r = r

            def read(self):
                return self.r.content

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(request, timeout=0):
            r = self.client.post(urllib.parse.urlparse(request.full_url).path, content=request.data, headers={k: v for k, v in request.header_items()})
            if r.status_code >= 400:
                raise urllib.error.HTTPError(request.full_url, r.status_code, "x", {}, io.BytesIO(r.content))
            return Response(r)

        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(sys, "argv", [str(path), *args])
        out = io.StringIO()
        code = 0
        real_stdout = sys.stdout
        sys.stdout = out
        try:
            runpy.run_path(str(path), run_name="__main__")
        except SystemExit as exit_:
            code = exit_.code or 0
        finally:
            sys.stdout = real_stdout
        self.runs += 1
        if self.crash_after_file and self.runs == 1:
            raise RuntimeError("the machine went away after the file ran")
        return code, out.getvalue()


@pytest.fixture
def program(tmp_path):
    scope = (f"Run the enrol_file_live_check against https://{HOST}, dev.compass_synthetic_food_delivery (views orders_current and orders_usd_gross), warehouse Serverless Starter Warehouse")
    path = tmp_path / "program.yaml"
    path.write_text(yaml.safe_dump({"gates": {"enrol_file_live_check": {"status": "approved", "approved_by": "user", "scope": scope}}}))
    return path


def go(tmp_path, program, monkeypatch, admin, harness=None, *, connector=None, extra=()):
    harness = harness or Harness(admin)
    code = main([*BASE, "--profile", "p", "--approval-id", "enrol_file_live_check", "--out", str(tmp_path / "out"), "--execute", "--ledger", str(tmp_path / "ledger.json"), *extra],
                admin_factory=lambda p, h: admin, serve=harness.serve, run_file=lambda path, args: harness.run_file(path, args, monkeypatch), connector=connector or FakeConnector(), program=program, dns=lambda h: ["34.1.2.3"])
    reports = list((tmp_path / "out").glob("enrol-check-*.json"))
    return code, (json.loads(reports[0].read_text()) if reports else None), harness


GONE = {"principals": [], "secrets": [], "grants": [], "acl": []}


def test_the_whole_customer_path_runs_and_leaves_nothing_behind(tmp_path, program, monkeypatch, fake_sdk):  # noqa: F811
    admin = RotatingAdmin()
    code, report, _ = go(tmp_path, program, monkeypatch, admin)
    assert code == 0 and report["ok"], report
    steps = report["steps"]
    assert steps["run_file"]["enrolled"] and steps["status"]["state"] == "connected" and len(steps["status"]["tables_read"]) == 2 and steps["connection"]["status"] == "active"
    assert steps["warm_path"]["wired_path_ok"] and steps["rotate"]["state"] == "connected" and steps["rotate"]["secret_changed"] and steps["rotate"]["health_ok"]
    assert steps["remove_file"]["principal_gone"] and report["cleanup"]["left"] == [] and report["leaks_clean"]
    assert admin.left_behind() == GONE and Ledger(tmp_path / "ledger.json").entries() == []
    assert SECRET not in json.dumps(report)


def test_a_crash_after_the_file_ran_still_removes_the_principal_by_its_exact_name(tmp_path, program, monkeypatch, fake_sdk):  # noqa: F811
    admin = RotatingAdmin(existing_principals=("compass-conn-cafef00d",))  # a customer's own principal with our prefix
    code, report, _ = go(tmp_path, program, monkeypatch, admin, Harness(admin, crash_after_file=True))
    assert code == 1 and not report["ok"] and report["steps"]["stopped"].startswith("unexpected:RuntimeError")
    assert report["cleanup"]["principal_gone"] and report["cleanup"]["left"] == []
    assert list(admin.principals) == ["compass-conn-cafef00d"] and admin.grants == set() and admin.secrets == {} and admin.acl == set()
    assert Ledger(tmp_path / "ledger.json").entries() == []


def test_a_connection_test_that_fails_makes_the_file_remove_what_it_created(tmp_path, program, monkeypatch, fake_sdk):  # noqa: F811
    class Failing(FakeConnector):
        def connect(self, **kwargs):
            raise RuntimeError("the principal cannot read")

    admin = RotatingAdmin()
    code, report, _ = go(tmp_path, program, monkeypatch, admin, connector=Failing())
    assert code == 1 and report["steps"]["stopped"] == "not_enrolled" and report["steps"]["status"]["state"] == "refused"
    assert admin.left_behind() == GONE and report["cleanup"]["left"] == []


def test_a_sweep_removes_a_crashed_runs_principal_by_exact_name_only(tmp_path, program, monkeypatch, fake_sdk, capsys):  # noqa: F811
    admin = RotatingAdmin(existing_principals=("compass-conn-cafef00d",))
    mine = admin._create("compass-conn-0a1b2c3d")
    Ledger(tmp_path / "ledger.json").put({"run_id": "enrol-0a1b2c3d", "suffix": "0a1b2c3d", "principal_name": "compass-conn-0a1b2c3d", "catalog": "dev",
                                          "tables": [["compass_synthetic_food_delivery", "orders_current"], ["compass_synthetic_food_delivery", "orders_usd_gross"]],
                                          "warehouse": "Serverless Starter Warehouse", "warehouse_id": "abcdef0123456789", "application_id": "", "principal_id": "", "secret_id": ""})
    assert mine.display_name in admin.principals
    code = main([*BASE, "--profile", "p", "--sweep", "--ledger", str(tmp_path / "ledger.json")], admin_factory=lambda p, h: admin)
    assert code == 0 and "1 recorded run" in capsys.readouterr().out
    assert list(admin.principals) == ["compass-conn-cafef00d"] and Ledger(tmp_path / "ledger.json").entries() == []


def test_the_dry_run_makes_no_call_and_lists_the_steps(capsys):
    assert main(BASE, admin_factory=lambda p, h: pytest.fail("no call in a dry run")) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "RUN the enrol file" in out and "ROTATE" in out and "EXACT name" in out and "no model is called" in out


@pytest.mark.parametrize("extra", [[], ["--profile", "p"], ["--profile", "p", "--approval-id", "enrol_file_live_check"]])
def test_execute_refuses_without_its_inputs(extra):
    assert main([*BASE, "--execute", *extra], admin_factory=lambda p, h: pytest.fail("must not reach the workspace")) == 2


def test_execute_refuses_without_a_recorded_approval_a_leftover_ledger_or_the_wrong_workspace(tmp_path, program, monkeypatch, capsys, fake_sdk):  # noqa: F811
    common = [*BASE, "--profile", "p", "--out", str(tmp_path / "o"), "--execute", "--ledger", str(tmp_path / "ledger.json")]
    assert main([*common, "--approval-id", "no_such_approval"], admin_factory=lambda p, h: pytest.fail("no"), program=program, connector=FakeConnector()) == 2
    Ledger(tmp_path / "ledger.json").put({"run_id": "old"})
    assert main([*common, "--approval-id", "enrol_file_live_check"], admin_factory=lambda p, h: pytest.fail("no"), program=program, connector=FakeConnector()) == 2
    assert "--sweep" in capsys.readouterr().err
    Ledger(tmp_path / "ledger.json").drop("old")
    admin = RotatingAdmin()
    admin.config.host = "https://dbc-other.cloud.databricks.com"
    assert main([*common, "--approval-id", "enrol_file_live_check"], admin_factory=lambda p, h: admin, program=program, connector=FakeConnector()) == 3
    assert admin.statements == [] and admin.principals == {}


def test_a_cap_above_the_maximum_is_refused():
    assert main([*BASE, "--cap-minutes", "21"]) == 2


def test_the_real_server_and_the_real_file_runner_work_on_localhost(tmp_path):
    from fastapi import FastAPI
    from reference_stack.enrol_check.__main__ import real_run_file, real_serve

    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    client, base, stop = real_serve(app)
    try:
        assert base.startswith("http://127.0.0.1:") and client.get("/ping").json() == {"ok": True}
    finally:
        stop()
    script = tmp_path / "f.py"
    script.write_text("import sys\nprint('args:', ' '.join(sys.argv[1:]))\nraise SystemExit(3)\n")
    assert real_run_file(script, ["--profile", "p"]) == (3, "args: --profile p\n")
