"""Sandbox provisioning and its safety rules (plan P1): scripted admin connection and workspace client. No network, no account."""

import re
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from tests.test_warehouse_validation import FakeSession, World

from app.execution import warehouse_provisioning as wp
from app.execution import warehouse_validation as wv
from app.execution.warehouse_sessions import DatabricksConfig, SnowflakeConfig

TAG = wp.TAG

# ------------------------------------------------------------------------------------------------------- Snowflake


class AdminCursor:
    KINDS = {"USERS": "user", "ROLES": "role", "WAREHOUSES": "warehouse", "RESOURCE MONITORS": "resource monitor", "DATABASES": "database"}

    def __init__(self, conn):
        self.conn, self.description, self._rows = conn, None, []

    def execute(self, sql):
        self.conn.statements.append(sql)
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise RuntimeError("SELECT leaked-secret FROM t")
        match = re.match(r"SHOW (USERS|ROLES|WAREHOUSES|RESOURCE MONITORS|DATABASES) LIKE '([^']+)'", sql)
        if match:
            kind = self.KINDS[match.group(1)]
            self.description = [("name",), ("comment",)]
            self._rows = []
            if kind in self.conn.existing:  # a value of None means the object exists but has no comment
                self._rows = [(self.conn.names.get(kind, match.group(2)), self.conn.existing[kind] or "")]
            if self.conn.wildcard_noise and kind == "database":
                self._rows.append(("COMPASSXVALIDATION", ""))  # LIKE treats _ as a wildcard: this row is not our name

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class AdminConn:
    def __init__(self, fail_on=None, existing=None, wildcard_noise=False, names=None):
        self.statements, self.fail_on, self.existing = [], fail_on, existing or {}  # kind -> the object's comment (None: no comment)
        self.wildcard_noise, self.names, self.closed = wildcard_noise, names or {}, False

    def cursor(self):
        return AdminCursor(self)

    def creates(self):
        return [s for s in self.statements if s.startswith("CREATE")]

    def drops(self):
        return [s for s in self.statements if s.startswith("DROP")]

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


def test_snowflake_setup_never_adopts_or_overwrites_so_it_uses_plain_create_and_tags_what_it_makes():
    statements = [s for _, s in wp.SNOWFLAKE_SETUP]
    assert not any("IF NOT EXISTS" in s or "OR REPLACE" in s for s in statements)  # a race fails instead of taking an object over
    tagged = [s for kind, s in wp.SNOWFLAKE_SETUP if kind in ("database", "warehouse", "role", "user")]
    assert len(tagged) == 4 and all(f"COMMENT = '{TAG}'" in s for s in tagged)
    assert [k for k, _ in wp.SNOWFLAKE_SETUP if k] == ["database", "resource monitor", "warehouse", "role", "user"]


def test_snowflake_setup_on_a_clean_account_checks_first_then_creates_and_registers_only_the_public_key():
    conn = AdminConn()
    result = snowflake_provisioner(conn).setup()
    sql = conn.statements
    assert [s.split(" LIKE")[0] for s in sql[:5]] == [f"SHOW {k}" for k in ("USERS", "ROLES", "WAREHOUSES", "RESOURCE MONITORS", "DATABASES")]
    assert sql[-1] == f"ALTER USER {wp.SNOWFLAKE_USER} SET RSA_PUBLIC_KEY = 'PUBLICBODY'" and result.steps == len(wp.SNOWFLAKE_SETUP) + 1
    assert not any("private" in s.lower() for s in sql)
    config = result.config
    assert (config.user, config.role, config.database, config.schema) == ("COMPASS_VALIDATION_SVC", "COMPASS_VALIDATION_RO", "COMPASS_VALIDATION", "SANDBOX")
    assert config.private_key_der == b"\x30private-der" and "private-der" not in repr(config)


@pytest.mark.parametrize("kind", ["user", "role", "warehouse", "resource monitor", "database"])
def test_a_name_that_exists_and_is_not_ours_stops_the_run_before_anything_is_created(kind):
    conn = AdminConn(existing={kind: "someone else's, not ours"})
    with pytest.raises(wp.ProvisioningError) as info:
        snowflake_provisioner(conn).setup()
    assert info.value.code == "name_taken" and any(kind in name for name in info.value.names)
    assert conn.creates() == [] and conn.drops() == []  # nothing created, nothing dropped, nothing altered


