"""A connected Databricks workspace gets a query gateway built from its own record: tenant-scoped, active only, allowlist exactly
the admin's tables, OAuth M2M with the secret read from custody. Fakes only: no workspace, no driver."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.connect.gateway import ConnectionUnavailable, databricks_gateway_for, gateway_for
from app.connect.store import ConnectionRecord, ConnectionStore, TenantSecretStore
from app.execution import DatabricksGateway, ExecutionLimits, QueryRejected
from app.execution.warehouse_sessions import databricks_gateway as real_builder

HOST = "dbc-1234abcd-5678.cloud.databricks.com"
APP = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
SECRET = "dose-OAUTH-secret-value-0123456789-do-not-leak"
PATH = "/sql/1.0/warehouses/abcdef0123456789"


def record(**over):
    base = dict(tenant_id="t1", connection_id="c1", engine="databricks", host=HOST, account=HOST, user=APP, role="", warehouse="Starter",
                database="sales", default_schema="data", allowed_tables=("sales.data.orders", "sales.data.customers"),
                auth_method="oauth_m2m", secret_name="databricks/c1", http_path=PATH, status="active")
    return ConnectionRecord(**{**base, **over})


def wired(**over):
    store, secrets = ConnectionStore(), TenantSecretStore()
    secrets.put("t1", "databricks/c1", SECRET)
    store.add(record(**over))
    return store, secrets


class Stream:
    columns = ("n",)

    def fetchmany(self, size):
        return []

    def close(self):
        pass


class Session:
    def __init__(self):
        self.ran = []

    def prepare(self, *, timeout_seconds):
        pass

    def explain_cost(self, sql, parameters):
        return 1.0

    def execute(self, sql, parameters):
        self.ran.append(sql)
        return Stream()

    def cancel(self):
        pass

    def close(self):
        pass


def test_the_config_comes_from_the_record_and_custody_and_never_shows_the_secret(monkeypatch):
    seen = {}

    def fake(config, *, allowed_tables, connector=None, **options):
        seen.update(config=config, tables=tuple(allowed_tables))
        return "gateway"

    monkeypatch.setattr("app.connect.gateway.databricks_gateway", fake)
    store, secrets = wired()
    assert databricks_gateway_for(store, secrets, "t1", "c1") == "gateway"
    config = seen["config"]
    assert seen["tables"] == ("sales.data.orders", "sales.data.customers")
    assert (config.server_hostname, config.http_path, config.catalog, config.client_id, config.auth_method) == (HOST, PATH, "sales", APP, "oauth_m2m")
    assert config.client_secret == SECRET and SECRET not in repr(config)


@pytest.mark.parametrize(("tenant", "over", "code"), [
    ("t2", {}, "connection_not_found"),
    ("t1", {"status": "pending"}, "connection_not_active"),
    ("t1", {"status": "removed"}, "connection_not_active"),
    ("t1", {"http_path": ""}, "engine_not_supported"),
])
def test_an_unavailable_connection_gives_a_stable_code(tenant, over, code):
    store, secrets = wired(**over)
    with pytest.raises(ConnectionUnavailable) as caught:
        databricks_gateway_for(store, secrets, tenant, "c1")
    assert caught.value.code == code


def test_a_snowflake_record_is_not_served_by_the_databricks_builder():
    store, secrets = wired(engine="snowflake", auth_method="key_pair", user="U", role="R", http_path="")
    with pytest.raises(ConnectionUnavailable) as caught:
        databricks_gateway_for(store, secrets, "t1", "c1")
    assert caught.value.code == "engine_not_supported"


def test_a_missing_credential_is_one_stable_code_that_never_says_why():
    store, secrets = wired()
    secrets.delete("t1", "databricks/c1")
    with pytest.raises(ConnectionUnavailable) as caught:
        databricks_gateway_for(store, secrets, "t1", "c1")
    assert caught.value.code == "credential_unavailable" and SECRET not in str(caught.value)


def test_the_real_gateway_allows_only_the_admins_tables_and_only_reads(monkeypatch):
    session = Session()
    monkeypatch.setattr("app.connect.gateway.databricks_gateway",
                        lambda config, *, allowed_tables, connector=None, **o: DatabricksGateway(lambda: session, allowed_tables=allowed_tables))
    store, secrets = wired()
    gateway = databricks_gateway_for(store, secrets, "t1", "c1")
    limits = ExecutionLimits(timeout_seconds=5, max_rows=10, max_cost_units=100)

    def plan(sql):
        return SimpleNamespace(dialect="databricks", sql=sql, parameters={})

    gateway.execute(plan("SELECT count(*) FROM sales.data.orders"), limits=limits)
    assert session.ran
    for bad in ("SELECT * FROM sales.data.payroll_outside", "SELECT * FROM other.data.orders", "DROP TABLE sales.data.orders",
                "INSERT INTO sales.data.orders VALUES (1, 2)"):
        with pytest.raises(QueryRejected):
            gateway.execute(plan(bad), limits=limits)
    assert len(session.ran) == 1  # nothing rejected reached the warehouse


def test_the_dispatcher_picks_the_gateway_by_engine(monkeypatch):
    monkeypatch.setattr("app.connect.gateway.databricks_gateway", lambda config, *, allowed_tables, connector=None, **o: "dbx")
    monkeypatch.setattr("app.connect.gateway.snowflake_gateway", lambda config, *, allowed_tables, connector=None, **o: "sf")
    store, secrets = wired()
    assert gateway_for(store, secrets, "t1", "c1") == "dbx"
    import base64
    s2, k2 = ConnectionStore(), TenantSecretStore()
    k2.put("t1", "key-1", base64.b64encode(b"der").decode())
    s2.add(record(engine="snowflake", auth_method="key_pair", user="U", role="R", http_path="", secret_name="key-1"))
    assert gateway_for(s2, k2, "t1", "c1") == "sf"
    with pytest.raises(ConnectionUnavailable) as caught:
        gateway_for(store, secrets, "t2", "c1")
    assert caught.value.code == "connection_not_found"


def test_the_real_builder_makes_a_databricks_gateway_without_opening_a_connection():
    from app.execution.warehouse_sessions import DatabricksConfig

    gateway = real_builder(DatabricksConfig(HOST, PATH, "sales", "data", client_id=APP, client_secret=SECRET), allowed_tables=("sales.data.orders",))
    assert isinstance(gateway, DatabricksGateway)  # sessions open lazily, per query
