"""The `/api/v2/connect` endpoints (CM4), against fakes: a fake identity verifier, a fake service-identity session, no network.

The fake session answers `SHOW GRANTS` and `DESCRIBE TABLE` from whatever grants the test says the admin ran, so the
connection test is exercised against state rather than canned answers.
"""

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.test_connect_setup_worker import Clock, key_pair

from app.connect.api import MANAGER_GROUP, ConnectRuntime, build_connect_router
from app.connect.approval import ApprovalGate
from app.connect.gateway import ConnectionUnavailable, snowflake_gateway_for
from app.connect.store import ConnectionStore, TenantSecretStore
from app.connect.worker import SCRIPT_WINDOW_SECONDS, SetupCoordinator
from app.security import AuthenticationError
from app.security.audit import AuditChain
from packages.platform_contracts.security import AnalyticsIdentity

HOST = "acme.snowflakecomputing.com"
BODY = {
    "host": HOST,
    "account": "acme",
    "database": "ANALYTICS",
    "tables": [{"schema": "PUBLIC", "table": "ORDERS"}, {"schema": "PUBLIC", "table": "CUSTOMERS"}],
}
IDENTITIES = {
    "mgr-a": AnalyticsIdentity(tenant_id="ta", user_id="lead-a", groups=[MANAGER_GROUP]),
    "usr-a": AnalyticsIdentity(tenant_id="ta", user_id="analyst-a", groups=[]),
    "mgr-b": AnalyticsIdentity(tenant_id="tb", user_id="lead-b", groups=[MANAGER_GROUP]),
}


class Verifier:
    def verify(self, token):
        try:
            return IDENTITIES[token]
        except KeyError as exc:
            raise AuthenticationError("bad token") from exc


class Stream:
    def __init__(self, columns, rows):
        self.columns, self._rows = tuple(columns), list(rows)

    def fetchmany(self, size):
        out, self._rows = self._rows[:size], self._rows[size:]
        return out

    def close(self):
        pass


class AdminRanScript:
    """A service-identity session over a warehouse where `grants` is what the admin actually ran."""

    def __init__(self, state):
        self.state = state

    def prepare(self, *, timeout_seconds):
        pass

    def execute(self, sql, parameters):
        if sql.startswith("SHOW GRANTS TO ROLE"):
            rows = [(g["privilege"], g["granted_on"], g["name"], g["grant_option"]) for g in self.state["grants"]]
            return Stream(("privilege", "granted_on", "name", "grant_option"), rows)
        if sql.startswith("DESCRIBE TABLE"):
            return Stream(("name",), [("ID",)] if self.state["describe"] else [])
        raise AssertionError(sql)

    def close(self):
        self.state["closed"] = self.state.get("closed", 0) + 1


def grants_from(statements):
    rows = []
    for s in statements:
        m = re.match(r"GRANT (USAGE|SELECT) ON (WAREHOUSE|DATABASE|SCHEMA|TABLE) (.+) TO ROLE", s)
        if m:
            rows.append({"privilege": m[1], "granted_on": m[2], "name": m[3], "grant_option": "false"})
    return rows


def make(configured=True, session_factory="default"):
    clock = Clock()
    store, secrets, chain = ConnectionStore(), TenantSecretStore(), AuditChain()
    gate = ApprovalGate(clock=clock)
    counter = iter(range(1000))
    coord = SetupCoordinator(
        store, secrets, chain, gate, clock=clock, key_pair=key_pair, id_source=lambda: f"{next(counter):08x}" + "0" * 24
    )
    state = {"grants": [], "describe": True}
    factory = (lambda record: AdminRanScript(state)) if session_factory == "default" else session_factory
    runtime = ConnectRuntime(
        coordinator=coord if configured else None,
        store=store if configured else None,
        verifier=Verifier() if configured else None,
        session_factory=factory,
        dns=lambda host: ["54.230.10.4"],
        allowed_networks=("35.238.115.103/32",),
    )

    async def no_key():
        return None

    app = FastAPI()
    app.include_router(build_connect_router(runtime, no_key))
    return TestClient(app), runtime, state, clock, secrets, chain


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def start(client, token="mgr-a", body=BODY):
    return client.post("/api/v2/connect/setups", json=body, headers=auth(token))


