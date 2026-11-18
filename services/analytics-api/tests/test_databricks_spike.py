"""The Databricks spike driver against a fake workspace that keeps state: each outcome of Q1 to Q7 (allowed and denied), the stop
rules, the created-only teardown and the sweep. Nothing here touches a workspace."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.connect.e2e import databricks_spike as ds
from app.connect.e2e.databricks_spike import Names, Spike, SpikeStop

APP = Path(__file__).resolve().parents[1] / "app"
SUFFIX = "0a1b2c3d"
N = Names.for_run(SUFFIX)


class PermissionDenied(Exception):
    pass


class World:
    """A workspace as it might behave. `flags` say what the delegated setup principal `S` may do; the defaults are the plan's expectations."""

    def __init__(self, **flags):
        self.flags = {"s_create_sp": False, "s_secret_foreign": False, "s_grant_inside": True, "s_grant_outside": False, "s_grant_path": False,
                      "s_acl": False, "s_sees_decoy": True, "s_delete": False, "t_reads_outside": False, "t_writes": False,
                      "fail_admin_sql": None, "existing_catalog": None, "existing_principal": None, **flags}
        self.principals, self.secrets, self.catalogs, self.grants, self.acl, self.statements = {}, {}, set(), set(), set(), []
        self.n = 0
        if self.flags["existing_catalog"]:
            self.catalogs.add(self.flags["existing_catalog"])
        if self.flags["existing_principal"]:
            self._new_principal(self.flags["existing_principal"], "owner")

    def _new_principal(self, name, creator):
        self.n += 1
        pid, app = f"pid{self.n}", f"app-{self.n:04d}"
        self.principals[pid] = {"display_name": name, "application_id": app, "creator": creator}
        return SimpleNamespace(id=pid, application_id=app, display_name=name)

    def role(self, app):
        name = next((p["display_name"] for p in self.principals.values() if p["application_id"] == app), "")
        return "S" if "-setup-" in name else "T"

    # ---- clients
    def client(self, actor):
        w = self
        return SimpleNamespace(
            statement_execution=SimpleNamespace(execute_statement=lambda statement, warehouse_id, wait_timeout: w._sql(actor, statement)),
            service_principals=SimpleNamespace(create=lambda display_name: w._create(actor, display_name), list=lambda: w._list(actor),
                                               delete=lambda i: w._delete(actor, i)),
            service_principal_secrets_proxy=SimpleNamespace(create=lambda service_principal_id, lifetime: w._secret(actor, service_principal_id),
                                                            delete=lambda pid, sid: w._del_secret(actor, sid)),
            warehouses=SimpleNamespace(update_permissions=lambda wid, access_control_list: w._acl(actor, access_control_list),
                                       get_permissions=lambda wid: SimpleNamespace(access_control_list=[SimpleNamespace(service_principal_name=a) for a in w.acl]),
                                       list=lambda: [SimpleNamespace(id="wh1", name="Serverless Starter Warehouse")]),
        )

    def connect(self, client_id, secret):
        assert secret in self.secret_values.values()  # only a secret the workspace issued signs in
        return self.client(self.role(client_id) + ":" + client_id)

    secret_values: dict = {}

    def _is_admin(self, actor):
        return actor == "admin"

    def _create(self, actor, name):
        if not self._is_admin(actor) and not self.flags["s_create_sp"]:
            raise PermissionDenied("workspace admin required")
        return self._new_principal(name, actor)

    def _list(self, actor):
        hide = not self._is_admin(actor) and not self.flags["s_sees_decoy"]
        return iter([SimpleNamespace(id=i, display_name=p["display_name"], application_id=p["application_id"]) for i, p in self.principals.items()
                     if not (hide and p["creator"] == "admin" and "conn2" in p["display_name"])])

    def _delete(self, actor, pid):
        if pid not in self.principals:
            raise KeyError("does not exist")
        if not self._is_admin(actor) and not (self.flags["s_delete"] and self.principals[pid]["creator"] == actor):
            raise PermissionDenied("not allowed")
        app = self.principals.pop(pid)["application_id"]
        self.acl.discard(app)
        self.grants = {g for g in self.grants if g[0] != app}

    def _secret(self, actor, pid):
        mine = self.principals[pid]["creator"] == actor
        if not self._is_admin(actor) and not (mine or self.flags["s_secret_foreign"]):
            raise PermissionDenied("service principal manager role required")
        self.n += 1
        sid, value = f"sec{self.n}", f"oauth-secret-{self.n}-do-not-leak"
        self.secrets[sid] = pid
        World.secret_values[sid] = value
        return SimpleNamespace(id=sid, secret=value)

    def _del_secret(self, actor, sid):
        if sid not in self.secrets:
            raise KeyError("does not exist")
        del self.secrets[sid]

    def _acl(self, actor, entries):
        if not self._is_admin(actor) and not self.flags["s_acl"]:
            raise PermissionDenied("CAN_MANAGE required")
        for e in entries:
            self.acl.add(e.service_principal_name)

    # ---- SQL
    def _ok(self, rows=()):
        return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="SUCCEEDED")), result=SimpleNamespace(data_array=[list(r) for r in rows]))

    def _fail(self, text):
        return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="FAILED"), error=SimpleNamespace(error_code="BAD_REQUEST", message=text)), result=None)

    def _sql(self, actor, statement):
        self.statements.append((actor, statement))
        admin = self._is_admin(actor)
        if statement == "SHOW CATALOGS":
            return self._ok([[c] for c in sorted(self.catalogs)])
        if self.flags["fail_admin_sql"] and admin and self.flags["fail_admin_sql"] in statement:
            return self._fail("internal error")
        if statement.startswith("CREATE CATALOG"):
            self.catalogs.add(re.search(r"`([^`]+)`", statement)[1])
            return self._ok()
        if statement.startswith("DROP CATALOG"):
            if not admin:
                return self._fail("PERMISSION_DENIED")
            self.catalogs.discard(re.search(r"`([^`]+)`", statement)[1])
            return self._ok()
        if statement.startswith(("CREATE SCHEMA", "CREATE TABLE", "INSERT INTO")):
            if not admin and not (statement.startswith("INSERT") and self.flags["t_writes"]) and not (statement.startswith("CREATE TABLE") and self.flags["t_writes"]):
                return self._fail("PERMISSION_DENIED: user does not have CREATE/MODIFY")
            return self._ok()
        grant = re.match(r"(GRANT|REVOKE) (SELECT|USE CATALOG|USE SCHEMA|MANAGE) ON (TABLE|SCHEMA|CATALOG) (.+?) (?:TO|FROM) `([^`]+)`$", statement)
        if grant:
            verb, priv, kind, obj, app = grant.groups()
            if not admin:
                inside = any(obj.endswith(f"`{t}`") for t in ds.INSIDE) and kind == "TABLE"
                allowed = (priv == "SELECT" and inside and self.flags["s_grant_inside"]) or \
                          (priv == "SELECT" and not inside and self.flags["s_grant_outside"]) or \
                          (priv.startswith("USE") and self.flags["s_grant_path"])
                if not allowed:
                    return self._fail("PERMISSION_DENIED: user does not have MANAGE on the object")
            (self.grants.add if verb == "GRANT" else self.grants.discard)((app, priv, kind, obj))
            return self._ok()
        if statement.startswith("SELECT count"):
            app = actor.split(":", 1)[1]
            table = re.search(r"FROM (.+)$", statement)[1]
            path = all((app, p, k, o) in self.grants for p, k, o in
                       (("USE CATALOG", "CATALOG", f"`{N.catalog}`"), ("USE SCHEMA", "SCHEMA", f"`{N.catalog}`.`data`")))
            outside = table.endswith(f"`{ds.OUTSIDE}`")
            if path and ((app, "SELECT", "TABLE", table) in self.grants or (outside and self.flags["t_reads_outside"])):
                return self._ok([[ds.ROWS]])
            return self._fail("PERMISSION_DENIED: user does not have SELECT")
        if statement.startswith("DROP TABLE"):
            return self._fail("PERMISSION_DENIED: user does not have MANAGE")
        return self._fail("unsupported in the fake")