def test_leftovers_that_carry_our_tag_stop_the_run_and_are_named_not_adopted():
    conn = AdminConn(existing={"database": TAG, "warehouse": TAG, "role": TAG, "user": TAG, "resource monitor": None})
    with pytest.raises(wp.ProvisioningError) as info:
        snowflake_provisioner(conn).setup()
    assert info.value.code == "leftovers_found" and len(info.value.names) == 5
    assert conn.creates() == [] and conn.drops() == []


def test_a_resource_monitor_has_no_comment_so_it_is_ours_only_beside_our_tagged_warehouse():
    alone = AdminConn(existing={"resource monitor": None})
    with pytest.raises(wp.ProvisioningError) as info:
        snowflake_provisioner(alone).setup()
    assert info.value.code == "name_taken"
    beside = AdminConn(existing={"resource monitor": None, "warehouse": TAG})
    with pytest.raises(wp.ProvisioningError) as info:
        snowflake_provisioner(beside).setup()
    assert info.value.code == "leftovers_found"


def test_the_existence_check_matches_the_exact_name_because_like_treats_an_underscore_as_a_wildcard():
    conn = AdminConn(wildcard_noise=True)  # SHOW DATABASES also returns COMPASSXVALIDATION
    assert snowflake_provisioner(conn).setup().steps  # not mistaken for our database


def test_a_failed_setup_step_is_reported_by_step_and_type_only():
    with pytest.raises(wp.ProvisioningError) as info:
        snowflake_provisioner(AdminConn(fail_on="CREATE WAREHOUSE")).setup()
    assert info.value.code == "setup_step_4:RuntimeError" and "leaked-secret" not in str(info.value)


def test_teardown_drops_exactly_what_this_run_created_in_order():
    conn = AdminConn()
    provisioner = snowflake_provisioner(conn)
    provisioner.setup()
    conn.statements.clear()
    assert provisioner.teardown() == 5
    assert conn.drops() == [
        f"DROP USER IF EXISTS {wp.SNOWFLAKE_USER}", f"DROP ROLE IF EXISTS {wp.SNOWFLAKE_ROLE}", f"DROP WAREHOUSE IF EXISTS {wp.SNOWFLAKE_WAREHOUSE}",
        f"DROP RESOURCE MONITOR IF EXISTS {wp.SNOWFLAKE_MONITOR}", f"DROP DATABASE IF EXISTS {wp.SNOWFLAKE_DB}",
    ]


def test_a_failed_setup_drops_only_the_objects_it_had_already_created():
    conn = AdminConn(fail_on="CREATE WAREHOUSE")  # fails after the database and the monitor
    provisioner = snowflake_provisioner(conn)
    with pytest.raises(wp.ProvisioningError):
        provisioner.setup()
    conn.statements.clear()
    assert provisioner.teardown() == 2
    assert conn.drops() == [f"DROP RESOURCE MONITOR IF EXISTS {wp.SNOWFLAKE_MONITOR}", f"DROP DATABASE IF EXISTS {wp.SNOWFLAKE_DB}"]  # never the warehouse, role or user


def test_teardown_with_nothing_created_drops_nothing():
    conn = AdminConn()
    assert snowflake_provisioner(conn).teardown() == 0 and conn.drops() == []


def test_snowflake_teardown_tries_every_drop_and_names_what_is_left():
    conn = AdminConn()
    provisioner = snowflake_provisioner(conn)
    provisioner.setup()
    conn.fail_on = "DROP ROLE"
    conn.statements.clear()
    with pytest.raises(wp.ProvisioningError) as info:
        provisioner.teardown()
    assert info.value.code == "teardown_incomplete:1" and info.value.names == (f"role {wp.SNOWFLAKE_ROLE}",)
    assert len(conn.drops()) == 5  # the others still ran


def test_the_sweep_removes_tagged_leftovers_and_leaves_untagged_objects_alone():
    conn = AdminConn(existing={"database": TAG, "warehouse": TAG, "resource monitor": None, "role": "a customer's own role"})
    result = snowflake_provisioner(conn).sweep()
    assert result.dropped == [f"warehouse {wp.SNOWFLAKE_WAREHOUSE}", f"resource monitor {wp.SNOWFLAKE_MONITOR}", f"database {wp.SNOWFLAKE_DB}"]
    assert result.skipped == [f"role {wp.SNOWFLAKE_ROLE}"] and not any("ROLE" in s for s in conn.drops())