# ---- gating --------------------------------------------------------------------------------------------------------


def test_every_call_is_503_until_a_runtime_is_configured():
    client, *_ = make(configured=False)
    for method, path in (("get", "/capabilities"), ("post", "/setups"), ("get", "/connections")):
        assert getattr(client, method)(f"/api/v2/connect{path}", headers=auth("mgr-a")).status_code == 503


def test_the_main_app_mounts_it_inert():
    from app import main

    assert TestClient(main.app).get("/api/v2/connect/capabilities").status_code == 503


def test_a_bearer_token_is_required_and_only_managers_can_change_things():
    client, *_ = make()
    assert client.get("/api/v2/connect/capabilities").status_code == 401
    assert client.get("/api/v2/connect/capabilities", headers=auth("nope")).status_code == 401
    denied = start(client, "usr-a")
    assert denied.status_code == 403 and denied.json()["detail"] == "connection_manager_required"
    assert client.get("/api/v2/connect/capabilities", headers=auth("usr-a")).json()["can_manage"] is False


def test_capabilities_are_honest_about_stage_and_method():
    client, *_ = make()
    body = client.get("/api/v2/connect/capabilities", headers=auth("mgr-a")).json()
    snowflake = body["engines"][0]
    assert snowflake["engine"] == "snowflake" and "internal testing" in snowflake["stage"]
    assert "security review" in snowflake["stage"]
    assert snowflake["setup_methods"] == ["script"]
    assert snowflake["product_run_setup"] == {"available": False, "reason": "admin_sign_in_not_built"}
    assert [e["engine"] for e in body["engines"]] == ["snowflake"]  # Databricks is not offered


# ---- starting a setup ----------------------------------------------------------------------------------------------


def test_starting_a_setup_returns_the_exact_script_and_no_key_material():
    client, *_ = make()
    response = start(client)
    assert response.status_code == 201
    view = response.json()
    assert view["status"] == "awaiting_admin" and view["method"] == "script"
    assert view["tables"] == ["analytics.public.customers", "analytics.public.orders"]
    assert any("read-only" in line for line in view["summary"])
    assert any(s.startswith("GRANT SELECT ON TABLE") for s in view["statements"])
    assert view["teardown"][0].startswith("DROP USER")
    assert any("ALLOWED_IP_LIST = ('35.238.115.103/32')" in s for s in view["statements"])  # Compass's own addresses
    text = json.dumps(view)
    for leaked in ("fake-private-key", "PRIVATE KEY", "secret_name", "snowflake-key-"):
        assert leaked not in text


@pytest.mark.parametrize(
    "patch,status,code",
    [
        ({"host": "169.254.169.254"}, 422, "ip_address_not_allowed"),
        ({"host": "evil.example"}, 422, "not_a_vendor_domain"),
        ({"account": "someoneelse"}, 422, "account_mismatch"),
        ({"tables": [{"schema": "PUBLIC", "table": "*"}]}, 422, "invalid_scope"),
        ({"tables": [{"schema": "PUBLIC", "table": "T"}, {"schema": "PUBLIC", "table": "T"}]}, 422, "invalid_scope"),
    ],
)
def test_bad_requests_get_a_short_stable_code(patch, status, code):
    client, *_ = make()
    response = start(client, body={**BODY, **patch})
    assert response.status_code == status and response.json()["detail"] == code


def test_validation_errors_for_missing_fields_and_limits():
    client, *_ = make()
    assert start(client, body={**BODY, "tables": []}).status_code == 422
    assert start(client, body={**BODY, "credit_quota": 0}).status_code == 422


