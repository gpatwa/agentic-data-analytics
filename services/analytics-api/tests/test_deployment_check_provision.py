"""The self-provisioning deployment check (docs/design/SELF_SERVE_CONNECT_DESIGN.md, S1) with a FAKE admin client and a fake connector: nothing leaves the process and nothing here claims a live run.

The fake admin keeps real state (principals, secrets, grants, the warehouse list) so the tests can assert that a run leaves none of it behind."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from reference_stack.deployment_check import __main__ as cli
from reference_stack.deployment_check.runner import Ledger, RegistrationRunner, RunnerStop, pick_warehouse, run_sql
from test_deployment_check import FakeConnector, fake_sdk  # noqa: F401  (the fixture is used by name)

from app.connect.grants import ConnectScope

HOST = "dbc-1234abcd-5678.cloud.databricks.com"
SECRET = "dapi-fake-secret-that-must-never-appear-anywhere"
CEILING = ConnectScope("dev", (("compass_synthetic_food_delivery", "orders_current"), ("compass_synthetic_food_delivery", "orders_usd_gross")))
ARGS = ["--host", HOST, "--warehouse-name", "Serverless Starter Warehouse", "--profile", "p", "--approval-id", "a", "--out"]


class FakeAdmin:
    """Principals, secrets, grants and the warehouse ACL, with the SDK's shapes."""

    def __init__(self, *, deny_show_grants=False, fail_on=None, existing_principals=()):
        self.principals = {p: SimpleNamespace(id=f"id-{p}", application_id=f"app-{p}", display_name=p) for p in existing_principals}
        self.secrets, self.grants, self.acl, self.statements = {}, set(), set(), []
        self.deny_show_grants, self.fail_on = deny_show_grants, fail_on
        self.config = SimpleNamespace(host=f"https://{HOST}")
        self.service_principals = SimpleNamespace(create=self._create, list=lambda: list(self.principals.values()), delete=self._delete)
        self.service_principal_secrets_proxy = SimpleNamespace(create=self._secret, delete=lambda pid, sid: self.secrets.pop(sid, None))
        self.warehouses = SimpleNamespace(list=lambda: [SimpleNamespace(name="Serverless Starter Warehouse", id="wh1")], get_permissions=self._perms, update_permissions=self._update_perms)
        self.statement_execution = SimpleNamespace(execute_statement=self._execute)

    def _create(self, display_name):
        if self.fail_on == "create_principal":
            raise RuntimeError("boom")
        p = SimpleNamespace(id=f"id-{len(self.principals)}", application_id=f"app-{len(self.principals)}", display_name=display_name)
        self.principals[display_name] = p
        return p

    def _delete(self, pid):
        for name, p in list(self.principals.items()):
            if p.id == pid:
                del self.principals[name]
                self.acl = {a for a in self.acl if a != p.application_id}

    def _secret(self, service_principal_id, lifetime):
        sid = f"sec-{len(self.secrets)}"
        self.secrets[sid] = SECRET
        return SimpleNamespace(id=sid, secret=SECRET)

    def _perms(self, _wid):
        entries = [SimpleNamespace(service_principal_name=a, all_permissions=[SimpleNamespace(permission_level="CAN_USE")]) for a in sorted(self.acl)]
        return SimpleNamespace(access_control_list=entries)

    def _update_perms(self, _wid, access_control_list):
        for e in access_control_list:
            self.acl.add(e.service_principal_name)

    def _execute(self, statement, warehouse_id, wait_timeout):
        self.statements.append(statement)
        ok = SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="SUCCEEDED")))
        if self.fail_on and self.fail_on in statement:
            return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="FAILED")))
        m = re.match(r"GRANT (USE CATALOG|USE SCHEMA|SELECT) ON (CATALOG|SCHEMA|TABLE) (.+) TO `(.+)`$", statement)
        if m:
            self.grants.add((m[4], m[1], m[2], m[3].replace("`", "")))
        m = re.match(r"REVOKE (USE CATALOG|USE SCHEMA|SELECT) ON (CATALOG|SCHEMA|TABLE) (.+) FROM `(.+)`$", statement)
        if m:
            self.grants.discard((m[4], m[1], m[2], m[3].replace("`", "")))
        m = re.match(r"SHOW GRANTS `(.+)` ON (CATALOG|SCHEMA|TABLE) (.+)$", statement)
        if m:
            rows = [[g[0], g[1], g[2], g[3]] for g in sorted(self.grants) if g[0] == m[1] and g[2] == m[2] and g[3] == m[3].replace("`", "")]
            return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="SUCCEEDED")), result=SimpleNamespace(data_array=rows))
        if statement.startswith("SHOW GRANTS ON CATALOG"):
            if self.deny_show_grants:
                return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="FAILED")))
            return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="SUCCEEDED")), result=SimpleNamespace(data_array=[]))
        return ok

    def left_behind(self):
        return {"principals": sorted(self.principals), "secrets": sorted(self.secrets), "grants": sorted(self.grants), "acl": sorted(self.acl)}