def test_a_sweep_of_a_clean_account_does_nothing():
    conn = AdminConn()
    result = snowflake_provisioner(conn).sweep()
    assert result.dropped == [] and result.skipped == [] and conn.drops() == []


def test_the_snowflake_admin_signs_in_through_the_browser_with_no_password_and_a_configurable_role():
    seen = {}
    connector = SimpleNamespace(connect=lambda **kwargs: seen.update(kwargs) or "conn")
    assert wp.connect_snowflake_admin("org-acct", "me", connector=connector) == "conn"
    assert seen["authenticator"] == "externalbrowser" and seen["role"] == "ACCOUNTADMIN" and "password" not in seen
    wp.connect_snowflake_admin("org-acct", "me", role="SECURITYADMIN", connector=connector)
    assert seen["role"] == "SECURITYADMIN"


# ------------------------------------------------------------------------------------------------------ Databricks


class Client:
    def __init__(self, *, fail_state=None, catalog_exists=False, fail_delete=None, schema="absent", principals=(), acl_after=()):
        self.sql, self.calls, self.fail_state, self.catalog_exists, self.fail_delete = [], [], fail_state, catalog_exists, fail_delete
        self.schema, self.principals, self.acl_after, self.deleted = schema, list(principals), list(acl_after), False
        self.statement_execution = SimpleNamespace(execute_statement=self._execute)
        self.service_principals = SimpleNamespace(
            create=self._create_sp, delete=lambda i: self._delete("sp", i), list=lambda: iter(self.principals)
        )
        self.warehouses = SimpleNamespace(
            update_permissions=lambda wid, access_control_list: self.calls.append(("acl", wid, access_control_list)),
            list=lambda: [SimpleNamespace(id="wh1", name="Starter"), SimpleNamespace(id="wh2", name="Other"), SimpleNamespace(id="wh3", name="Other")],
            get_permissions=lambda wid: SimpleNamespace(access_control_list=[SimpleNamespace(service_principal_name=n) for n in self.acl_after]),
        )
        self.service_principal_secrets_proxy = SimpleNamespace(create=self._create_secret, delete=lambda *a: self._delete("secret", a[-1]))
        self.token_management = SimpleNamespace(
            create_obo_token=self._token, delete=lambda i: self._delete("token", i),
            update_permissions=lambda access_control_list: self.calls.append(("token_acl", access_control_list)),
        )

    def _execute(self, statement, warehouse_id, wait_timeout):
        self.sql.append(statement)
        if self.fail_state and self.fail_state in statement:
            return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="FAILED")), result=None)
        rows = []
        if statement.startswith("SHOW CATALOGS") and self.catalog_exists:
            rows = [["compass_validation"]]
        elif statement.startswith("SHOW SCHEMAS") and self.schema != "absent":
            rows = [["compass_validation_sandbox"]]
        elif statement.startswith("DESCRIBE SCHEMA"):
            rows = [["Catalog Name", "dev"], ["Comment", TAG if self.schema == "ours" else "somebody else's schema"]]
        return SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="SUCCEEDED")), result=SimpleNamespace(data_array=rows))

    def _create_sp(self, display_name):
        self.calls.append(("sp", display_name))
        return SimpleNamespace(id="sp-id", application_id="app-uuid")

    def _create_secret(self, service_principal_id, lifetime):
        self.calls.append(("secret", service_principal_id, lifetime))
        return SimpleNamespace(id="secret-id", secret="oauth-secret-value")

    def _token(self, application_id, lifetime_seconds, comment):
        self.calls.append(("token", application_id, lifetime_seconds))
        return SimpleNamespace(token_value="dapi-secret-token", token_info=SimpleNamespace(token_id="tok-id"))

    def _delete(self, kind, ident):
        self.calls.append((f"delete_{kind}", ident))
        if self.fail_delete == kind:
            raise RuntimeError("boom")


