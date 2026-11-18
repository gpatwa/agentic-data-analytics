"""Product-run wiring (CM5c): register, plan inside the ceiling, approve, sign in, run, on a fake Snowflake that keeps state.

Offline only. The fake models what the spike observed (a setup role that can create a role and user and grant only inside its
ceiling) so the worker, the verifier and the identity checks are exercised against behaviour, not canned answers. It proves the
wiring's own logic, not Snowflake's: the spike and the live gates showed that part on one account."""

import json
import re
import urllib.parse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.connect.approval import ApprovalError, ApprovalGate
from app.connect.grants import ConnectScope
from app.connect.oauth import ConfigStore, OAuthFlow, StateStore, TokenHandoff, TokenHandoffError, TokenResult
from app.connect.oauth_api import MANAGER_GROUP
from app.connect.product_run import (
    ProductRunService,
    RegistrationConflict,
    RegistrationNotFound,
    RegistrationStore,
    snowflake_oauth_connect,
)
from app.connect.product_run_api import ProductRunRuntime, build_product_run_router
from app.connect.registration import OutsideCeiling, generate_registration
from app.connect.store import ConnectionStore, TenantSecretStore
from app.connect.targets import TargetRejected
from app.connect.token import AdminToken
from app.connect.worker import SetupCoordinator, SetupNotFound
from app.security import AuthenticationError
from app.security.audit import AuditChain
from packages.platform_contracts.security import AnalyticsIdentity

TOKEN = "snowflake-access-token-SECRET-0123456789"
HOST = "ei45621.us-east-2.aws.snowflakecomputing.com"
LOCATOR = "EI45621"
REDIRECT = "https://connect.example.com/api/v2/connect/oauth/callback"
CEILING = ConnectScope("ANALYTICS", (("PUBLIC", "ORDERS"), ("PUBLIC", "CUSTOMERS"), ("MART", "DAILY")))
REG = "reg-00000001"
SUFFIX = "a1b2c3d4"


def PUBLIC(host):  # noqa: N802 - a fake resolver
    return ["54.230.10.4"]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Refusal(Exception):
    errno = 3001
    msg = "SQL access control error: Insufficient privileges"


def registration_script(**over):
    args = dict(suffix=SUFFIX, redirect_uri=REDIRECT, egress_networks=["35.238.115.103/32"], setup_users=["ADMIN_USER"])
    args.update(over)
    return generate_registration(CEILING, **args)


def grant_rows(statements, account=LOCATOR):
    rows = []
    for x in statements:
        m = re.match(r"GRANT (CREATE ROLE|CREATE USER|USAGE|SELECT) ON (ACCOUNT|WAREHOUSE|DATABASE|SCHEMA|TABLE)(?: (.+?))? TO ROLE \w+( WITH GRANT OPTION)?$", x)
        if m:
            rows.append({"privilege": m[1], "granted_on": m[2], "name": account if m[2] == "ACCOUNT" else m[3], "grant_option": "true" if m[4] else "false"})
    return rows