@pytest.fixture(autouse=True)
def _fresh_secrets():
    World.secret_values = {}


def spike(world=None, **flags):
    world = world or World(**flags)
    return world, Spike(world.client("admin"), lambda cid, secret: world.connect(cid, secret), "wh1", N)


def results(report):
    return {o["step"]: (o["result"], o["detail"]) for o in report["outcomes"]}


# ---- names, statements, stop rules -----------------------------------------------------------------------------------------------


def test_names_need_an_eight_hex_suffix_and_carry_the_prefixes():
    assert N.catalog == f"compass_connect_spike_{SUFFIX}" and N.setup.startswith("compass-spike-setup-")
    for bad in ("", "xyz", "0A1B2C3D", "0a1b2c3", "0a1b2c3d4"):
        with pytest.raises(ValueError):
            Names.for_run(bad)


def test_every_generated_statement_passes_the_stop_rule_and_none_touches_users_or_anything_else():
    statements = [*ds.fixture(N), *ds.setup_grants(N, "app-1"), *(s for _l, s in ds.s_grants_to_conn(N, "app-2")),
                  *(s for _l, s in ds.conn_reads(N)), *ds.revokes(N, "app-2"), ds.teardown_catalog(N), "SHOW CATALOGS"]
    for statement in statements:
        ds.assert_ours(statement, N)
    assert not any(re.search(r"\b(USER|GROUP|METASTORE|OWNER)\b", s, re.I) for s in statements)
    assert all("IF NOT EXISTS" not in s and "OR REPLACE" not in s for s in statements)  # plain CREATE: a race fails instead of adopting


