"""Sandbox provisioning (no manual steps): scripted admin connection and workspace client. No network, no account."""

from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from tests.test_warehouse_validation import FakeSession, World

from app.execution import warehouse_provisioning as wp
from app.execution import warehouse_validation as wv
from app.execution.warehouse_sessions import DatabricksConfig, SnowflakeConfig

# ------------------------------------------------------------------------------------------------------- Snowflake


class AdminCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql):
        self.conn.statements.append(sql)
        failing = self.conn.fail_on
        if failing and failing in sql:
            raise RuntimeError("SELECT leaked-secret FROM t")

    def close(self):
        pass


class AdminConn:
    def __init__(self, fail_on=None):
        self.statements, self.fail_on, self.closed = [], fail_on, False

    def cursor(self):
        return AdminCursor(self)

    def close(self):
        self.closed = True


def snowflake_provisioner(conn=None, **kwargs):
    return wp.SnowflakeProvisioner(conn or AdminConn(), account="org-acct", key_pair=lambda: (b"\x30private-der", "PUBLICBODY"), **kwargs)


def test_a_real_key_pair_is_generated_in_memory_and_the_halves_match():
    private, public_body = wp.new_key_pair()
    key = serialization.load_der_private_key(private, password=None)
    import base64

    expected = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert base64.b64decode(public_body) == expected


def test_snowflake_setup_creates_only_named_objects_and_registers_the_public_key_not_the_private():
    conn = AdminConn()
    result = snowflake_provisioner(conn).setup()
    sql = conn.statements
    assert result.steps == len(sql) == len(wp.SNOWFLAKE_SETUP) + 1
    assert sql[-1] == f"ALTER USER {wp.SNOWFLAKE_USER} SET RSA_PUBLIC_KEY = 'PUBLICBODY'"
    assert all("COMPASS_VALIDATION" in s for s in sql if s.startswith(("CREATE", "DROP", "ALTER", "GRANT", "USE")))
    assert not any("private" in s.lower() for s in sql)
    config = result.config
    assert (config.user, config.role, config.database, config.schema) == ("COMPASS_VALIDATION_SVC", "COMPASS_VALIDATION_RO", "COMPASS_VALIDATION", "SANDBOX")
    assert config.private_key_der == b"\x30private-der" and "private-der" not in repr(config)


def test_the_snowflake_role_is_granted_use_and_select_only():
    grants = [s for s in wp.SNOWFLAKE_SETUP if s.startswith("GRANT") and "TO ROLE" in s]
    assert grants and all(g.startswith(("GRANT USAGE", "GRANT SELECT")) for g in grants)
    assert not any(" ALL " in g or "OWNERSHIP" in g or "INSERT" in g for g in grants)


def test_a_failed_setup_step_is_reported_by_step_and_type_only():
    with pytest.raises(wp.ProvisioningError) as info:
        snowflake_provisioner(AdminConn(fail_on="CREATE WAREHOUSE")).setup()
    assert info.value.code == "setup_step_4:RuntimeError" and "leaked-secret" not in str(info.value)


def test_snowflake_teardown_drops_only_the_named_objects_and_tries_every_one():
    conn = AdminConn()
    assert snowflake_provisioner(conn).teardown() == 5
    assert conn.statements == list(wp.SNOWFLAKE_TEARDOWN)
    assert all(s.startswith("DROP") and "COMPASS_VALIDATION" in s for s in conn.statements)
    broken = AdminConn(fail_on="DROP ROLE")
    with pytest.raises(wp.ProvisioningError) as info:
        snowflake_provisioner(broken).teardown()
    assert info.value.code == "teardown_incomplete:1" and len(broken.statements) == 5  # the others still ran


def test_the_snowflake_admin_signs_in_through_the_browser_with_no_password():
    seen = {}
    connector = SimpleNamespace(connect=lambda **kwargs: seen.update(kwargs) or "conn")
    assert wp.connect_snowflake_admin("org-acct", "me", connector=connector) == "conn"
    assert seen["authenticator"] == "externalbrowser" and seen["role"] == "ACCOUNTADMIN" and "password" not in seen


# ------------------------------------------------------------------------------------------------------ Databricks