def test_a_host_another_tenant_holds_is_refused():
    client, *_ = make()
    assert start(client, "mgr-a").status_code == 201
    other = start(client, "mgr-b")
    assert other.status_code == 409 and other.json()["detail"] == "target_claimed"


# ---- tenants cannot see each other ---------------------------------------------------------------------------------


def test_another_tenant_cannot_read_verify_cancel_or_remove():
    client, *_ = make()
    setup = start(client).json()
    sid = setup["setup_id"]
    for call in (
        client.get(f"/api/v2/connect/setups/{sid}", headers=auth("mgr-b")),
        client.post(f"/api/v2/connect/setups/{sid}/verify", headers=auth("mgr-b")),
        client.post(f"/api/v2/connect/setups/{sid}/cancel", headers=auth("mgr-b")),
    ):
        assert call.status_code == 404 and call.json()["detail"] == "setup_not_found"
    missing = client.get("/api/v2/connect/setups/does-not-exist", headers=auth("mgr-b"))
    assert missing.json() == client.get(f"/api/v2/connect/setups/{sid}", headers=auth("mgr-b")).json()
    assert client.get("/api/v2/connect/connections", headers=auth("mgr-b")).json() == []
    assert client.delete(f"/api/v2/connect/connections/{setup['connection_id']}", headers=auth("mgr-b")).status_code == 404
    assert client.get(f"/api/v2/connect/setups/{sid}", headers=auth("usr-a")).status_code == 200  # same tenant may read


# ---- verifying -----------------------------------------------------------------------------------------------------


def test_the_connection_test_passes_once_the_admin_has_run_the_script():
    client, _, state, _, _, chain = make()
    setup = start(client).json()
    state["grants"] = grants_from(setup["statements"])
    response = client.post(f"/api/v2/connect/setups/{setup['setup_id']}/verify", headers=auth("mgr-a"))
    body = response.json()
    assert response.status_code == 200 and body["ok"] is True and body["code"] is None
    assert body["connection_status"] == "active"
    assert [(c["name"], c["passed"]) for c in body["checks"]] == [("read_only_grants", True), ("metadata_read", True)]
    assert any("sensitive" in line.lower() for line in body["not_run_on_customer_data"])
    assert state["closed"] == 1
    listed = client.get("/api/v2/connect/connections", headers=auth("usr-a")).json()
    assert listed[0]["status"] == "active" and listed[0]["auth_method"] == "key_pair" and "secret" not in json.dumps(listed)
    assert client.get(f"/api/v2/connect/setups/{setup['setup_id']}", headers=auth("mgr-a")).status_code == 404  # no longer pending
    assert [e.event_type for e in chain.events] == ["connect.consent_shown", "connect.script_run_reported", "connect.verified"]
    assert chain.verify()


def test_a_failed_test_names_the_check_and_can_be_retried_after_the_admin_fixes_it():
    client, _, state, _, _, _ = make()
    setup = start(client).json()
    first = client.post(f"/api/v2/connect/setups/{setup['setup_id']}/verify", headers=auth("mgr-a")).json()
    assert first["ok"] is False and first["code"] == "connection_test_failed" and first["connection_status"] == "pending"
    assert [c["name"] for c in first["checks"] if not c["passed"]] == ["read_only_grants"]
    state["grants"] = grants_from(setup["statements"])
    again = client.post(f"/api/v2/connect/setups/{setup['setup_id']}/verify", headers=auth("mgr-a")).json()
    assert again["ok"] is True and again["connection_status"] == "active"


def test_extra_grants_fail_the_read_only_check():
    client, _, state, *_ = make()
    setup = start(client).json()
    extra = {"privilege": "INSERT", "granted_on": "TABLE", "name": 'ANALYTICS.PUBLIC."ORDERS"', "grant_option": "false"}
    state["grants"] = [*grants_from(setup["statements"]), extra]
    body = client.post(f"/api/v2/connect/setups/{setup['setup_id']}/verify", headers=auth("mgr-a")).json()
    assert body["ok"] is False and "read_only_grants" in [c["name"] for c in body["checks"] if not c["passed"]]