@pytest.mark.parametrize("statement", [
    "DROP CATALOG `dev` CASCADE", "GRANT SELECT ON TABLE `dev`.`x`.`y` TO `app`", "ALTER CATALOG `compass_connect_spike_0a1b2c3d` OWNER TO `x`",
    "GRANT ALL PRIVILEGES ON CATALOG `compass_connect_spike_0a1b2c3d` TO `x`", "DROP CATALOG `compass_connect_spike_ffffffff` CASCADE",
    "GRANT CREATE CATALOG ON METASTORE TO `x`", "SELECT 1",
])
def test_the_stop_rule_refuses_anything_outside_the_sandbox(statement):
    with pytest.raises(SpikeStop):
        ds.assert_ours(statement, N)


# ---- the expected outcome (the plan's expectations) ------------------------------------------------------------------------------


def test_the_expected_workspace_answers_every_question_and_the_fallbacks_keep_the_later_questions_askable():
    world, s = spike()
    report = s.run()
    r = results(report)
    assert report["stopped"] == "" and report["leaks"]["clean"] is True
    assert r["s_creates_connection_principal"][0] == "denied"  # Q1
    assert r["s_creates_secret_for_admin_made_principal"][0] == "denied"  # Q2
    assert r["s_grants_select_orders"][0] == "allowed" and r["s_grants_select_customers"][0] == "allowed"  # Q3
    assert r["s_grants_select_outside"][0] == "denied" and r["s_grants_select_schema"][0] == "denied"
    assert r["s_grants_use_catalog"][0] == "denied" and r["s_grants_use_schema"][0] == "denied"
    assert r["s_gives_connection_principal_can_use"][0] == "denied"  # Q4
    assert r["t_read_orders"] == ("allowed", f"rows={ds.ROWS}") and r["t_read_customers"][0] == "allowed"  # Q6
    assert r["t_read_outside"][0] == "denied" and r["t_write_orders"][0] == "denied"
    assert r["t_create_table"][0] == "denied" and r["t_drop_orders"][0] == "denied"
    assert r["s_revokes_select"][0] == "allowed" and r["s_deletes_connection_principal"][0] == "denied"  # Q5
    assert "does not have" not in json.dumps(report) and "manager role" not in json.dumps(report)  # denials keep no API message
    assert set(report["fallbacks"]) == {"admin_created_connection_principal", "admin_created_connection_secret", "admin_granted_use_catalog",
                                        "admin_granted_use_schema", "admin_gave_connection_principal_can_use"}