class Client:
    def __init__(self, *, fail_state=None, catalog_exists=False, fail_delete=None):
        self.sql, self.calls, self.fail_state, self.catalog_exists, self.fail_delete = [], [], fail_state, catalog_exists, fail_delete
        self.statement_execution = SimpleNamespace(execute_statement=self._execute)
        self.service_principals = SimpleNamespace(create=self._create_sp, delete=lambda i: self._delete("sp", i))
        self.warehouses = SimpleNamespace(
            update_permissions=lambda wid, access_control_list: self.calls.append(("acl", wid, access_control_list)),
            list=lambda: [SimpleNamespace(id="wh1", name="Starter"), SimpleNamespace(id="wh2", name="Other")],
        )
        self.token_management = SimpleNamespace(create_obo_token=self._token, delete=lambda i: self._delete("token", i))

    def _execute(self, statement, warehouse_id, wait_timeout):
        self.sql.append(statement)
        if self.fail_state and self.fail_state in statement:
            return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="FAILED")), result=None)
        rows = [["compass_validation"]] if statement.startswith("SHOW CATALOGS") and self.catalog_exists else []
        return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="SUCCEEDED")), result=SimpleNamespace(data_array=rows))

    def _create_sp(self, display_name):
        self.calls.append(("sp", display_name))
        return SimpleNamespace(id="sp-id", application_id="app-uuid")

    def _token(self, application_id, lifetime_seconds, comment):
        self.calls.append(("token", application_id, lifetime_seconds))
        return SimpleNamespace(token_value="dapi-secret-token", token_info=SimpleNamespace(token_id="tok-id"))

    def _delete(self, kind, ident):
        self.calls.append((f"delete_{kind}", ident))
        if self.fail_delete == kind:
            raise RuntimeError("boom")


TYPES = SimpleNamespace(
    WarehouseAccessControlRequest=lambda **kw: SimpleNamespace(**kw), WarehousePermissionLevel=SimpleNamespace(CAN_USE="CAN_USE")
)


def databricks_provisioner(client=None):
    client = client or Client()
    return wp.DatabricksProvisioner(client, host="dbc-host.example", warehouse_id="wh1", sql_types=TYPES), client


def test_databricks_setup_creates_a_principal_with_least_privilege_and_a_short_lived_token():
    provisioner, client = databricks_provisioner()
    result = provisioner.setup()
    config = result.config
    assert (config.catalog, config.schema, config.server_hostname, config.http_path) == ("compass_validation", "sandbox", "dbc-host.example", "/sql/1.0/warehouses/wh1")
    assert config.access_token == "dapi-secret-token" and "dapi-secret-token" not in repr(config)
    grants = [s for s in client.sql if s.startswith("GRANT")]
    assert [g.split(" ON ")[0] for g in grants] == ["GRANT USE CATALOG", "GRANT USE SCHEMA", "GRANT SELECT"]
    assert all("`app-uuid`" in g for g in grants)
    acl = next(c for c in client.calls if c[0] == "acl")
    assert acl[1] == "wh1" and acl[2][0].permission_level == "CAN_USE" and acl[2][0].service_principal_name == "app-uuid"
    assert ("token", "app-uuid", 3600) in client.calls
    assert not any(s.startswith(("DROP", "ALTER", "DELETE", "UPDATE")) for s in client.sql)


def test_databricks_never_edits_the_warehouse_it_picks():
    client = Client()
    assert wp.pick_warehouse(client) == "wh1" and wp.pick_warehouse(client, "Other") == "wh2"
    with pytest.raises(wp.ProvisioningError):
        wp.pick_warehouse(client, "missing")
    assert not hasattr(client.warehouses, "edit") and not hasattr(client.warehouses, "create")


def test_a_failed_databricks_statement_is_reported_by_step_without_the_message():
    provisioner, _ = databricks_provisioner(Client(fail_state="CREATE SCHEMA"))
    with pytest.raises(wp.ProvisioningError) as info:
        provisioner.setup()
    assert info.value.code.startswith("setup_schema:") and "FAILED" not in info.value.code.split(":")[0]


def test_databricks_teardown_removes_the_token_the_principal_and_the_schema_and_a_catalog_it_created():
    provisioner, client = databricks_provisioner()
    provisioner.setup()
    client.sql.clear()
    client.calls.clear()
    assert provisioner.teardown() == 4
    assert client.calls == [("delete_token", "tok-id"), ("delete_sp", "sp-id")]
    assert client.sql == ["DROP SCHEMA IF EXISTS `compass_validation`.`sandbox` CASCADE", "DROP CATALOG IF EXISTS `compass_validation` CASCADE"]


def test_a_catalog_that_existed_before_is_left_alone():
    provisioner, client = databricks_provisioner(Client(catalog_exists=True))
    provisioner.setup()
    client.sql.clear()
    provisioner.teardown()
    assert client.sql == ["DROP SCHEMA IF EXISTS `compass_validation`.`sandbox` CASCADE"]


def test_databricks_teardown_continues_past_a_failure_and_reports_the_count():
    provisioner, client = databricks_provisioner(Client(fail_delete="token"))
    provisioner.setup()
    client.calls.clear()
    with pytest.raises(wp.ProvisioningError) as info:
        provisioner.teardown()
    assert info.value.code == "teardown_incomplete:1" and ("delete_sp", "sp-id") in client.calls


def test_the_databricks_admin_signs_in_through_the_browser():
    seen = {}
    sdk = SimpleNamespace(WorkspaceClient=lambda **kwargs: seen.update(kwargs) or "client")
    assert wp.connect_databricks_admin("dbc-host.example", sdk=sdk) == "client"
    assert seen == {"host": "https://dbc-host.example", "auth_type": "external-browser"}