TYPES = SimpleNamespace(
    WarehouseAccessControlRequest=lambda **kw: SimpleNamespace(**kw), WarehousePermissionLevel=SimpleNamespace(CAN_USE="CAN_USE"),
    TokenAccessControlRequest=lambda **kw: SimpleNamespace(**kw), TokenPermissionLevel=SimpleNamespace(CAN_USE="CAN_USE"),
)


def databricks_provisioner(client=None, auth="token", **kwargs):
    """The legacy access-token path by default (most tests below describe it); pass auth="oauth_m2m" for the default method."""
    client = client or Client()
    return wp.DatabricksProvisioner(client, host="dbc-host.example", warehouse_id="wh1", sql_types=TYPES, auth=auth, **kwargs), client


def test_databricks_setup_creates_a_principal_with_least_privilege_and_a_short_lived_token():
    provisioner, client = databricks_provisioner()
    result = provisioner.setup()
    config = result.config
    assert (config.catalog, config.schema, config.server_hostname, config.http_path) == ("compass_validation", "compass_validation_sandbox", "dbc-host.example", "/sql/1.0/warehouses/wh1")
    assert config.access_token == "dapi-secret-token" and "dapi-secret-token" not in repr(config)
    grants = [s for s in client.sql if s.startswith("GRANT")]
    assert [g.split(" ON ")[0] for g in grants] == ["GRANT USE CATALOG", "GRANT USE SCHEMA", "GRANT SELECT"] and all("`app-uuid`" in g for g in grants)
    acl = next(c for c in client.calls if c[0] == "acl")
    assert acl[1] == "wh1" and acl[2][0].permission_level == "CAN_USE" and acl[2][0].service_principal_name == "app-uuid"
    assert not any(s.startswith(("DROP", "ALTER", "DELETE", "UPDATE")) for s in client.sql)


def test_databricks_setup_uses_plain_create_with_the_tag_never_if_not_exists_or_replace():
    provisioner, client = databricks_provisioner()
    provisioner.setup()
    schema = next(s for s in client.sql if s.startswith("CREATE SCHEMA"))
    table = next(s for s in client.sql if s.startswith("CREATE TABLE") or s.startswith("CREATE OR REPLACE TABLE"))
    assert f"COMMENT '{TAG}'" in schema and "IF NOT EXISTS" not in schema
    assert table.startswith("CREATE TABLE") and "OR REPLACE" not in table


def test_each_run_names_its_principal_with_a_run_id_so_two_runs_never_collide():
    one, _ = databricks_provisioner()
    two, _ = databricks_provisioner()
    assert re.fullmatch(r"compass-validation-[0-9a-f]{8}", one.principal_name) and one.principal_name != two.principal_name
    pinned, client = databricks_provisioner(run_id="abc12345")
    pinned.setup()
    assert ("sp", "compass-validation-abc12345") in client.calls


@pytest.mark.parametrize(("schema", "code"), [("other", "name_taken"), ("ours", "leftovers_found")])
def test_an_existing_schema_stops_the_run_before_anything_is_created(schema, code):
    provisioner, client = databricks_provisioner(Client(catalog_exists=True, schema=schema))
    with pytest.raises(wp.ProvisioningError) as info:
        provisioner.setup()
    assert info.value.code == code and info.value.names == ("schema compass_validation.compass_validation_sandbox",)
    assert not any(s.startswith(("CREATE", "GRANT", "INSERT", "DROP")) for s in client.sql) and not [c for c in client.calls if c[0] in ("sp", "secret", "token")]
    client.sql.clear()
    assert provisioner.teardown() == 0 and client.sql == []  # and teardown does not drop the schema it never created


def test_leftover_principals_with_our_prefix_stop_the_run_and_are_named():
    old = SimpleNamespace(id="old-id", display_name="compass-validation-deadbeef")
    unrelated = SimpleNamespace(id="x", display_name="payments-service")
    provisioner, client = databricks_provisioner(Client(principals=[old, unrelated]))
    with pytest.raises(wp.ProvisioningError) as info:
        provisioner.setup()
    assert info.value.code == "leftovers_found" and info.value.names == ("service principal compass-validation-deadbeef",)
    assert not any(s.startswith("CREATE") for s in client.sql)