class Account:
    """The customer's account as the setup role sees it."""

    def __init__(self, *, locator=LOCATOR, role=None, setup_grants=None, objects=(), fail_on=None):
        from app.connect.registration import RegNames

        self.names = RegNames.for_registration(SUFFIX)
        self.locator, self.role = locator, role or self.names.setup_role
        self.setup_grants = setup_grants if setup_grants is not None else grant_rows(registration_script().statements)
        self.objects, self.fail_on = set(objects) | {self.names.warehouse, self.names.monitor}, fail_on  # pre-created at registration
        self.executed, self.closed, self.role_grants, self.user_roles = [], 0, {}, {}
        self.allowed = {t for t in CEILING.allowed_tables}


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    @property
    def description(self):
        return self.conn.description

    def execute(self, sql):
        return self.conn.execute(sql)

    def fetchall(self):
        return self.conn._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, account):
        self.a, self.description, self._rows = account, None, []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.a.closed += 1

    def _table(self, rows):
        if rows:
            self.description = [(k,) for k in rows[0]]
            self._rows = [tuple(r.values()) for r in rows]

    def execute(self, sql):
        a = self.a
        a.executed.append(sql)
        self.description, self._rows = None, []
        if a.fail_on and a.fail_on in sql:
            raise Refusal()
        if sql.startswith("SELECT CURRENT_ACCOUNT()"):
            return self._table([{"acct": a.locator, "role": a.role}])
        if sql.startswith("SHOW GRANTS TO ROLE"):
            name = sql.split()[-1]
            return self._table(a.setup_grants if name == a.names.setup_role else a.role_grants.get(name, []))
        if sql.startswith("SHOW GRANTS TO USER"):
            return self._table([{"role": r} for r in a.user_roles.get(sql.split()[-1], [])])
        if sql.startswith(("SHOW ROLES", "SHOW USERS")):
            name = sql.split("'")[1]
            return self._table([{"name": name}] if name in a.objects else [])
        if sql.startswith("DROP "):
            a.objects.discard(sql.split()[-1])
            return None
        if sql.startswith(("CREATE ROLE", "CREATE USER")):
            a.objects.add(sql.split()[2])
            return None
        if sql.startswith("ALTER USER"):
            return None
        if m := re.match(r"GRANT ROLE (\w+) TO USER (\w+)", sql):
            a.user_roles.setdefault(m[2], []).append(m[1])
            return None
        if m := re.match(r"GRANT (USAGE|SELECT) ON (WAREHOUSE|DATABASE|SCHEMA|TABLE) (.+) TO ROLE (\w+)$", sql):
            if m[2] == "TABLE":
                parts = re.findall(r'"([^"]+)"', m[3])
                if ".".join(p.lower() for p in parts) not in a.allowed:
                    raise Refusal()
            a.role_grants.setdefault(m[4], []).append({"privilege": m[1], "granted_on": m[2], "name": m[3], "grant_option": "false"})
            return None
        raise Refusal()


def build(account=None, *, ttl=300.0, connect_error=None):
    clock = Clock()
    account = account or Account()
    store, secrets, chain, gate = ConnectionStore(), TenantSecretStore(), AuditChain(), ApprovalGate(clock=clock)
    counter = iter(range(1000))
    coordinator = SetupCoordinator(store, secrets, chain, gate, clock=clock, key_pair=lambda: (b"fake-private-der", "MIIB" + "B" * 120),
                                   id_source=lambda: f"{next(counter):08x}" + "0" * 24)
    registrations, configs, handoff = RegistrationStore(), ConfigStore(), TokenHandoff()
    seen = {"tokens": [], "hosts": []}

    def factory(host):
        seen["hosts"].append(host)

        def connect(token):
            seen["tokens"].append(token)
            if connect_error:
                raise RuntimeError(f"sign-in failed for {token}")
            return FakeConn(account)

        return connect

    service = ProductRunService(registrations, configs, coordinator, gate, handoff, connect_factory=factory,
                                key_pair=lambda: (b"fake-private-der", "MIIB" + "B" * 120))
    return service, account, coordinator, handoff, store, secrets, chain, clock, seen, configs


def register(service, tenant="ta", reg=REG, **over):
    args = dict(tenant_id=tenant, registration_id=reg, reg_suffix=SUFFIX, host=HOST, client_id="client-id-12345678=", account_locator=LOCATOR,
                ceiling=CEILING, setup_users=["ADMIN_USER"], redirect_uri=REDIRECT, resolver=PUBLIC)
    args.update(over)
    return service.register(**args)


def token(clock, ttl=300.0):
    return AdminToken(TOKEN, ttl_seconds=ttl, clock=clock)


SCOPE = ConnectScope("ANALYTICS", (("PUBLIC", "ORDERS"), ("PUBLIC", "CUSTOMERS")))


def ready(service, handoff, clock, scope=SCOPE, tenant="ta", user="lead"):
    pending = service.begin(tenant_id=tenant, user_id=user, registration_id=REG, scope=scope)
    service.approve(pending, user_id=user, digest=pending.script.digest)
    handoff.put(tenant, pending.setup_id, token(clock))
    return pending