def test_unreadable_tables_fail_the_metadata_check():
    client, _, state, *_ = make()
    setup = start(client).json()
    state.update(grants=grants_from(setup["statements"]), describe=False)
    body = client.post(f"/api/v2/connect/setups/{setup['setup_id']}/verify", headers=auth("mgr-a")).json()
    assert [c["name"] for c in body["checks"] if not c["passed"]] == ["metadata_read"]


def test_a_session_that_cannot_connect_fails_both_checks_without_echoing_why():
    def broken(record):
        raise RuntimeError("could not connect with SECRET-KEY-VALUE")

    client, *_ = make(session_factory=broken)
    setup = start(client).json()
    response = client.post(f"/api/v2/connect/setups/{setup['setup_id']}/verify", headers=auth("mgr-a"))
    assert response.status_code == 200 and not response.json()["ok"]
    assert all(not c["passed"] for c in response.json()["checks"])
    assert "SECRET-KEY-VALUE" not in response.text


def test_verify_needs_a_session_factory():
    client, *_ = make(session_factory=None)
    setup = start(client).json()
    assert client.post(f"/api/v2/connect/setups/{setup['setup_id']}/verify", headers=auth("mgr-a")).status_code == 503


# ---- cancel, lapse and remove --------------------------------------------------------------------------------------


def test_cancel_deletes_the_credential_and_the_pending_setup():
    client, _, _, _, secrets, _ = make()
    setup = start(client).json()
    assert client.post(f"/api/v2/connect/setups/{setup['setup_id']}/cancel", headers=auth("mgr-a")).status_code == 204
    assert repr(secrets) == "TenantSecretStore(secrets=0)"
    assert client.get(f"/api/v2/connect/setups/{setup['setup_id']}", headers=auth("mgr-a")).status_code == 404
    assert client.get("/api/v2/connect/connections", headers=auth("mgr-a")).json() == []
    assert start(client, "mgr-b").status_code == 201  # the host is free again


def test_an_unused_setup_lapses_and_its_credential_goes_with_it():
    client, _, _, clock, secrets, _ = make()
    setup = start(client).json()
    clock.now += SCRIPT_WINDOW_SECONDS + 1
    assert client.get(f"/api/v2/connect/setups/{setup['setup_id']}", headers=auth("mgr-a")).status_code == 404
    assert repr(secrets) == "TenantSecretStore(secrets=0)"


def test_remove_access_ends_it_now_and_hands_back_the_teardown_for_the_customer():
    client, runtime, state, _, secrets, chain = make()
    setup = start(client).json()
    state["grants"] = grants_from(setup["statements"])
    client.post(f"/api/v2/connect/setups/{setup['setup_id']}/verify", headers=auth("mgr-a"))
    response = client.delete(f"/api/v2/connect/connections/{setup['connection_id']}", headers=auth("mgr-a"))
    body = response.json()
    assert response.status_code == 200 and body["status"] == "removed" and body["credential_deleted"] is True
    assert body["teardown"] == setup["teardown"] and "remain" in body["note"] and len(body["teardown_digest"]) == 64
    assert repr(secrets) == "TenantSecretStore(secrets=0)"
    assert client.get("/api/v2/connect/connections", headers=auth("mgr-a")).json() == []
    with pytest.raises(ConnectionUnavailable) as gone:
        snowflake_gateway_for(runtime.store, secrets, "ta", setup["connection_id"])
    assert gone.value.code == "connection_not_active"
    assert [e.event_type for e in chain.events][-2:] == ["connect.credential_deleted", "connect.access_removed"]
    assert chain.verify()
    assert len({e.event_id for e in chain.events}) == len(chain.events)  # every event id is unique


def test_only_managers_can_remove_access():
    client, *_ = make()
    setup = start(client).json()
    assert client.delete(f"/api/v2/connect/connections/{setup['connection_id']}", headers=auth("usr-a")).status_code == 403