def test_a_schema_that_appears_after_the_check_is_not_adopted_and_is_not_dropped_by_teardown():
    """Plain CREATE SCHEMA fails if the schema exists, so the flag that lets teardown drop it is never set (the old code set it first)."""
    provisioner, client = databricks_provisioner(Client(fail_state="CREATE SCHEMA", catalog_exists=True))  # the catalog is not ours either
    with pytest.raises(wp.ProvisioningError) as info:
        provisioner.setup()
    assert info.value.code.startswith("setup_schema:")
    client.sql.clear()
    assert provisioner.teardown() == 0 and not any(s.startswith("DROP") for s in client.sql)


def test_a_catalog_this_run_created_is_dropped_even_when_the_schema_step_failed():
    provisioner, client = databricks_provisioner(Client(fail_state="CREATE SCHEMA"))  # no catalog before: this run made it
    with pytest.raises(wp.ProvisioningError):
        provisioner.setup()
    client.sql.clear()
    assert provisioner.teardown() == 1 and client.sql == ["DROP CATALOG IF EXISTS `compass_validation` CASCADE"]


def test_a_refused_warehouse_permission_and_a_refused_token_are_told_apart():
    for broken, code in (("acl", "setup_warehouse_access:"), ("token", "setup_token:")):
        provisioner, client = databricks_provisioner()
        if broken == "acl":
            client.warehouses.update_permissions = lambda *a, **k: (_ for _ in ()).throw(PermissionError("nope"))
        else:
            client.token_management.create_obo_token = lambda **k: (_ for _ in ()).throw(PermissionError("nope"))
        with pytest.raises(wp.ProvisioningError) as info:
            provisioner.setup()
        assert info.value.code == code + "PermissionError"
        assert provisioner.teardown() >= 2  # the principal and the schema are still removed


def test_the_principal_is_allowed_to_use_tokens_before_its_token_is_created():
    provisioner, client = databricks_provisioner()
    provisioner.setup()
    names = [c[0] for c in client.calls]
    assert names.index("token_acl") < names.index("token")
    acl = next(c for c in client.calls if c[0] == "token_acl")[1][0]
    assert acl.service_principal_name == "app-uuid" and acl.permission_level == "CAN_USE"


def test_databricks_teardown_removes_the_token_the_principal_the_schema_and_a_catalog_it_created():
    provisioner, client = databricks_provisioner()
    provisioner.setup()
    client.sql.clear()
    client.calls.clear()
    assert provisioner.teardown() == 4
    assert client.calls == [("delete_token", "tok-id"), ("delete_sp", "sp-id")]
    assert client.sql == ["DROP SCHEMA IF EXISTS `compass_validation`.`compass_validation_sandbox` CASCADE", "DROP CATALOG IF EXISTS `compass_validation` CASCADE"]


def test_a_catalog_that_existed_before_is_left_alone():
    provisioner, client = databricks_provisioner(Client(catalog_exists=True))
    provisioner.setup()
    client.sql.clear()
    provisioner.teardown()
    assert client.sql == ["DROP SCHEMA IF EXISTS `compass_validation`.`compass_validation_sandbox` CASCADE"]


def test_databricks_teardown_continues_past_a_failure_and_names_what_is_left():
    provisioner, client = databricks_provisioner(Client(fail_delete="token"))
    provisioner.setup()
    client.calls.clear()
    with pytest.raises(wp.ProvisioningError) as info:
        provisioner.teardown()
    assert info.value.code == "teardown_incomplete:1" and info.value.names == ("token",) and ("delete_sp", "sp-id") in client.calls


def test_a_warehouse_permission_that_survives_the_deleted_principal_is_reported_not_edited():
    provisioner, client = databricks_provisioner(Client(acl_after=["app-uuid"]))
    provisioner.setup()
    with pytest.raises(wp.ProvisioningError) as info:
        provisioner.teardown()
    assert info.value.names == ("warehouse permission for the deleted principal",)
    assert not hasattr(client.warehouses, "set_permissions")  # the check reads the warehouse's permissions and never rewrites them


def test_an_unreadable_warehouse_permission_list_is_not_claimed_as_a_leftover():
    provisioner, client = databricks_provisioner()
    provisioner.setup()
    client.warehouses.get_permissions = lambda wid: (_ for _ in ()).throw(RuntimeError("cannot read"))
    assert provisioner.teardown() >= 2