# ---- the happy path and its order ----------------------------------------------------------------------------------


def test_register_plan_approve_sign_in_run_creates_only_a_read_only_role_and_user_inside_the_ceiling():
    service, account, coordinator, handoff, store, secrets, chain, clock, seen, _ = build()
    register(service)
    pending = ready(service, handoff, clock)
    result = service.run(pending)
    assert result.ok and result.code is None and result.created == ("role", "user")
    executed = [s for s in account.executed if s.startswith(("CREATE", "ALTER", "GRANT", "DROP"))]
    assert tuple(executed) == pending.script.statements  # exactly what was approved, in order
    assert seen["tokens"] == [TOKEN] and seen["hosts"] == [HOST] and account.closed == 1
    assert not any(s.startswith(("CREATE WAREHOUSE", "CREATE RESOURCE", "CREATE NETWORK", "CREATE SECURITY", "CREATE DATABASE")) for s in account.executed)
    assert store.get("ta", pending.connection_id).status == "pending" and secrets.exists("ta", pending.secret_name)
    assert [e.event_type for e in chain.events] == ["connect.consent_shown", "connect.setup_approved", "connect.setup_executed"] and chain.verify()
    assert not handoff.holds("ta", pending.setup_id)


def test_the_statements_are_read_only_grants_inside_the_ceiling_and_teardown_drops_only_the_user_and_role():
    service, *_ = build()
    register(service)
    pending = service.begin(tenant_id="ta", user_id="lead", registration_id=REG, scope=SCOPE)
    text = "\n".join(pending.script.statements).upper()
    for forbidden in ("WITH GRANT OPTION", "MANAGE GRANTS", "OWNERSHIP", "ACCOUNTADMIN", "SYSADMIN", "CREATE WAREHOUSE", "CREATE RESOURCE", "INTEGRATION"):
        assert forbidden not in text
    assert [s.split()[1] for s in pending.script.teardown] == ["USER", "ROLE"] and pending.preflight == ("role", "user")
    assert pending.names.warehouse == service.registration("ta", REG).names.warehouse


def test_a_run_before_approval_is_refused_without_wasting_the_sign_in():
    service, account, _, handoff, _, _, _, clock, seen, _ = build()
    register(service)
    pending = service.begin(tenant_id="ta", user_id="lead", registration_id=REG, scope=SCOPE)
    handoff.put("ta", pending.setup_id, token(clock))
    with pytest.raises(ApprovalError) as early:
        service.run(pending)
    assert early.value.code == "not_approved" and handoff.holds("ta", pending.setup_id) and seen["tokens"] == [] and account.executed == []
    service.approve(pending, user_id="lead", digest=pending.script.digest)
    assert service.run(pending).ok


def test_a_run_before_sign_in_leaves_the_approval_usable():
    service, _, _, handoff, _, _, _, clock, _, _ = build()
    register(service)
    pending = service.begin(tenant_id="ta", user_id="lead", registration_id=REG, scope=SCOPE)
    service.approve(pending, user_id="lead", digest=pending.script.digest)
    with pytest.raises(TokenHandoffError) as missing:
        service.run(pending)
    assert missing.value.code == "no_token" and service.state(pending) == (True, False)
    handoff.put("ta", pending.setup_id, token(clock))
    assert service.state(pending) == (True, True) and service.run(pending).ok


def test_an_expired_sign_in_is_refused():
    service, _, _, handoff, _, _, _, clock, _, _ = build()
    register(service)
    pending = service.begin(tenant_id="ta", user_id="lead", registration_id=REG, scope=SCOPE)
    service.approve(pending, user_id="lead", digest=pending.script.digest)
    handoff.put("ta", pending.setup_id, token(clock, ttl=60))
    clock.now += 61
    with pytest.raises(TokenHandoffError) as expired:
        service.run(pending)
    assert expired.value.code == "token_expired"