# --------------------------------------------------------------------------------------------- the provisioned run


class FakeProvisioner:
    def __init__(self, config, *, setup_error=None, teardown_error=None):
        self.config, self.setup_error, self.teardown_error = config, setup_error, teardown_error
        self.torn_down = self.closed = False

    def setup(self):
        if self.setup_error:
            raise wp.ProvisioningError(self.setup_error)
        return wp.Provisioned(self.config, 14)

    def teardown(self):
        self.torn_down = True
        if self.teardown_error:
            raise wp.ProvisioningError(self.teardown_error)
        return 5

    def close(self):
        self.closed = True


SF_CONFIG = SnowflakeConfig(
    account="org-acct", user="u", warehouse="W", database="COMPASS_VALIDATION", schema="SANDBOX", role="COMPASS_VALIDATION_RO",
    private_key_der=b"\x30secret-der-bytes",
)
DB_CONFIG = DatabricksConfig(
    server_hostname="dbc-host.example", http_path="/p", catalog="compass_validation", schema="sandbox", access_token="dapi-secret-token"
)


def run_provisioned(monkeypatch, tmp_path, engine, provisioner, **world_options):
    world = World(engine, **world_options)
    name = "SnowflakeSession" if engine == "snowflake" else "DatabricksSession"
    monkeypatch.setattr(wv, name, lambda config: FakeSession(world))
    monkeypatch.setattr(wv, "make_provisioner", lambda args: provisioner)
    out = tmp_path / "r.md"
    target = "org-acct" if engine == "snowflake" else "dbc-host.example"
    argv = [engine, "--allow-live", "--provision", "--expect-target", target, "--out", str(out)]
    return wv.main(argv, {}), out


@pytest.mark.parametrize(("engine", "config"), [("snowflake", SF_CONFIG), ("databricks", DB_CONFIG)])
def test_a_provisioned_run_sets_up_validates_and_tears_down_with_no_secret_in_the_report(monkeypatch, tmp_path, engine, config):
    provisioner = FakeProvisioner(config)
    code, out = run_provisioned(monkeypatch, tmp_path, engine, provisioner)
    text = out.read_text()
    assert code == 0 and provisioner.torn_down and provisioner.closed
    assert "setup ok, 14 steps" in text and "teardown ok, 5 steps" in text and "all checks ok" in text
    assert "dapi-secret-token" not in text and "secret-der-bytes" not in text


def test_teardown_runs_even_when_a_check_fails(monkeypatch, tmp_path):
    provisioner = FakeProvisioner(SF_CONFIG)
    code, out = run_provisioned(monkeypatch, tmp_path, "snowflake", provisioner, columns=("id",))
    assert code == 1 and provisioner.torn_down and "columns_differ" in out.read_text()


def test_a_setup_failure_still_tears_down_and_reports_the_code(monkeypatch, tmp_path):
    provisioner = FakeProvisioner(SF_CONFIG, setup_error="setup_step_4:RuntimeError")
    code, out = run_provisioned(monkeypatch, tmp_path, "snowflake", provisioner)
    assert code == 1 and provisioner.torn_down
    assert "setup failed: setup_step_4:RuntimeError" in out.read_text()


def test_an_incomplete_teardown_fails_the_run_and_says_what_to_remove(monkeypatch, tmp_path, capsys):
    provisioner = FakeProvisioner(DB_CONFIG, teardown_error="teardown_incomplete:2")
    code, out = run_provisioned(monkeypatch, tmp_path, "databricks", provisioner)
    assert code == 1 and "TEARDOWN INCOMPLETE: teardown_incomplete:2" in out.read_text()
    assert "remove the COMPASS_VALIDATION" in capsys.readouterr().err


def test_snowflake_provisioning_needs_an_admin_user_and_refuses_before_any_sign_in(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(wp, "connect_snowflake_admin", lambda *a, **k: pytest.fail("signed in"))
    code = wv.main(["snowflake", "--allow-live", "--provision", "--expect-target", "org-acct", "--out", str(tmp_path / "r.md")], {})
    assert code == 2 and "--admin-user" in capsys.readouterr().err


def test_provisioning_still_needs_the_live_flag_and_a_named_target(tmp_path):
    assert wv.main(["databricks", "--provision", "--expect-target", "h", "--out", str(tmp_path / "r.md")], {}) == 2
    assert wv.main(["databricks", "--provision", "--allow-live", "--out", str(tmp_path / "r.md")], {}) == 2


def test_the_runtime_api_never_imports_the_admin_tool():
    import pathlib

    app = pathlib.Path(wp.__file__).parents[1]
    importers = [p.name for p in app.rglob("*.py") if "warehouse_provisioning" in p.read_text() and p.name not in {"warehouse_provisioning.py", "warehouse_validation.py"}]
    assert importers == []