def runner_for(admin, tmp_path, **kw):
    return RegistrationRunner(admin, ceiling=CEILING, warehouse_id="wh1", warehouse_name="Serverless Starter Warehouse", ledger=Ledger(tmp_path / "ledger.json"), suffix=kw.pop("suffix", "a1b2c3d4"), **kw)


def provision_main(tmp_path, admin, monkeypatch, *, extra=(), connector=None):
    monkeypatch.setattr(cli, "check_approval", lambda *a, **k: None)
    out = tmp_path / "out"
    code = cli.main([*ARGS, str(out), "--execute", "--provision", "--ledger", str(tmp_path / "ledger.json"), *extra], admin_factory=lambda p, h: admin, connector=connector or FakeConnector())
    return code, out


# ---- the runner ---------------------------------------------------------------------------------------------------------------


def test_the_runner_creates_what_the_product_script_says_verifies_it_and_removes_all_of_it(tmp_path):
    admin = FakeAdmin()
    runner = runner_for(admin, tmp_path)
    with runner.provision() as principal:
        assert principal.secret == SECRET and SECRET not in repr(principal)
        assert principal.principal == "compass-conn-a1b2c3d4"
        assert runner.verify().ok
        assert len(admin.grants) == 4 and admin.acl == {principal.application_id}  # catalog, schema, two tables; CAN_USE
        assert json.loads((tmp_path / "ledger.json").read_text())[0]["principal_id"]  # recorded for a sweep
    assert runner.teardown_result["left"] == [] and admin.left_behind() == {"principals": [], "secrets": [], "grants": [], "acl": []}
    assert Ledger(tmp_path / "ledger.json").entries() == []
    assert SECRET not in (tmp_path / "ledger.json").read_text()


def test_a_failure_during_setup_still_removes_what_was_created(tmp_path):
    admin = FakeAdmin(fail_on="GRANT SELECT")
    runner = runner_for(admin, tmp_path)
    with pytest.raises(RunnerStop):
        with runner.provision():
            pytest.fail("setup must not succeed")
    assert admin.left_behind() == {"principals": [], "secrets": [], "grants": [], "acl": []}
    assert Ledger(tmp_path / "ledger.json").entries() == []


def test_an_error_inside_the_run_still_tears_down(tmp_path):
    admin = FakeAdmin()
    runner = runner_for(admin, tmp_path)
    with pytest.raises(ValueError):
        with runner.provision():
            raise ValueError("the check blew up")
    assert admin.left_behind() == {"principals": [], "secrets": [], "grants": [], "acl": []}


def test_leftovers_in_the_ledger_stop_a_new_run_before_anything_is_created(tmp_path):
    admin = FakeAdmin()
    Ledger(tmp_path / "ledger.json").put({"run_id": "run-old", "suffix": "deadbeef"})
    with pytest.raises(RunnerStop) as stop:
        runner_for(admin, tmp_path).preflight()
    assert stop.value.code == "leftovers_found" and admin.statements == [] and admin.principals == {}


def test_a_taken_name_and_missing_rights_stop_the_run_before_anything_is_created(tmp_path):
    taken = FakeAdmin(existing_principals=("compass-conn-a1b2c3d4",))
    with pytest.raises(RunnerStop) as stop:
        runner_for(taken, tmp_path).preflight()
    assert stop.value.code == "name_taken"
    denied = FakeAdmin(deny_show_grants=True)
    with pytest.raises(RunnerStop) as stop:
        runner_for(denied, tmp_path).preflight()
    assert stop.value.code.startswith("cannot_read_grants") and denied.principals == {}


def test_a_crashed_run_is_swept_by_recorded_id_and_a_customers_principal_is_never_touched(tmp_path):
    admin = FakeAdmin(existing_principals=("compass-conn-cafef00d",))  # a real customer connection with our prefix
    crashed = runner_for(admin, tmp_path)
    crashed.preflight()
    crashed.setup()  # then the process dies: no teardown
    assert len(admin.principals) == 2 and Ledger(tmp_path / "ledger.json").entries()
    result = runner_for(admin, tmp_path, suffix="11111111").sweep()
    assert result["runs"] == 1 and result["left"] == []
    assert list(admin.principals) == ["compass-conn-cafef00d"] and admin.grants == set() and admin.acl == set() and admin.secrets == {}