def test_only_the_person_who_planned_it_can_approve_and_only_the_exact_digest():
    service, *_ = build()
    register(service)
    pending = service.begin(tenant_id="ta", user_id="lead", registration_id=REG, scope=SCOPE)
    with pytest.raises(ApprovalError) as other:
        service.approve(pending, user_id="someone-else", digest=pending.script.digest)
    with pytest.raises(ApprovalError) as wrong:
        service.approve(pending, user_id="lead", digest="0" * 64)
    assert other.value.code == "wrong_admin" and wrong.value.code == "digest_mismatch" and service.state(pending) == (False, False)


# ---- T27, drift: the checks that run after sign-in and before anything is created -----------------------------------------


@pytest.mark.parametrize("over,code", [({"locator": "ZZ99999"}, "wrong_account"), ({"role": "SOME_OTHER_ROLE"}, "wrong_role"), ({"role": "ACCOUNTADMIN"}, "wrong_role")])
def test_a_sign_in_on_the_wrong_account_or_role_stops_before_anything_is_created(over, code):
    account = Account(**over)
    service, account, _, handoff, store, secrets, chain, clock, _, _ = build(account)
    register(service)
    pending = ready(service, handoff, clock)
    result = service.run(pending)
    assert not result.ok and result.code == code and result.created == ()
    assert not any(s.startswith(("CREATE", "GRANT", "ALTER", "DROP")) for s in account.executed)
    assert not secrets.exists("ta", pending.secret_name) and store.get("ta", pending.connection_id).status == "removed"
    assert chain.events[-1].event_type == "connect.credential_deleted" and not handoff.holds("ta", pending.setup_id)


def _widened():
    rows = grant_rows(registration_script().statements)
    return [*rows, {"privilege": "SELECT", "granted_on": "TABLE", "name": 'ANALYTICS.PUBLIC."PAYROLL"', "grant_option": "true"}]


@pytest.mark.parametrize(
    "rows",
    [
        _widened(),  # the ceiling was widened after registration
        grant_rows(registration_script().statements)[:-1],  # a grant was removed
        [*grant_rows(registration_script().statements), {"privilege": "MANAGE GRANTS", "granted_on": "ACCOUNT", "name": LOCATOR, "grant_option": "false"}],
        [{**r, "grant_option": "false"} if r["privilege"] == "SELECT" else r for r in grant_rows(registration_script().statements)],
    ],
)
def test_a_setup_role_that_no_longer_matches_the_registration_stops_the_run(rows):
    service, account, _, handoff, _, _, _, clock, _, _ = build(Account(setup_grants=rows))
    register(service)
    result = service.run(ready(service, handoff, clock))
    assert not result.ok and result.code == "registration_changed" and not any(s.startswith(("CREATE", "GRANT")) for s in account.executed)


def test_the_identity_and_grants_are_checked_before_the_name_preflight():
    service, account, *_ = build(Account(locator="ZZ99999", objects={"COMPASS_RO_00000000"}))
    handoff = service._handoff
    register(service)
    clock = Clock()
    result = service.run(ready(service, handoff, clock))
    assert result.code == "wrong_account"
    assert not any(s.startswith("SHOW ROLES") for s in account.executed)


# ---- the ceiling ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope",
    [ConnectScope("ANALYTICS", (("PUBLIC", "PAYROLL"),)), ConnectScope("ANALYTICS", (("PUBLIC", "ORDERS"), ("PUBLIC", "PAYROLL"))), ConnectScope("OTHER", (("PUBLIC", "ORDERS"),))],
)
def test_anything_outside_the_ceiling_is_refused_before_a_key_an_approval_or_a_record_exists(scope):
    service, account, coordinator, handoff, store, secrets, chain, clock, _, _ = build()
    register(service)
    with pytest.raises(OutsideCeiling):
        service.begin(tenant_id="ta", user_id="lead", registration_id=REG, scope=scope)
    assert repr(secrets) == "TenantSecretStore(secrets=0)" and store.list("ta") == [] and chain.events == [] and account.executed == []