def test_a_failed_catalog_creation_leaves_nothing_to_drop_so_teardown_is_quiet():
    provisioner, client = databricks_provisioner(Client(fail_state="CREATE CATALOG"))
    with pytest.raises(wp.ProvisioningError) as info:
        provisioner.setup()
    assert info.value.code.startswith("setup_catalog:")
    client.sql.clear()
    assert provisioner.teardown() == 0 and client.sql == []


def test_a_named_existing_catalog_is_never_created_or_dropped_only_the_schema_inside_it():
    client = Client()
    provisioner = wp.DatabricksProvisioner(client, host="h", warehouse_id="wh1", catalog="workspace", sql_types=TYPES, auth="token")
    config = provisioner.setup().config
    assert config.catalog == "workspace"
    assert not any("CREATE CATALOG" in s or s.startswith("SHOW CATALOGS") for s in client.sql)
    client.sql.clear()
    provisioner.teardown()
    assert client.sql == ["DROP SCHEMA IF EXISTS `workspace`.`compass_validation_sandbox` CASCADE"]


def test_the_databricks_sweep_removes_a_tagged_schema_and_our_principals_and_never_the_catalog():
    old = SimpleNamespace(id="old-id", display_name="compass-validation-deadbeef")
    provisioner, client = databricks_provisioner(Client(catalog_exists=True, schema="ours", principals=[old]))
    result = provisioner.sweep()
    assert result.dropped == ["schema compass_validation.compass_validation_sandbox", "service principal compass-validation-deadbeef"]
    assert ("delete_sp", "old-id") in client.calls and not any("DROP CATALOG" in s for s in client.sql)


def test_the_databricks_sweep_leaves_a_schema_that_is_not_ours():
    provisioner, client = databricks_provisioner(Client(catalog_exists=True, schema="other"))
    result = provisioner.sweep()
    assert result.dropped == [] and result.skipped == ["schema compass_validation.compass_validation_sandbox"]
    assert not any(s.startswith("DROP") for s in client.sql)


def test_the_databricks_admin_signs_in_through_the_browser():
    seen = {}
    sdk = SimpleNamespace(WorkspaceClient=lambda **kwargs: seen.update(kwargs) or "client")
    assert wp.connect_databricks_admin("dbc-host.example", sdk=sdk) == "client"
    assert seen == {"host": "https://dbc-host.example", "auth_type": "external-browser"}


def test_the_warehouse_must_be_named_there_is_no_default_and_a_duplicate_name_is_refused():
    client = Client()
    assert wp.pick_warehouse(client, "Starter") == "wh1"
    for name, code in ((None, "warehouse_name_required"), ("", "warehouse_name_required"), ("missing", "no_matching_warehouse"), ("Other", "warehouse_name_ambiguous")):
        with pytest.raises(wp.ProvisioningError) as info:
            wp.pick_warehouse(client, name)
        assert info.value.code == code
    assert not hasattr(client.warehouses, "edit") and not hasattr(client.warehouses, "create")


# ------------------------------------------------------------------------------------------------ OAuth M2M (the default)


def test_oauth_m2m_is_the_default_and_creates_client_credentials_not_a_token():
    client = Client()
    provisioner = wp.DatabricksProvisioner(client, host="dbc-host.example", warehouse_id="wh1", sql_types=TYPES)
    config = provisioner.setup().config
    assert config.auth_method == "oauth_m2m" and config.client_id == "app-uuid" and config.client_secret == "oauth-secret-value"
    assert config.access_token == "" and "oauth-secret-value" not in repr(config)
    names = [c[0] for c in client.calls]
    assert "secret" in names and "token" not in names and "token_acl" not in names
    assert ("secret", "sp-id", "3600s") in client.calls


def test_the_oauth_secret_is_deleted_before_its_principal_and_the_schema_is_dropped():
    provisioner = wp.DatabricksProvisioner(client := Client(), host="h", warehouse_id="wh1", sql_types=TYPES)
    provisioner.setup()
    client.calls.clear()
    client.sql.clear()
    provisioner.teardown()
    assert client.calls == [("delete_secret", "secret-id"), ("delete_sp", "sp-id")]
    assert any(s.startswith("DROP SCHEMA") for s in client.sql)


