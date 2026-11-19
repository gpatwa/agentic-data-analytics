"""Databricks pre-created-principal registration: the generator, the verifier and the end-to-end driver on a fake workspace."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.connect import databricks_registration as reg
from app.connect.e2e import databricks_e2e as e2e
from app.connect.e2e import databricks_spike as sp
from app.connect.grants import ConnectScope, ScopeError

SUFFIX = "0a1b2c3d"
N = sp.Names.for_run(SUFFIX)
CEILING = ConnectScope(N.catalog, (("data", "orders"), ("data", "customers")))
APP = "app-1"


def rows_for(app=APP, extra=()):
    base = [("USE CATALOG", "CATALOG", N.catalog), ("USE SCHEMA", "SCHEMA", f"{N.catalog}.data"),
            ("SELECT", "TABLE", f"{N.catalog}.data.orders"), ("SELECT", "TABLE", f"{N.catalog}.data.customers"), *extra]
    return [{"actionType": a, "objectType": t, "objectKey": k} for a, t, k in base]


# ---- generator -----------------------------------------------------------------------------------------------------------------


def test_the_script_grants_only_the_path_and_select_on_the_named_tables_and_never_anything_wider():
    script = reg.generate(CEILING, suffix=SUFFIX, warehouse_name="Starter", application_id="app-1")
    sql = [s.arg for s in script.steps if s.op == "sql"]
    assert len(sql) == 4 and sql[0].startswith("GRANT USE CATALOG") and sum("GRANT SELECT ON TABLE" in s for s in sql) == 2
    assert not any(re.search(r"MANAGE|ALL PRIVILEGES|MODIFY|WITH GRANT|OWNER|\*|%", s) for s in sql)
    assert [s.op for s in script.steps][:3] == ["create_principal", "create_secret", "warehouse_can_use"]
    assert script.principal == f"compass-conn-{SUFFIX}"
    assert all(s.count("`") % 2 == 0 for s in sql)


def test_the_removal_revokes_what_was_granted_then_deletes_the_principal():
    script = reg.generate(CEILING, suffix=SUFFIX, warehouse_name="Starter", application_id="app-1")
    ops = [s.op for s in script.teardown]
    assert ops[-1] == "delete_principal" and all(o == "sql" for o in ops[:-1]) and len(ops) == 5
    assert all(s.arg.startswith("REVOKE") for s in script.teardown if s.op == "sql")


@pytest.mark.parametrize("bad", ["", "xyz", "0A1B2C3D", "0a1b2c3d4"])
def test_the_suffix_must_be_eight_lowercase_hex(bad):
    with pytest.raises(ScopeError):
        reg.generate(CEILING, suffix=bad, warehouse_name="Starter")


def test_wildcards_backticks_and_a_missing_warehouse_are_refused():
    for part in ("a`b", "x*", "y%", ""):
        with pytest.raises(ScopeError):
            reg.bt(part)
    with pytest.raises(ScopeError):
        reg.generate(CEILING, suffix=SUFFIX, warehouse_name=" ")


# ---- verifier ------------------------------------------------------------------------------------------------------------------


GOOD_ACL = [(APP, "CAN_USE")]


def test_exactly_the_scripts_grants_and_can_use_verify():
    assert reg.verify_registration(rows_for(), GOOD_ACL, CEILING, APP).ok


@pytest.mark.parametrize(("extra", "field"), [
    ((("MANAGE", "TABLE", f"{N.catalog}.data.orders"),), "dangerous"),
    ((("MODIFY", "TABLE", f"{N.catalog}.data.orders"),), "dangerous"),
    ((("SELECT", "TABLE", f"{N.catalog}.data.payroll_outside"),), "unexpected"),
    ((("SELECT", "SCHEMA", f"{N.catalog}.data"),), "unexpected"),
])
def test_wider_grants_are_reported(extra, field):
    report = reg.verify_registration(rows_for(extra=extra), GOOD_ACL, CEILING, APP)
    assert not report.ok and getattr(report, field)


def test_missing_grants_and_wrong_warehouse_permission_are_reported():
    assert reg.verify_registration(rows_for()[1:], GOOD_ACL, CEILING, APP).code == "registration_missing"
    assert reg.verify_registration(rows_for(), [], CEILING, APP).code == "registration_warehouse"
    assert reg.verify_registration(rows_for(), [(APP, "CAN_MANAGE")], CEILING, APP).code == "registration_warehouse"
    assert reg.verify_registration(rows_for(), [(APP, "CAN_USE"), (APP, "CAN_MANAGE")], CEILING, APP).code == "registration_warehouse"


# ---- the end-to-end driver on a fake workspace ----------------------------------------------------------------------------------


class Fake:
    def __init__(self, *, fail_sql=None, extra_grant=None, existing_principal=None, acl_level="CAN_USE"):
        self.catalogs, self.grants, self.principals, self.secrets, self.acl, self.statements = set(), set(), {}, {}, {}, []
        self.fail_sql, self.extra_grant, self.acl_level, self.n = fail_sql, extra_grant, acl_level, 0
        if existing_principal:
            self.principals["old"] = SimpleNamespace(id="old", application_id="old-app", display_name=existing_principal)
        self.statement_execution = SimpleNamespace(execute_statement=self._exec)
        self.service_principals = SimpleNamespace(create=self._create, list=lambda: iter(self.principals.values()), delete=self._delete)
        self.service_principal_secrets_proxy = SimpleNamespace(create=self._secret, delete=lambda pid, sid: self.secrets.pop(sid))
        self.warehouses = SimpleNamespace(update_permissions=self._acl, list=lambda: [SimpleNamespace(id="wh1", name="Starter")],
                                          get_permissions=self._perms)

    def _create(self, display_name):
        self.n += 1
        p = SimpleNamespace(id=f"p{self.n}", application_id=f"app-{self.n}", display_name=display_name)
        self.principals[p.id] = p
        return p

    def _delete(self, pid):
        self.acl.pop(self.principals.pop(pid).application_id, None)

    def _secret(self, service_principal_id, lifetime):
        self.n += 1
        self.secrets[f"s{self.n}"] = f"oauth-secret-{self.n}-do-not-leak"
        return SimpleNamespace(id=f"s{self.n}", secret=self.secrets[f"s{self.n}"])

    def _acl(self, wid, access_control_list):
        for e in access_control_list:
            self.acl[e.service_principal_name] = self.acl_level

    def _perms(self, wid):
        return SimpleNamespace(access_control_list=[SimpleNamespace(service_principal_name=a, all_permissions=[SimpleNamespace(permission_level=lv)])
                                                    for a, lv in self.acl.items()])

    def _res(self, rows=()):
        return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="SUCCEEDED")), result=SimpleNamespace(data_array=[list(r) for r in rows]))

    def _exec(self, statement, warehouse_id, wait_timeout):
        self.statements.append(statement)
        if self.fail_sql and self.fail_sql in statement:
            return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="FAILED"), error=None), result=None)
        if statement == "SHOW CATALOGS":
            return self._res([[c] for c in sorted(self.catalogs)])
        if statement.startswith("CREATE CATALOG"):
            self.catalogs.add(re.search(r"`([^`]+)`", statement)[1])
        elif statement.startswith("DROP CATALOG"):
            self.catalogs.clear()
            self.grants.clear()
        m = re.match(r"(GRANT|REVOKE) (.+?) ON (CATALOG|SCHEMA|TABLE) (.+?) (?:TO|FROM) `([^`]+)`$", statement)
        if m:
            verb, priv, kind, obj, app = m.groups()
            (self.grants.add if verb == "GRANT" else self.grants.discard)((app, priv, kind, obj.replace("`", "")))
        s = re.match(r"SHOW GRANTS `([^`]+)` ON (CATALOG|SCHEMA|TABLE) (.+)$", statement)
        if s:
            app, kind, obj = s.groups()
            rows = [(app, p, k, o) for a, p, k, o in self.grants if a == app and k == kind and o == obj.replace("`", "")]
            if self.extra_grant and kind == "TABLE" and obj.endswith(f"`{sp.OUTSIDE}`"):
                rows.append((app, *self.extra_grant))
            return self._res(rows)
        return self._res()


def conn_test(app, secret):
    return [("read_orders", "allowed", "rows=30"), ("read_customers", "allowed", "rows=30"), ("read_outside", "denied", "")]


def driver(fake=None, test=conn_test):
    fake = fake or Fake()
    return fake, e2e.E2E(fake, test, "wh1", "Starter", N)


def test_a_clean_run_registers_verifies_tests_the_connection_and_leaves_nothing():
    fake, d = driver()
    result = d.run()
    steps = {o["step"]: o for o in result["outcomes"]}
    assert result["stopped"] == "" and steps["verify_registration"]["result"] == "allowed"
    assert steps["connection_read_orders"]["detail"] == "rows=30" and steps["connection_read_outside"]["result"] == "denied"
    assert result["teardown"]["left"] == [] and result["sweep"] == {"catalogs": 0, "principals": 0, "warehouse_entries": 0}
    assert not fake.principals and not fake.secrets and not fake.acl and not fake.catalogs
    assert result["leaks"]["clean"] and result["leaks"]["watched"] == ["principal_secret"]  # the secret was watched all along
    assert not any(v in json.dumps(result) for v in ["oauth-secret"])


def test_removal_revokes_before_deleting_and_drops_only_the_catalog_this_run_created():
    fake, d = driver()
    d.run()
    revokes = [s for s in fake.statements if s.startswith("REVOKE")]
    assert len(revokes) == 4 and [s for s in fake.statements if s.startswith("DROP")] == [sp.teardown_catalog(N)]


def test_a_wider_grant_than_the_script_makes_is_caught_by_the_verifier():
    fake, d = driver(Fake(extra_grant=("SELECT", "TABLE", f"{N.catalog}.data.{sp.OUTSIDE}")))
    result = d.run()
    assert next(o for o in result["outcomes"] if o["step"] == "verify_registration")["result"] == "problem:registration_unexpected"


def test_a_warehouse_permission_other_than_can_use_is_caught():
    fake, d = driver(Fake(acl_level="CAN_MANAGE"))
    assert next(o for o in d.run()["outcomes"] if o["step"] == "verify_registration")["result"] == "problem:registration_warehouse"


def test_leftovers_stop_the_run_before_anything_is_created():
    fake, d = driver(Fake(existing_principal="compass-conn-deadbeef"))
    result = d.run()
    assert result["stopped"] == "leftovers_found" and not any(s.startswith("CREATE") for s in fake.statements)


def test_a_failed_grant_still_removes_everything_created_and_keeps_no_message():
    fake, d = driver(Fake(fail_sql="GRANT SELECT"))
    result = d.run()
    assert result["stopped"].startswith("unexpected") and not fake.principals and not fake.catalogs and not fake.secrets
    assert result["teardown"]["left"] == []


def test_a_connection_that_can_read_outside_is_reported_not_hidden():
    fake, d = driver(test=lambda a, s: [("read_outside", "allowed", "rows=30")])
    assert any(o["step"] == "connection_read_outside" and o["result"] == "allowed" for o in d.run()["outcomes"])


def test_the_command_line_refuses_without_the_flag_or_with_a_bad_host():
    base = ["--expect-host", "dbc-1.cloud.databricks.com", "--suffix", SUFFIX, "--warehouse", "Starter"]
    assert e2e.main(base) == 2
    assert e2e.main(["--allow-live", "--expect-host", "evil.example.com", "--suffix", SUFFIX, "--warehouse", "w"]) == 2


def test_the_dev_tools_are_never_imported_by_the_app():
    app = Path(__file__).resolve().parents[1] / "app"
    assert [p.name for p in app.rglob("*.py") if "connect/e2e" not in p.as_posix() and re.search(r"databricks_e2e|databricks_spike", p.read_text())] == []


def test_a_catalog_the_run_could_not_create_is_never_dropped():
    fake, d = driver(Fake(fail_sql="CREATE CATALOG"))
    result = d.run()
    assert result["stopped"].startswith("unexpected") and not any(s.startswith("DROP") for s in fake.statements)


def test_the_statements_document_equals_the_generator():
    path = Path(__file__).resolve().parents[3] / "docs/design/CONNECT_DATABRICKS_REGISTRATION_STATEMENTS.md"
    assert path.read_text() == e2e.render_markdown(), "regenerate with render_markdown()"