def test_a_subset_and_the_whole_ceiling_are_allowed():
    service, *_ = build()
    register(service)
    for scope in (ConnectScope("ANALYTICS", (("MART", "DAILY"),)), CEILING):
        service.begin(tenant_id="ta", user_id=f"u{len(scope.tables)}", registration_id=REG, scope=scope)


# ---- preflight and failure -------------------------------------------------------------------------------------------


def test_the_pre_created_warehouse_and_monitor_are_not_treated_as_taken_but_a_clashing_role_is():
    service, account, _, handoff, _, _, _, clock, _, _ = build()
    register(service)
    pending = ready(service, handoff, clock)
    assert service.run(pending).ok  # the warehouse and monitor exist in the fake and the run still passes
    service2, account2, _, handoff2, _, _, _, clock2, _, _ = build(Account(objects={"COMPASS_RO_00000000"}))
    register(service2)
    pending2 = ready(service2, handoff2, clock2)
    result = service2.run(pending2)
    assert result.code == "name_taken" and "COMPASS_RO_00000000" in account2.objects and not any(s.startswith(("CREATE", "DROP")) for s in account2.executed)


def test_a_failure_part_way_drops_only_what_it_created_and_deletes_the_credential():
    service, account, _, handoff, store, secrets, _, clock, _, _ = build(Account(fail_on="CREATE USER"))
    register(service)
    pending = ready(service, handoff, clock)
    result = service.run(pending)
    assert not result.ok and result.created == ("role",) and result.leftovers == ()
    assert account.objects == {service.registration("ta", REG).names.warehouse, service.registration("ta", REG).names.monitor}
    assert not secrets.exists("ta", pending.secret_name) and store.get("ta", pending.connection_id).status == "removed"


def test_a_failed_sign_in_session_reports_the_type_only():
    service, account, _, handoff, _, secrets, chain, clock, seen, _ = build(connect_error=True)
    register(service)
    pending = ready(service, handoff, clock)
    result = service.run(pending)
    assert result.code == "admin_connect_failed:RuntimeError" and TOKEN not in json.dumps([e.model_dump(mode="json") for e in chain.events]) + repr(result)


def test_nothing_secret_survives_a_run():
    service, account, _, handoff, store, secrets, chain, clock, _, _ = build()
    register(service)
    pending = ready(service, handoff, clock)
    result = service.run(pending)
    haystack = json.dumps([e.model_dump(mode="json") for e in chain.events]) + repr(result) + repr(pending) + repr(handoff) + repr(secrets) + repr(store.get("ta", pending.connection_id))
    assert TOKEN not in haystack and "fake-private-der" not in haystack and "ZmFrZS1w" not in haystack


# ---- tenants and registrations ---------------------------------------------------------------------------------------


def test_another_tenant_cannot_use_a_registration_see_a_setup_or_borrow_its_sign_in():
    service, _, coordinator, handoff, *_ = build()
    clock = Clock()
    register(service)
    with pytest.raises(RegistrationNotFound):
        service.begin(tenant_id="tb", user_id="x", registration_id=REG, scope=SCOPE)
    pending = ready(service, handoff, clock)
    with pytest.raises(SetupNotFound):
        coordinator.get_pending("tb", pending.setup_id)
    with pytest.raises(TokenHandoffError):
        handoff.take("tb", pending.setup_id)


def test_a_host_belongs_to_one_tenant_and_a_registration_id_is_not_reused():
    service, *_ = build()
    register(service)
    with pytest.raises(RegistrationConflict) as claimed:
        register(service, tenant="tb")
    assert claimed.value.code == "target_claimed"
    with pytest.raises(RegistrationConflict) as taken:
        register(service)
    assert taken.value.code == "registration_id_taken"


@pytest.mark.parametrize(
    "over",
    [{"reg_suffix": "ZZZ"}, {"account_locator": "ab"}, {"account_locator": "bad locator"}, {"host": "evil.example"}, {"host": "10.0.0.1"},
     {"client_id": "x"}, {"setup_users": []}, {"redirect_uri": "http://insecure.example.com/cb"}],
)
def test_a_bad_registration_is_refused(over):
    service, *_ = build()
    with pytest.raises(ValueError):
        register(service, **over)