def test_a_refused_oauth_secret_is_reported_by_step_and_teardown_still_cleans_up():
    client = Client()
    client.service_principal_secrets_proxy.create = lambda **kw: (_ for _ in ()).throw(PermissionError("nope"))
    provisioner = wp.DatabricksProvisioner(client, host="h", warehouse_id="wh1", sql_types=TYPES)
    with pytest.raises(wp.ProvisioningError) as info:
        provisioner.setup()
    assert info.value.code == "setup_secret:PermissionError"
    assert provisioner.teardown() >= 2


def test_an_unknown_auth_method_is_refused():
    with pytest.raises(ValueError):
        wp.DatabricksProvisioner(Client(), host="h", warehouse_id="wh1", auth="password")


def test_the_real_sdk_types_exist_for_the_calls_the_provisioner_makes():
    pytest.importorskip("databricks.sdk")
    types = wp._sdk_types()
    request = types.TokenAccessControlRequest(service_principal_name="x", permission_level=types.TokenPermissionLevel.CAN_USE)
    assert request.service_principal_name == "x" and types.WarehousePermissionLevel.CAN_USE


# --------------------------------------------------------------------------------------------- the provisioned run


class FakeProvisioner:
    def __init__(self, config, *, setup_error=None, teardown_error=None, sweep_result=None):
        self.config, self.setup_error, self.teardown_error = config, setup_error, teardown_error
        self.sweep_result = sweep_result or wp.SweepResult()
        self.torn_down = self.closed = False

    def setup(self):
        if self.setup_error:
            raise self.setup_error
        return wp.Provisioned(self.config, 14)

    def teardown(self):
        self.torn_down = True
        if self.teardown_error:
            raise self.teardown_error
        return 5

    def sweep(self):
        return self.sweep_result

    def close(self):
        self.closed = True


