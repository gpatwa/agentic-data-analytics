"""The Databricks connect API and secret custody, against fakes: no workspace, no network."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.connect.api import MANAGER_GROUP
from app.connect.databricks_api import (
    DatabricksRuntime,
    DatabricksSetups,
    build_databricks_router,
    make_session_factory,
)
from app.connect.store import ConnectionRecord, ConnectionStore, TenantSecretStore
from app.security import AuthenticationError
from app.security.audit import AuditChain
from packages.platform_contracts.security import AnalyticsIdentity

HOST = "dbc-1234abcd-5678.cloud.databricks.com"
APP_ID = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
SECRET = "dose-OAUTH-secret-value-0123456789-do-not-leak"
PATH = "/sql/1.0/warehouses/abcdef0123456789"
BODY = {"host": HOST, "catalog": "sales", "warehouse_name": "Starter", "tables": [{"schema": "data", "table": "orders"}, {"schema": "data", "table": "customers"}]}
IDENT = {"mgr-a": AnalyticsIdentity(tenant_id="ta", user_id="lead-a", groups=[MANAGER_GROUP]),
         "usr-a": AnalyticsIdentity(tenant_id="ta", user_id="analyst", groups=[]),
         "mgr-b": AnalyticsIdentity(tenant_id="tb", user_id="lead-b", groups=[MANAGER_GROUP])}


class Verifier:
    def verify(self, token):
        try:
            return IDENT[token]
        except KeyError as exc:
            raise AuthenticationError("bad") from exc


class Stream:
    def fetchmany(self, n):
        return [(1,)]

    def close(self):
        pass


class Session:
    def __init__(self, fail_on=None):
        self.fail_on, self.sql, self.closed = fail_on, [], False

    def execute(self, sql, params):
        self.sql.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError(f"PERMISSION_DENIED {SECRET}")  # a driver message that would leak if echoed
        return Stream()

    def close(self):
        self.closed = True


def build(fail_on=None, factory=None):
    store, secrets, chain = ConnectionStore(), TenantSecretStore(), AuditChain()
    setups = DatabricksSetups(store, secrets, chain)
    sessions: list[Session] = []

    def default_factory(record: ConnectionRecord):
        assert secrets.read(record.tenant_id, record.secret_name) == SECRET  # the session reads the secret from custody
        s = Session(fail_on)
        sessions.append(s)
        return s

    runtime = DatabricksRuntime(setups, Verifier(), None, factory or default_factory, dns=lambda h: ["34.1.2.3"])
    app = FastAPI()
    app.include_router(build_databricks_router(runtime, lambda: None))
    return TestClient(app), setups, store, secrets, chain, sessions


def auth(token="mgr-a"):
    return {"Authorization": f"Bearer {token}"}


def begin(client, token="mgr-a"):
    r = client.post("/api/v2/connect/databricks/setups", json=BODY, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()


def finish(client, view, **over):
    body = {"application_id": APP_ID, "client_secret": SECRET, "http_path": PATH, **over}
    return client.post(f"/api/v2/connect/databricks/setups/{view['setup_id']}/credentials", json=body, headers=auth())


# ---- the script ----------------------------------------------------------------------------------------------------------------


def test_begin_returns_the_script_with_a_placeholder_until_the_application_id_is_given():
    client, *_ = build()
    view = begin(client)
    assert view["principal_name"].startswith("compass-conn-") and view["tables"] == ["data.customers", "data.orders"]
    assert any(s["kind"] == "sql" and "<APPLICATION_ID>" in s["text"] for s in view["steps"])
    r = client.post(f"/api/v2/connect/databricks/setups/{view['setup_id']}/script", json={"application_id": APP_ID}, headers=auth())
    sql = [s["text"] for s in r.json()["steps"] if s["kind"] == "sql"]
    assert r.status_code == 200 and all(APP_ID in s and "<APPLICATION_ID>" not in s for s in sql)
    assert not any("MANAGE" in s or "ALL PRIVILEGES" in s or "MODIFY" in s for s in sql)


@pytest.mark.parametrize("override", [{"host": "evil.example.com"}, {"host": "10.0.0.1"}, {"catalog": "a`b"}, {"tables": [{"schema": "d", "table": "x*"}]}])
def test_a_bad_host_or_scope_is_refused_before_anything_exists(override):
    client, setups, *_ = build()
    r = client.post("/api/v2/connect/databricks/setups", json={**BODY, **override}, headers=auth())
    assert r.status_code == 422 and not setups._pending


def test_only_a_connection_manager_acts_and_another_tenant_cannot_see_the_setup():
    client, *_ = build()
    assert client.post("/api/v2/connect/databricks/setups", json=BODY, headers=auth("usr-a")).status_code == 403
    assert client.post("/api/v2/connect/databricks/setups", json=BODY).status_code == 401
    view = begin(client)
    r = client.post(f"/api/v2/connect/databricks/setups/{view['setup_id']}/script", json={"application_id": APP_ID}, headers=auth("mgr-b"))
    assert r.status_code == 404


# ---- custody -------------------------------------------------------------------------------------------------------------------


def test_the_secret_goes_to_custody_the_connection_goes_active_and_nothing_returns_the_secret(caplog):
    caplog.set_level(logging.DEBUG)
    client, setups, store, secrets, chain, sessions = build()
    view = begin(client)
    r = finish(client, view)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["connection_status"] == "active"
    record = store.get("ta", view["connection_id"])
    assert record.auth_method == "oauth_m2m" and record.status == "active" and record.user == APP_ID and record.http_path == PATH
    assert secrets.read("ta", record.secret_name) == SECRET and record.secret_name == f"databricks/{view['connection_id']}"
    everything = r.text + json.dumps(view) + repr(record) + json.dumps([e.payload for e in chain.events]) + caplog.text + repr(secrets)
    assert SECRET not in everything and sessions[0].closed
    assert [e.event_type for e in chain.events] == ["connect.consent_shown", "connect.script_run_reported", "connect.verified"]
    assert not setups._pending  # the setup is finished


def test_a_failed_connection_test_deletes_the_secret_and_removes_the_connection_without_echoing_the_driver_message():
    client, setups, store, secrets, chain, _ = build(fail_on="`customers`")
    view = begin(client)
    r = finish(client, view)
    body = r.json()
    assert r.status_code == 200 and body["ok"] is False and body["code"] == "connection_test_failed" and body["connection_status"] == "removed"
    assert {c["table"]: c["passed"] for c in body["checks"]} == {"sales.data.customers": False, "sales.data.orders": True}
    assert not secrets.exists("ta", f"databricks/{view['connection_id']}") and store.get("ta", view["connection_id"]).status == "removed"
    assert SECRET not in r.text and "PERMISSION_DENIED" not in r.text
    assert "connect.credential_deleted" in [e.event_type for e in chain.events]


def test_a_session_that_cannot_open_fails_closed_and_deletes_the_secret():
    def boom(record):
        raise RuntimeError(SECRET)

    client, setups, store, secrets, *_ = build(factory=boom)
    view = begin(client)
    r = finish(client, view)
    assert r.json()["ok"] is False and not secrets.exists("ta", f"databricks/{view['connection_id']}") and SECRET not in r.text


@pytest.mark.parametrize("over", [{"application_id": "not-a-uuid"}, {"http_path": "/etc/passwd"}, {"client_secret": "short"}, {"client_secret": "has a space in it 0123456789"}])
def test_bad_input_is_refused_by_code_before_custody(over):
    client, _setups, store, secrets, *_ = build()
    view = begin(client)
    r = finish(client, view, **over)
    assert r.status_code == 422 and not store.list("ta") and secrets._values == {}
    assert SECRET not in r.text


def test_a_malformed_body_never_echoes_the_secret_in_the_error():
    client, *_ = build()
    view = begin(client)
    r = client.post(f"/api/v2/connect/databricks/setups/{view['setup_id']}/credentials", json={"client_secret": SECRET}, headers=auth())
    assert r.status_code == 422 and SECRET not in r.text and r.json() == {"detail": "invalid_request"}


def test_removal_deletes_the_credential_and_returns_the_admins_removal_statements():
    client, setups, store, secrets, chain, _ = build()
    view = begin(client)
    finish(client, view)
    r = client.delete(f"/api/v2/connect/databricks/connections/{view['connection_id']}", headers=auth())
    body = r.json()
    assert r.status_code == 200 and body["credential_deleted"] and len(body["teardown"]) == 5 and body["teardown"][-1].startswith("delete the service principal") and all(t.startswith("REVOKE") for t in body["teardown"][:-1])
    assert not secrets.exists("ta", f"databricks/{view['connection_id']}") and store.get("ta", view["connection_id"]).status == "removed"
    assert client.delete(f"/api/v2/connect/databricks/connections/{view['connection_id']}", headers=auth("mgr-b")).status_code == 404


def test_the_connection_is_listed_and_tenant_scoped_in_the_store():
    client, setups, store, *_ = build()
    view = begin(client)
    finish(client, view)
    assert [r.connection_id for r in store.list("ta")] == [view["connection_id"]] and store.list("tb") == []


def test_the_session_factory_reads_the_secret_only_when_it_opens_a_session():
    secrets = TenantSecretStore()
    secrets.put("ta", "databricks/x", SECRET)
    seen = {}

    class Driver:
        @staticmethod
        def connect(**options):
            seen.update(options)
            return SimpleConn()

    class SimpleConn:
        def close(self):
            pass

    class Core:
        class Config:
            def __init__(self, **kw):
                seen["config"] = kw

        @staticmethod
        def oauth_service_principal(cfg):
            return lambda: {}

    record = ConnectionRecord(tenant_id="ta", connection_id="x", engine="databricks", host=HOST, account=HOST, user=APP_ID, role="", warehouse="w",
                              database="sales", default_schema="data", allowed_tables=("sales.data.orders",), auth_method="oauth_m2m",
                              secret_name="databricks/x", http_path=PATH)
    make_session_factory(secrets, connector=Driver, sdk_core=Core)(record)
    seen["credentials_provider"]()  # the SDK config is built lazily, when the driver asks for a token
    assert seen["config"]["client_secret"] == SECRET and seen["config"]["client_id"] == APP_ID


def test_a_connection_record_rules_for_oauth_m2m():
    base = dict(tenant_id="ta", connection_id="x", engine="databricks", host=HOST, account=HOST, user=APP_ID, role="", warehouse="w",
                database="sales", default_schema="data", allowed_tables=("sales.data.orders",), auth_method="oauth_m2m", secret_name="databricks/x")
    ConnectionRecord(**base)
    for bad in ({"secret_name": None}, {"engine": "snowflake"}, {"user": ""}):
        with pytest.raises(ValueError):
            ConnectionRecord(**{**base, **bad})


def test_a_workspace_another_tenant_already_connected_is_refused_and_leaves_no_secret():
    client, setups, store, secrets, *_ = build()
    store.add(ConnectionRecord(tenant_id="tb", connection_id="other", engine="databricks", host=HOST, account=HOST, user=APP_ID, role="", warehouse="w",
                               database="sales", default_schema="data", allowed_tables=("sales.data.orders",), auth_method="oauth_m2m",
                               secret_name="databricks/other", http_path=PATH))
    view = begin(client)
    r = finish(client, view)
    assert r.status_code == 409 and r.json()["detail"] == "target_claimed" and not secrets.exists("ta", f"databricks/{view['connection_id']}")