def test_the_decoy_is_seen_or_not_and_is_never_touched_by_s():
    world, s = spike(s_sees_decoy=True)
    report = s.run()
    r = results(report)
    assert r["s_lists_principals_by_name"] == ("allowed", "found=1") and r["decoy_still_present"] == ("allowed", "decoy_seen_by_S=yes")
    world, s = spike(s_sees_decoy=False)
    r = results(s.run())
    assert r["s_lists_principals_by_name"] == ("allowed", "found=0") and r["decoy_still_present"] == ("allowed", "decoy_seen_by_S=no")


def test_the_decoy_survives_until_teardown_and_only_the_owner_removes_it_by_recorded_id():
    world, s = spike()
    s.preflight()
    s.setup()
    s.q7_decoy()
    assert any(p["display_name"] == N.decoy for p in world.principals.values())
    s.teardown_all()
    assert not any(p["display_name"].startswith(ds.PRINCIPAL_PREFIX) for p in world.principals.values())


# ---- a permissive workspace: surprises stop the run ---------------------------------------------------------------------------------


def test_s_able_to_grant_outside_the_ceiling_stops_the_run_and_still_tears_down():
    world, s = spike(s_grant_outside=True)
    report = s.run()
    assert report["stopped"] == "setup_principal_exceeds_ceiling"
    assert report["teardown"]["left"] == [] and world.catalogs == set() and report["sweep"] == {"catalogs": 0, "principals": 0, "warehouse_entries": 0}


def test_a_connection_principal_that_can_write_or_read_outside_stops_the_run():
    world, s = spike(t_reads_outside=True)
    assert s.run()["stopped"] == "connection_principal_not_read_only"
    world, s = spike(t_writes=True)
    assert s.run()["stopped"] == "connection_principal_not_read_only"


def test_a_permissive_s_creates_the_connection_principal_and_its_secret_and_no_fallback_is_needed():
    world, s = spike(s_create_sp=True, s_grant_path=True, s_acl=True, s_delete=True)
    report = s.run()
    r = results(report)
    assert r["s_creates_connection_principal"][0] == "allowed" and r["s_creates_secret_for_own_principal"][0] == "allowed"
    assert r["s_gives_connection_principal_can_use"][0] == "allowed" and r["s_deletes_connection_principal"][0] == "allowed"
    assert report["fallbacks"] == [] and report["stopped"] == "" and report["teardown"]["left"] == []


# ---- preflight, failure and teardown ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("flags", [{"existing_catalog": "compass_connect_spike_deadbeef"}, {"existing_principal": "compass-spike-setup-deadbeef"}])
def test_leftovers_stop_the_run_before_anything_is_created(flags):
    world, s = spike(**flags)
    before = (dict(world.principals), set(world.catalogs))
    report = s.run()
    assert report["stopped"] == "leftovers_found" and report["outcomes"] == []
    assert (world.principals, world.catalogs) == before and not any(st.startswith("CREATE") for _a, st in world.statements)


def test_a_failed_setup_step_is_reported_by_type_and_what_was_created_is_removed():
    world, s = spike(fail_admin_sql="CREATE TABLE")
    report = s.run()
    assert report["stopped"].startswith("admin_step_failed") and world.catalogs == set() and report["teardown"]["left"] == []
    assert "internal error" not in json.dumps(report)  # no API message is ever kept