SF_CONFIG = SnowflakeConfig(
    account="org-acct", user="u", warehouse="W", database="COMPASS_VALIDATION", schema="SANDBOX", role="COMPASS_VALIDATION_RO",
    private_key_der=b"\x30secret-der-bytes",
)
DB_CONFIG = DatabricksConfig(
    server_hostname="dbc-host.example", http_path="/p", catalog="compass_validation", schema="compass_validation_sandbox", access_token="dapi-secret-token"
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
    provisioner = FakeProvisioner(SF_CONFIG, setup_error=wp.ProvisioningError("setup_step_4:RuntimeError"))
    code, out = run_provisioned(monkeypatch, tmp_path, "snowflake", provisioner)
    assert code == 1 and provisioner.torn_down
    assert "setup failed: setup_step_4:RuntimeError" in out.read_text()


@pytest.mark.parametrize(("code_name", "advice"), [("name_taken", "not ours"), ("leftovers_found", "--sweep")])
def test_a_name_collision_is_reported_with_the_names_and_what_to_do_and_nothing_was_changed(monkeypatch, tmp_path, capsys, code_name, advice):
    error = wp.ProvisioningError(code_name, ["database COMPASS_VALIDATION", "role COMPASS_VALIDATION_RO"])
    code, out = run_provisioned(monkeypatch, tmp_path, "snowflake", FakeProvisioner(SF_CONFIG, setup_error=error))
    text, err = out.read_text(), capsys.readouterr().err
    assert code == 1 and f"setup failed: {code_name} (database COMPASS_VALIDATION; role COMPASS_VALIDATION_RO)" in text and advice in err


def test_an_incomplete_teardown_fails_the_run_and_names_exactly_what_is_left(monkeypatch, tmp_path, capsys):
    error = wp.ProvisioningError("teardown_incomplete:2", ["warehouse COMPASS_VALIDATION_WH", "resource monitor COMPASS_VALIDATION_RM"])
    code, out = run_provisioned(monkeypatch, tmp_path, "snowflake", FakeProvisioner(SF_CONFIG, teardown_error=error))
    assert code == 1 and "TEARDOWN INCOMPLETE: teardown_incomplete:2 (left: warehouse COMPASS_VALIDATION_WH; resource monitor COMPASS_VALIDATION_RM)" in out.read_text()
    err = capsys.readouterr().err
    assert "warehouse COMPASS_VALIDATION_WH; resource monitor COMPASS_VALIDATION_RM" in err and "--sweep" in err


def test_snowflake_provisioning_needs_an_admin_user_and_refuses_before_any_sign_in(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(wv, "connect_snowflake_admin", lambda *a, **k: pytest.fail("signed in"))
    code = wv.main(["snowflake", "--allow-live", "--provision", "--expect-target", "org-acct", "--out", str(tmp_path / "r.md")], {})
    assert code == 2 and "--admin-user" in capsys.readouterr().err


def test_databricks_provisioning_needs_a_named_warehouse_and_refuses_before_any_sign_in(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(wv, "connect_databricks_admin", lambda *a, **k: pytest.fail("signed in"))
    code = wv.main(["databricks", "--allow-live", "--provision", "--expect-target", "h", "--out", str(tmp_path / "r.md")], {})
    assert code == 2 and "--databricks-warehouse" in capsys.readouterr().err


def test_the_admin_role_is_configurable_and_passed_to_the_sign_in(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(wv, "connect_snowflake_admin", lambda account, user, role: seen.update(role=role) or AdminConn())
    wv.main(["snowflake", "--allow-live", "--provision", "--expect-target", "org-acct", "--admin-user", "me", "--admin-role", "SYSADMIN", "--out", str(tmp_path / "r.md")], {})
    assert seen["role"] == "SYSADMIN"


def test_provisioning_still_needs_the_live_flag_and_a_named_target(tmp_path):
    assert wv.main(["databricks", "--provision", "--expect-target", "h", "--out", str(tmp_path / "r.md")], {}) == 2
    assert wv.main(["databricks", "--provision", "--allow-live", "--out", str(tmp_path / "r.md")], {}) == 2


def test_a_report_path_is_required_except_for_a_sweep(tmp_path, capsys, monkeypatch):
    assert wv.main(["snowflake", "--allow-live", "--provision", "--expect-target", "org-acct", "--admin-user", "me"], {}) == 2
    assert "--out" in capsys.readouterr().err


def test_a_sweep_reports_what_it_removed_and_what_it_left_alone(monkeypatch, capsys):
    result = wp.SweepResult(dropped=["warehouse COMPASS_VALIDATION_WH"], skipped=["role COMPASS_VALIDATION_RO"])
    monkeypatch.setattr(wv, "make_provisioner", lambda args: FakeProvisioner(SF_CONFIG, sweep_result=result))
    code = wv.main(["snowflake", "--allow-live", "--sweep", "--expect-target", "org-acct", "--admin-user", "me"], {})
    captured = capsys.readouterr()
    assert code == 1 and "Removed: warehouse COMPASS_VALIDATION_WH" in captured.out and "role COMPASS_VALIDATION_RO" in captured.err


def test_a_sweep_with_nothing_to_do_succeeds_and_still_needs_the_live_guards(monkeypatch, capsys):
    monkeypatch.setattr(wv, "make_provisioner", lambda args: FakeProvisioner(SF_CONFIG))
    assert wv.main(["snowflake", "--allow-live", "--sweep", "--expect-target", "org-acct", "--admin-user", "me"], {}) == 0
    assert "Removed: nothing" in capsys.readouterr().out
    assert wv.main(["snowflake", "--sweep", "--expect-target", "org-acct"], {}) == 2  # no --allow-live


def test_the_runtime_api_never_imports_the_admin_tool():
    import pathlib

    app = pathlib.Path(wp.__file__).parents[1]
    importers = [p.name for p in app.rglob("*.py") if "warehouse_provisioning" in p.read_text() and p.name not in {"warehouse_provisioning.py", "warehouse_validation.py"}]
    assert importers == []


def test_the_module_does_not_claim_more_than_was_proven():
    source = open(wp.__file__).read()
    assert "Not run against a live account" not in source  # the stale line the security review found
    assert "NOT verified live" in source and "never changes a warehouse's settings" in source


def test_a_failed_admin_sign_in_is_reported_by_type_only_before_anything_runs(tmp_path, monkeypatch, capsys):
    def sign_in(args):
        raise ConnectionError("SSO page said secret-detail")

    monkeypatch.setattr(wv, "make_provisioner", sign_in)
    code = wv.main(["databricks", "--allow-live", "--provision", "--expect-target", "h", "--out", str(tmp_path / "r.md")], {})
    err = capsys.readouterr().err
    assert code == 2 and "ConnectionError" in err and "secret-detail" not in err and not (tmp_path / "r.md").exists()