def test_only_read_grants_naming_the_ceiling_catalog_can_be_sent():
    admin = FakeAdmin()
    for bad in ("DROP CATALOG `dev`", "GRANT ALL PRIVILEGES ON CATALOG `dev` TO `x`", "GRANT SELECT ON TABLE `dev`.`s`.`t` TO `x` WITH GRANT OPTION", "GRANT SELECT ON TABLE `other`.`s`.`t` TO `x`",
                "GRANT MANAGE ON CATALOG `dev` TO `x`"):
        with pytest.raises(RunnerStop):
            run_sql(admin, "wh1", "dev", bad, "t")
    assert admin.statements == []


def test_the_warehouse_is_chosen_by_name_only():
    admin = FakeAdmin()
    assert pick_warehouse(admin, "Serverless Starter Warehouse") == "wh1"
    with pytest.raises(RunnerStop):
        pick_warehouse(admin, "Another")


# ---- the command ------------------------------------------------------------------------------------------------------------------


def test_provision_runs_the_whole_check_with_no_environment_variables_and_leaves_nothing(tmp_path, monkeypatch, fake_sdk):  # noqa: F811
    monkeypatch.delenv(cli.ID_VAR, raising=False)
    monkeypatch.delenv(cli.SECRET_VAR, raising=False)
    admin = FakeAdmin()
    connector = FakeConnector()
    code, out = provision_main(tmp_path, admin, monkeypatch, connector=connector)
    report_file = next(out.glob("deployment-check-*.json"))
    report = json.loads(report_file.read_text())
    assert code == 0 and report["wired_path_ok"], report
    assert report["verify"]["ok"] and report["teardown"]["left"] == [] and report["leaks_clean"]
    assert admin.left_behind() == {"principals": [], "secrets": [], "grants": [], "acl": []}
    assert SECRET not in report_file.read_text() and SECRET not in "".join(q for c in connector.connections for q in c.sent)
    assert all(re.match(r"(GRANT|REVOKE) (USE CATALOG|USE SCHEMA|SELECT) |SHOW GRANTS ", s) for s in admin.statements)
    kwargs = [str(k) for k in connector.kwargs]
    assert kwargs  # the gateway connected through the connector with the principal's own credentials


def test_provision_tears_down_even_when_the_check_itself_fails(tmp_path, monkeypatch, fake_sdk):  # noqa: F811
    admin = FakeAdmin()
    monkeypatch.setattr("reference_stack.deployment_check.check.run_check", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    code, out = provision_main(tmp_path, admin, monkeypatch)
    report = json.loads(next(out.glob("deployment-check-*.json")).read_text())
    assert code == 1 and report["stopped"].startswith("unexpected") and not report["wired_path_ok"]
    assert admin.left_behind() == {"principals": [], "secrets": [], "grants": [], "acl": []}


def test_a_profile_for_another_workspace_is_refused_before_anything_is_created(tmp_path, monkeypatch, fake_sdk):  # noqa: F811
    admin = FakeAdmin()
    admin.config.host = "https://dbc-other.cloud.databricks.com"
    code, out = provision_main(tmp_path, admin, monkeypatch)
    report = json.loads(next(out.glob("deployment-check-*.json")).read_text())
    assert code == 1 and report["stopped"] == "profile_host_mismatch" and admin.statements == [] and admin.principals == {}


def test_the_sweep_command_removes_a_crashed_runs_objects(tmp_path, capsys):
    admin = FakeAdmin()
    crashed = runner_for(admin, tmp_path)
    crashed.preflight()
    crashed.setup()
    code = cli.main(["--host", HOST, "--warehouse-name", "Serverless Starter Warehouse", "--profile", "p", "--sweep", "--ledger", str(tmp_path / "ledger.json")], admin_factory=lambda p, h: admin)
    assert code == 0 and "1 recorded run" in capsys.readouterr().out and admin.left_behind() == {"principals": [], "secrets": [], "grants": [], "acl": []}


def test_the_dry_run_with_provision_names_what_it_would_create_and_makes_no_call(capsys):
    assert cli.main(["--host", HOST, "--warehouse-name", "W", "--provision"], admin_factory=lambda p, h: pytest.fail("no call in a dry run")) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "--provision" in out and "no environment variables" in out and "removed" in out


@pytest.mark.parametrize("extra", [[], ["--profile", "p"], ["--profile", "p", "--approval-id", "a"]])
def test_provision_refuses_without_its_inputs(extra, capsys):
    assert cli.main(["--host", HOST, "--warehouse-name", "W", "--execute", "--provision", *extra]) == 2


def test_provision_refuses_without_a_recorded_approval(tmp_path, capsys):
    code = cli.main(["--host", HOST, "--warehouse-name", "W", "--profile", "p", "--approval-id", "warmup_provision_check", "--out", str(tmp_path), "--execute", "--provision"],
                    admin_factory=lambda p, h: pytest.fail("must not reach the workspace"))
    assert code == 2 and "approval_not_recorded" in capsys.readouterr().err