def test_teardown_is_created_only_and_deletes_secrets_before_principals():
    world, s = spike()
    s.run()
    admin_calls = [st for a, st in world.statements if a == "admin" and st.startswith("DROP")]
    assert admin_calls == [ds.teardown_catalog(N)]
    assert world.secrets == {} and not world.principals and world.acl == set()


def test_a_principal_s_already_deleted_is_not_reported_as_left_behind():
    world, s = spike(s_create_sp=True, s_grant_path=True, s_acl=True, s_delete=True)
    assert s.run()["teardown"]["left"] == []


def test_a_catalog_that_was_not_created_by_this_run_is_never_dropped():
    world, s = spike()
    s._catalog_created = False
    s.teardown_all()
    assert not any(st.startswith("DROP CATALOG") for _a, st in world.statements)


def test_the_sweep_reports_what_remains():
    world, s = spike()
    s.run()
    world._new_principal("compass-spike-conn-stray", "admin")
    s.sweep()
    assert s.report.sweep["principals"] == 1


# ---- secrets never leave memory -------------------------------------------------------------------------------------------------------


def test_no_secret_appears_in_the_report_and_the_scanner_would_catch_one():
    world, s = spike()
    report = s.run()
    text = json.dumps(report)
    assert not any(value in text for value in World.secret_values.values())
    assert report["leaks"]["watched"] == ["conn_secret", "setup_secret"]
    s.scanner.scan("somewhere", f"x {next(iter(World.secret_values.values()))} y")
    assert s.scanner.clean is False and s.scanner.findings[0].secret in ("setup_secret", "conn_secret")


@pytest.mark.parametrize(("exc", "expected"), [(PermissionDenied("nope"), "denied"), (RuntimeError("FAILED PERMISSION_DENIED"), "denied"),
                                                (ValueError("boom"), "error:ValueError")])
def test_a_denial_is_told_from_another_error_without_keeping_the_message(exc, expected):
    assert ds.classify(exc) == expected


# ---- the command line and the guard --------------------------------------------------------------------------------------------------


def test_the_command_line_refuses_without_the_live_flag_or_with_bad_input():
    base = ["--expect-host", "dbc-1234.cloud.databricks.com", "--suffix", SUFFIX, "--warehouse", "Serverless Starter Warehouse"]
    assert ds.main(base) == 2  # no --allow-live
    assert ds.main(["--allow-live", "--expect-host", "evil.example.com", "--suffix", SUFFIX, "--warehouse", "w"]) == 2
    assert ds.main(["--allow-live", "--expect-host", "dbc-1234.cloud.databricks.com", "--suffix", "nothex!!", "--warehouse", "w"]) == 2


def test_the_spike_is_a_dev_tool_the_app_never_imports():
    offenders = [str(p.relative_to(APP)) for p in APP.rglob("*.py")
                 if "connect/e2e" not in p.as_posix() and re.search(r"databricks_spike|connect\.e2e", p.read_text())]
    assert offenders == []


def test_the_statements_document_equals_the_generator():
    path = Path(__file__).resolve().parents[3] / "docs/design/CONNECT_DATABRICKS_SPIKE_STATEMENTS.md"
    assert path.read_text() == ds.render_markdown(warehouse="Serverless Starter Warehouse"), (
        "regenerate: python3 -c 'from app.connect.e2e.databricks_spike import render_markdown; "
        "print(render_markdown(warehouse=\"Serverless Starter Warehouse\"), end=\"\")'")


def test_the_warehouse_is_picked_by_name_only():
    world = World()
    assert ds.pick_warehouse(world.client("admin"), "Serverless Starter Warehouse") == "wh1"
    for name in ("Other", ""):
        with pytest.raises(SpikeStop):
            ds.pick_warehouse(world.client("admin"), name)