def test_a_host_that_resolves_to_a_private_address_is_refused_at_registration():
    service, *_ = build()
    with pytest.raises(TargetRejected) as caught:
        register(service, resolver=lambda host: ["10.0.0.5"])
    assert caught.value.code == "non_public_address"


def test_the_connector_is_called_with_an_oauth_token_for_the_registered_account_only():
    calls = []

    class Driver:
        @staticmethod
        def connect(**kwargs):
            calls.append(kwargs)
            return "conn"

    assert snowflake_oauth_connect(HOST, connector=Driver)(TOKEN) == "conn"
    assert snowflake_oauth_connect("zcpwqmj-hd11049.snowflakecomputing.com", connector=Driver)(TOKEN) == "conn"
    assert calls[0] == {"account": "ei45621.us-east-2.aws", "authenticator": "oauth", "token": TOKEN, "login_timeout": 60, "network_timeout": 60}
    assert calls[1]["account"] == "zcpwqmj-hd11049"


# ---- the sign-in feeds the run: CM5b to CM5c -------------------------------------------------------------------------


def test_a_real_callback_hands_the_token_to_the_run():
    service, account, coordinator, handoff, store, secrets, chain, clock, seen, configs = build()
    register(service)
    states = StateStore(clock)
    flow = OAuthFlow(configs, states, handoff, exchange=lambda endpoint, cid, code, redirect, verifier: TokenResult(AdminToken(TOKEN, ttl_seconds=300, clock=clock), 600.0, False), clock=clock)
    pending = service.begin(tenant_id="ta", user_id="lead", registration_id=REG, scope=SCOPE)
    service.approve(pending, user_id="lead", digest=pending.script.digest)
    start = flow.start(tenant_id="ta", user_id="lead", registration_id=REG, setup_id=pending.setup_id)
    state = urllib.parse.parse_qs(urllib.parse.urlparse(start.authorize_url).query)["state"][0]
    assert service.state(pending) == (True, False)
    assert flow.callback({"code": "the-code-0000000000", "state": state}, start.binding).ok
    assert service.state(pending) == (True, True)
    assert service.run(pending).ok and seen["tokens"] == [TOKEN]


# ---- the endpoints ---------------------------------------------------------------------------------------------------

IDENTITIES = {
    "mgr": AnalyticsIdentity(tenant_id="ta", user_id="lead", groups=[MANAGER_GROUP]),
    "mgr2": AnalyticsIdentity(tenant_id="ta", user_id="second-lead", groups=[MANAGER_GROUP]),
    "usr": AnalyticsIdentity(tenant_id="ta", user_id="analyst", groups=[]),
    "mgr-b": AnalyticsIdentity(tenant_id="tb", user_id="lead-b", groups=[MANAGER_GROUP]),
}


class Verifier:
    def verify(self, bearer):
        try:
            return IDENTITIES[bearer]
        except KeyError as exc:
            raise AuthenticationError("bad") from exc


def make_app(configured=True, redirect=REDIRECT, **kw):
    service, account, coordinator, handoff, store, secrets, chain, clock, seen, configs = build(**kw)
    runtime = ProductRunRuntime(service=service if configured else None, coordinator=coordinator if configured else None,
                                verifier=Verifier() if configured else None, redirect_uri=redirect, dns=PUBLIC)

    async def no_key():
        return None

    app = FastAPI()
    app.include_router(build_product_run_router(runtime, no_key))
    return TestClient(app), service, handoff, clock, account, secrets, seen


def auth(who="mgr"):
    return {"Authorization": f"Bearer {who}"}


P = "/api/v2/connect/product-run"
REGISTER = {"registration_id": REG, "reg_suffix": SUFFIX, "host": HOST, "client_id": "client-id-12345678=", "account_locator": LOCATOR,
            "database": "ANALYTICS", "tables": [{"schema": "PUBLIC", "table": "ORDERS"}, {"schema": "PUBLIC", "table": "CUSTOMERS"}, {"schema": "MART", "table": "DAILY"}],
            "setup_users": ["ADMIN_USER"]}
PLAN = {"registration_id": REG, "tables": [{"schema": "PUBLIC", "table": "ORDERS"}]}


def test_every_endpoint_is_503_until_configured():
    client, *_ = make_app(configured=False)
    for method, path in (("get", "/registrations"), ("post", "/registrations"), ("post", "/setups"), ("get", "/setups/x")):
        assert getattr(client, method)(P + path, headers=auth()).status_code == 503


def test_a_bearer_and_the_manager_group_are_required():
    client, *_ = make_app()
    assert client.post(P + "/registrations", json=REGISTER).status_code == 401
    assert client.post(P + "/registrations", json=REGISTER, headers=auth("usr")).status_code == 403
    assert client.get(P + "/registrations", headers=auth("usr")).status_code == 200  # anyone in the tenant may look


def test_registering_needs_a_configured_redirect_and_a_public_host_and_does_not_echo_secrets():
    client, *_ = make_app(redirect=None)
    assert client.post(P + "/registrations", json=REGISTER, headers=auth()).json()["detail"] == "redirect_not_configured"
    client, *_ = make_app()
    created = client.post(P + "/registrations", json=REGISTER, headers=auth())
    assert created.status_code == 201 and set(created.json()) == {"registration_id", "host", "setup_role", "database", "ceiling_tables", "setup_user_count"}
    assert "client-id" not in created.text and client.post(P + "/registrations", json=REGISTER, headers=auth()).json()["detail"] == "registration_id_taken"
    assert client.post(P + "/registrations", json={**REGISTER, "registration_id": "reg-00000002", "host": "evil.example"}, headers=auth()).status_code == 422
    assert client.post(P + "/registrations", json={**REGISTER, "registration_id": "reg-00000003", "reg_suffix": "ZZZZZZZZ"}, headers=auth()).json()["detail"] == "invalid_registration"


def test_the_whole_flow_over_http_in_the_enforced_order():
    client, service, handoff, clock, account, secrets, seen = make_app()
    client.post(P + "/registrations", json=REGISTER, headers=auth())
    plan = client.post(P + "/setups", json=PLAN, headers=auth())
    body = plan.json()
    assert plan.status_code == 201 and body["status"] == "awaiting_approval" and not body["approved"] and not body["signed_in"]
    assert len(body["digest"]) == 64 and any(s.startswith("GRANT SELECT") for s in body["statements"]) and body["registration_id"] == REG
    text = json.dumps(body)
    assert "fake-private" not in text and "PRIVATE" not in text and "secret_name" not in text  # the public half is shown on purpose
    sid = body["setup_id"]
    assert client.post(f"{P}/setups/{sid}/run", headers=auth()).json()["detail"] == "not_approved"
    assert client.post(f"{P}/setups/{sid}/approve", json={"digest": "0" * 64}, headers=auth()).json()["detail"] == "digest_mismatch"
    assert client.post(f"{P}/setups/{sid}/approve", json={"digest": body["digest"]}, headers=auth("mgr2")).json()["detail"] == "wrong_admin"
    approved = client.post(f"{P}/setups/{sid}/approve", json={"digest": body["digest"]}, headers=auth()).json()
    assert approved["status"] == "awaiting_sign_in" and approved["approved"] is True
    assert client.post(f"{P}/setups/{sid}/run", headers=auth()).json()["detail"] == "sign_in_required"
    handoff.put("ta", sid, token(clock))  # the OAuth callback does this
    assert client.get(f"{P}/setups/{sid}", headers=auth()).json()["status"] == "ready_to_run"
    result = client.post(f"{P}/setups/{sid}/run", headers=auth())
    assert result.status_code == 200 and result.json()["ok"] is True and result.json()["created"] == ["role", "user"] and TOKEN not in result.text
    assert seen["tokens"] == [TOKEN]


def test_an_expired_sign_in_over_http():
    client, service, handoff, clock, *_ = make_app()
    client.post(P + "/registrations", json=REGISTER, headers=auth())
    body = client.post(P + "/setups", json=PLAN, headers=auth()).json()
    client.post(f"{P}/setups/{body['setup_id']}/approve", json={"digest": body["digest"]}, headers=auth())
    handoff.put("ta", body["setup_id"], token(clock, ttl=60))
    clock.now += 61
    assert client.post(f"{P}/setups/{body['setup_id']}/run", headers=auth()).json()["detail"] == "sign_in_expired"


def test_the_wrong_account_is_a_result_not_a_crash_over_http():
    client, service, handoff, clock, account, secrets, _ = make_app(account=Account(locator="ZZ99999"))
    client.post(P + "/registrations", json=REGISTER, headers=auth())
    body = client.post(P + "/setups", json=PLAN, headers=auth()).json()
    client.post(f"{P}/setups/{body['setup_id']}/approve", json={"digest": body["digest"]}, headers=auth())
    handoff.put("ta", body["setup_id"], token(clock))
    result = client.post(f"{P}/setups/{body['setup_id']}/run", headers=auth()).json()
    assert result["ok"] is False and result["code"] == "wrong_account" and result["created"] == []
    assert repr(secrets) == "TenantSecretStore(secrets=0)"


def test_planning_outside_the_ceiling_or_for_another_tenant_is_refused_and_isolated():
    client, *_ = make_app()
    client.post(P + "/registrations", json=REGISTER, headers=auth())
    outside = client.post(P + "/setups", json={**PLAN, "tables": [{"schema": "PUBLIC", "table": "PAYROLL"}]}, headers=auth())
    assert outside.status_code == 422 and outside.json()["detail"] == "outside_ceiling"
    assert client.post(P + "/setups", json={**PLAN, "tables": [{"schema": "PUBLIC", "table": "*"}]}, headers=auth()).json()["detail"] == "invalid_scope"
    assert client.post(P + "/setups", json=PLAN, headers=auth("mgr-b")).status_code == 404
    body = client.post(P + "/setups", json=PLAN, headers=auth()).json()
    for call in (client.get(f"{P}/setups/{body['setup_id']}", headers=auth("mgr-b")), client.post(f"{P}/setups/{body['setup_id']}/run", headers=auth("mgr-b")),
                 client.post(f"{P}/setups/{body['setup_id']}/cancel", headers=auth("mgr-b"))):
        assert call.status_code == 404 and call.json()["detail"] == "setup_not_found"
    assert client.get(P + "/registrations", headers=auth("mgr-b")).json() == []


def test_cancel_deletes_the_credential():
    client, service, handoff, clock, account, secrets, _ = make_app()
    client.post(P + "/registrations", json=REGISTER, headers=auth())
    body = client.post(P + "/setups", json=PLAN, headers=auth()).json()
    assert repr(secrets) == "TenantSecretStore(secrets=1)"
    assert client.post(f"{P}/setups/{body['setup_id']}/cancel", headers=auth()).status_code == 204
    assert repr(secrets) == "TenantSecretStore(secrets=0)" and client.get(f"{P}/setups/{body['setup_id']}", headers=auth()).status_code == 404


def test_setup_not_found_is_the_same_for_missing_and_foreign():
    client, *_ = make_app()
    client.post(P + "/registrations", json=REGISTER, headers=auth())
    assert client.get(P + "/setups/does-not-exist", headers=auth()).json() == {"detail": "setup_not_found"}
    with pytest.raises(SetupNotFound):
        make_app()[1]._coordinator.get_pending("ta", "nope")


def test_the_main_app_mounts_the_sign_in_and_product_run_endpoints_inert():
    from app import main

    client = TestClient(main.app)
    assert client.get(P + "/registrations").status_code == 503
    assert client.post("/api/v2/connect/oauth/start", json={"registration_id": REG, "setup_id": "setup-0000000001"}).status_code == 503
    assert client.get("/api/v2/connect/oauth/callback?state=x", follow_redirects=False).status_code == 503
