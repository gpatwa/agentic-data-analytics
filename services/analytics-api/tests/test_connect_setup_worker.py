"""The Connect-mode setup worker, approval gate and admin token (CM3), on a fake Snowflake admin session.

No network and no account. The fake keeps real state: it records what was created, answers the `SHOW` statements the worker
sends, and reports grants derived from the `GRANT` statements it was given, so verification and rollback are exercised
against something that behaves like a warehouse rather than a script of canned answers.
"""

import base64
import copy
import json
import logging
import pickle
import re

import pytest

from app.connect.approval import ApprovalError, ApprovalGate
from app.connect.grants import ConnectScope
from app.connect.records import ApprovalMismatch
from app.connect.store import ConnectionConflict, ConnectionStore, TenantSecretStore
from app.connect.targets import TargetRejected
from app.connect.token import AdminToken, TokenUnavailable
from app.connect.worker import DEADLINE_SECONDS, SetupCoordinator
from app.security.audit import AuditChain

TOKEN = "admin-oauth-token-SECRET-0123456789"
SCOPE = ConnectScope("ANALYTICS", (("PUBLIC", "ORDERS"), ("PUBLIC", "CUSTOMERS")))
HOST = "acme.snowflakecomputing.com"


def PUBLIC(host):  # noqa: N802
    return ["54.230.10.4"]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeAdmin:
    """A fake admin connection with real state. `fail_on` is a substring of a statement that should raise."""

    def __init__(self, *, existing=(), fail_on=None, extra_grants=(), fail_drop=None, clock=None, tick=0.0):
        self.executed, self.objects, self.grants, self.user_roles = [], set(existing), [], []
        self.fail_on, self.fail_drop, self.extra_grants = fail_on, fail_drop, list(extra_grants)
        self.closed, self._clock, self._tick = False, clock, tick
        self.description = None
        self._rows = []

    def cursor(self):
        return self

    def close(self):
        self.closed = True

    def fetchall(self):
        return self._rows

    def execute(self, statement):
        self.executed.append(statement)
        if self._clock:
            self._clock.now += self._tick
        self.description, self._rows = None, []
        if self.fail_on and self.fail_on in statement:
            raise RuntimeError(f"boom while using {TOKEN}")  # a driver error that carries the token
        if statement.startswith("DROP "):
            if self.fail_drop and self.fail_drop in statement:
                raise RuntimeError("drop failed")
            self.objects.discard(statement.split()[-1])
        elif m := re.match(r"CREATE (?:ROLE|USER|WAREHOUSE|RESOURCE MONITOR|NETWORK POLICY) (\w+)", statement):
            self.objects.add(m[1])
        elif m := re.match(r"GRANT (USAGE|SELECT) ON (WAREHOUSE|DATABASE|SCHEMA|TABLE) (.+) TO ROLE", statement):
            self.grants.append({"privilege": m[1], "granted_on": m[2], "name": m[3], "grant_option": "false"})
        elif m := re.match(r"GRANT ROLE (\w+) TO USER", statement):
            self.user_roles.append({"role": m[1]})
        elif statement.startswith("SHOW GRANTS TO ROLE"):
            self._table(self.grants + self.extra_grants)
        elif statement.startswith("SHOW GRANTS TO USER"):
            self._table(self.user_roles)
        elif m := re.match(r"SHOW \w+(?: \w+)? LIKE '(\w+)'", statement):
            self._table([{"name": m[1]}] if m[1] in self.objects else [])
        elif statement == "SHOW NETWORK POLICIES":
            self._table([{"name": n} for n in self.objects if "_NET_" in n])

    def _table(self, rows):
        if rows:
            keys = list(rows[0])
            self.description = [(k,) for k in keys]
            self._rows = [tuple(r.get(k) for k in keys) for r in rows]


def key_pair():
    return b"fake-private-key-der", "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA" + "B" * 120


def build(clock=None, ttl=300.0, ids=None):
    clock = clock or Clock()
    store, secrets, chain = ConnectionStore(), TenantSecretStore(), AuditChain()
    gate = ApprovalGate(clock=clock, ttl_seconds=ttl)
    counter = iter(range(1000))
    ids = ids or (lambda: f"{next(counter):08x}" + "0" * 24)
    return clock, store, secrets, chain, gate, SetupCoordinator(store, secrets, chain, gate, clock=clock, key_pair=key_pair, id_source=ids)


def begin(coord, tenant="t1", admin="admin@customer.example", **kw):
    return coord.begin(tenant_id=tenant, admin_id=admin, requested_by="lead@customer.example", host=HOST, account="acme", scope=SCOPE, resolver=PUBLIC, **kw)


def approved(gate, pending):
    gate.approve(pending.request.request_id, admin_id=pending.admin_id, digest=pending.request.digest)


def token(clock, ttl=120.0):
    return AdminToken(TOKEN, ttl_seconds=ttl, clock=clock)


def everything(*objects):
    return " ".join(repr(o) + str(getattr(o, "__dict__", "")) for o in objects)


# ---- the token -----------------------------------------------------------------------------------------------------


def test_a_token_cannot_be_printed_formatted_serialised_or_copied():
    t = AdminToken(TOKEN, ttl_seconds=60)
    for text in (repr(t), str(t), f"{t}", f"{t!s:>40}", "%s" % t):
        assert TOKEN not in text
    for action in (lambda: pickle.dumps(t), lambda: copy.copy(t), lambda: copy.deepcopy(t), lambda: json.dumps(t)):
        with pytest.raises(TypeError):
            action()
    assert t.reveal() == TOKEN


def test_a_token_expires_and_wipes():
    clock = Clock()
    t = AdminToken(TOKEN, ttl_seconds=60, clock=clock)
    clock.now += 61
    assert not t.usable
    with pytest.raises(TokenUnavailable) as expired:
        t.reveal()
    assert expired.value.code == "token_expired"
    fresh = AdminToken(TOKEN, ttl_seconds=60, clock=clock)
    fresh.wipe()
    with pytest.raises(TokenUnavailable) as wiped:
        fresh.reveal()
    assert wiped.value.code == "token_wiped"


@pytest.mark.parametrize("ttl", [0, -1, 301])
def test_a_token_lifetime_is_capped(ttl):
    with pytest.raises(ValueError):
        AdminToken(TOKEN, ttl_seconds=ttl)
    with pytest.raises(ValueError):
        AdminToken("", ttl_seconds=10)


# ---- the approval gate ---------------------------------------------------------------------------------------------


def test_only_the_signed_in_admin_can_approve_exact_statements_once():
    clock, _, _, _, gate, coord = build()
    pending = begin(coord)
    rid, digest = pending.request.request_id, pending.request.digest
    with pytest.raises(ApprovalError) as wrong_admin:
        gate.approve(rid, admin_id="lead@customer.example", digest=digest)  # the requester is not the admin
    assert wrong_admin.value.code == "wrong_admin"
    with pytest.raises(ApprovalError) as wrong_digest:
        gate.approve(rid, admin_id=pending.admin_id, digest="0" * 64)
    assert wrong_digest.value.code == "digest_mismatch"
    with pytest.raises(ApprovalError) as early:
        gate.take(rid, admin_id=pending.admin_id)
    assert early.value.code == "not_approved"
    gate.approve(rid, admin_id=pending.admin_id, digest=digest)
    assert gate.take(rid, admin_id=pending.admin_id).digest == digest
    with pytest.raises(ApprovalError) as again:
        gate.take(rid, admin_id=pending.admin_id)
    assert again.value.code == "unknown_request"


def test_an_approval_expires():
    clock, _, _, _, gate, coord = build(ttl=300)
    pending = begin(coord)
    approved(gate, pending)
    clock.now += 301
    with pytest.raises(ApprovalError) as expired:
        gate.take(pending.request.request_id, admin_id=pending.admin_id)
    assert expired.value.code == "approval_expired"


def test_the_request_carries_the_plain_summary_and_exact_statements():
    _, _, _, _, _, coord = build()
    request = begin(coord).request
    assert any("read-only" in line for line in request.summary)
    assert request.statements and all(isinstance(s, str) for s in request.statements)


# ---- begin ---------------------------------------------------------------------------------------------------------


def test_begin_stores_the_key_by_reference_and_a_pending_record():
    _, store, secrets, chain, _, coord = build()
    pending = begin(coord)
    record = store.get("t1", pending.connection_id)
    assert record.status == "pending" and record.secret_name == pending.secret_name
    assert base64.b64decode(secrets.read("t1", pending.secret_name)) == b"fake-private-key-der"
    assert record.allowed_tables == SCOPE.allowed_tables
    assert [e.event_type for e in chain.events] == ["connect.consent_shown"]
    assert "fake-private" not in everything(pending, record) and "ZmFrZS1wcml2" not in json.dumps([e.model_dump(mode="json") for e in chain.events])


def test_begin_rejects_a_bad_target_before_anything_is_stored():
    _, store, secrets, _, _, coord = build()
    with pytest.raises(TargetRejected):
        coord.begin(tenant_id="t1", admin_id="a", requested_by="r", host="169.254.169.254", account="acme", scope=SCOPE, resolver=PUBLIC)
    assert store.list("t1") == [] and repr(secrets) == "TenantSecretStore(secrets=0)"


def test_begin_cleans_up_the_key_when_another_tenant_holds_the_host():
    _, store, secrets, _, _, coord = build()
    begin(coord, tenant="t1")
    with pytest.raises(ConnectionConflict):
        begin(coord, tenant="t2")
    assert store.list("t2") == [] and secrets.exists("t1", next(iter(store.list("t1"))).secret_name)
    assert "secrets=1" in repr(secrets)


# ---- run: the happy path -------------------------------------------------------------------------------------------


def test_an_approved_setup_runs_exactly_the_approved_statements_and_verifies():
    clock, store, secrets, chain, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    admin, seen = FakeAdmin(), {}
    tok = token(clock)

    def connect(value):
        seen["value"] = value
        return admin

    result = coord.run(pending, connect=connect, token=tok, connection_test=lambda record: (True, []))
    assert result.ok and result.code is None and result.created == ("role", "monitor", "warehouse", "user")
    executed = [s for s in admin.executed if not s.startswith("SHOW")]
    assert tuple(executed) == pending.script.statements  # exactly what was approved, in order
    assert seen["value"] == TOKEN and admin.closed
    assert store.get("t1", pending.connection_id).status == "active"
    assert secrets.exists("t1", pending.secret_name)
    types = [e.event_type for e in chain.events]
    assert types == ["connect.consent_shown", "connect.setup_approved", "connect.setup_executed", "connect.verified"]
    assert chain.verify() and result.teardown_digest
    with pytest.raises(TokenUnavailable):
        tok.reveal()  # wiped


def test_without_a_connection_test_the_record_stays_pending():
    clock, store, _, _, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    assert coord.run(pending, connect=lambda v: FakeAdmin(), token=token(clock)).ok
    assert store.get("t1", pending.connection_id).status == "pending"


def test_a_failed_connection_test_suspends_and_leaves_the_objects_for_the_admin():
    clock, store, _, chain, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    admin = FakeAdmin()
    result = coord.run(pending, connect=lambda v: admin, token=token(clock), connection_test=lambda r: (False, ["read_only_grants", "cancel"]))
    assert not result.ok and result.code == "connection_test_failed" and result.failed_checks == ("cancel", "read_only_grants")
    assert store.get("t1", pending.connection_id).status == "suspended" and result.teardown_digest
    assert any(o.startswith("COMPASS_ROLE_") for o in admin.objects)
    assert chain.events[-1].payload == {"ok": False, "failed_checks": ["cancel", "read_only_grants"]}


def test_a_crashing_connection_test_is_a_failure_not_an_exception():
    clock, _, _, _, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)

    def boom(record):
        raise RuntimeError("no")

    result = coord.run(pending, connect=lambda v: FakeAdmin(), token=token(clock), connection_test=boom)
    assert result.code == "connection_test_failed" and result.failed_checks == ("test_error:RuntimeError",)


# ---- run: the gates ------------------------------------------------------------------------------------------------


def test_nothing_runs_without_approval_and_the_request_survives_for_a_retry():
    clock, _, _, _, gate, coord = build()
    pending = begin(coord)
    called = []
    with pytest.raises(ApprovalError) as early:
        coord.run(pending, connect=lambda v: called.append(v), token=token(clock))
    assert early.value.code == "not_approved" and not called
    approved(gate, pending)
    assert coord.run(pending, connect=lambda v: FakeAdmin(), token=token(clock)).ok


def test_an_expired_token_is_refused_before_any_connection():
    clock, _, _, _, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    tok = token(clock, ttl=10)
    clock.now += 11
    called = []
    with pytest.raises(TokenUnavailable):
        coord.run(pending, connect=lambda v: called.append(v), token=tok)
    assert not called


def test_a_setup_runs_once():
    clock, _, _, _, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    assert coord.run(pending, connect=lambda v: FakeAdmin(), token=token(clock)).ok
    with pytest.raises(ApprovalError) as again:
        coord.run(pending, connect=lambda v: FakeAdmin(), token=token(clock))
    assert again.value.code == "setup_not_pending"


def test_a_script_changed_after_approval_is_refused_and_rolled_back():
    from dataclasses import replace

    clock, store, secrets, _, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    tampered = replace(pending, script=replace(pending.script, statements=pending.script.statements + ("GRANT SELECT ON TABLE a.b.c TO ROLE x",)))
    admin = FakeAdmin()
    result = coord.run(tampered, connect=lambda v: admin, token=token(clock))
    assert not result.ok and result.code == "internal:ApprovalMismatch" and admin.executed == []
    assert not secrets.exists("t1", pending.secret_name) and store.get("t1", pending.connection_id).status == "removed"
    assert issubclass(ApprovalMismatch, ValueError)


# ---- run: failure and rollback -------------------------------------------------------------------------------------


def test_an_existing_name_aborts_before_anything_is_created():
    clock, store, secrets, _, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    admin = FakeAdmin(existing={pending.names.role})
    result = coord.run(pending, connect=lambda v: admin, token=token(clock))
    assert result.code == "name_taken" and result.created == ()
    assert not any(s.startswith(("CREATE", "GRANT", "ALTER")) for s in admin.executed)
    assert pending.names.role in admin.objects  # the object that was not ours is untouched
    assert not secrets.exists("t1", pending.secret_name) and store.get("t1", pending.connection_id).status == "removed"


@pytest.mark.parametrize("fail_on,created", [("CREATE ROLE", ()), ("CREATE WAREHOUSE", ("role", "monitor")), ("GRANT SELECT ON TABLE", ("role", "monitor", "warehouse")), ("CREATE USER", ("role", "monitor", "warehouse"))])
def test_a_failure_part_way_drops_only_what_this_run_created(fail_on, created):
    clock, store, secrets, chain, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    admin = FakeAdmin(existing={"UNRELATED_OBJECT"}, fail_on=fail_on)
    result = coord.run(pending, connect=lambda v: admin, token=token(clock))
    assert not result.ok and result.created == created and result.leftovers == ()
    assert admin.objects == {"UNRELATED_OBJECT"}  # everything this run created is gone, nothing else touched
    drops = [s for s in admin.executed if s.startswith("DROP")]
    assert len(drops) == len(created)
    assert not secrets.exists("t1", pending.secret_name) and store.get("t1", pending.connection_id).status == "removed"
    assert chain.events[-1].event_type == "connect.credential_deleted" and chain.verify()


def test_a_drop_that_fails_is_reported_as_a_leftover():
    clock, _, _, _, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    admin = FakeAdmin(fail_on="CREATE USER", fail_drop="DROP WAREHOUSE")
    result = coord.run(pending, connect=lambda v: admin, token=token(clock))
    assert result.leftovers == (f"warehouse {pending.names.warehouse}",)
    assert pending.names.warehouse in admin.objects and pending.names.role not in admin.objects


def test_extra_grants_found_at_verification_roll_everything_back():
    clock, store, _, _, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    extra = {"privilege": "SELECT", "granted_on": "TABLE", "name": 'ANALYTICS.PUBLIC."SECRETS"', "grant_option": "false"}
    admin = FakeAdmin(extra_grants=[extra])
    result = coord.run(pending, connect=lambda v: admin, token=token(clock))
    assert result.code == "grants_unexpected" and result.created and not any(o.startswith("COMPASS_") for o in admin.objects)
    assert store.get("t1", pending.connection_id).status == "removed"


def test_the_deadline_stops_the_setup_and_still_rolls_back():
    clock = Clock()
    _, store, _, _, gate, coord = build(clock=clock)
    pending = begin(coord)
    approved(gate, pending)
    admin = FakeAdmin(clock=clock, tick=DEADLINE_SECONDS / 4)
    result = coord.run(pending, connect=lambda v: admin, token=token(clock, ttl=300))
    assert result.code == "deadline_exceeded" and 0 < result.steps < len(pending.script.statements)
    assert not any(o.startswith("COMPASS_") for o in admin.objects)


def test_a_failed_sign_in_is_reported_by_type_only_and_cleans_up():
    clock, store, secrets, chain, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)

    def connect(value):
        raise RuntimeError(f"sign-in refused for {value}")

    tok = token(clock)
    result = coord.run(pending, connect=connect, token=tok)
    assert result.code == "admin_connect_failed:RuntimeError"
    assert TOKEN not in json.dumps([e.model_dump(mode="json") for e in chain.events]) and TOKEN not in repr(result)
    assert not secrets.exists("t1", pending.secret_name)
    with pytest.raises(TokenUnavailable):
        tok.reveal()


# ---- nothing leaks -------------------------------------------------------------------------------------------------


def test_the_token_appears_nowhere_after_a_failing_run(caplog):
    caplog.set_level(logging.DEBUG)
    clock, store, secrets, chain, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    admin = FakeAdmin(fail_on="GRANT SELECT ON TABLE")  # raises an error that carries the token
    result = coord.run(pending, connect=lambda v: admin, token=token(clock))
    assert result.code.startswith("setup_step_") and "RuntimeError" in result.code
    haystack = json.dumps([e.model_dump(mode="json") for e in chain.events]) + everything(result, pending, coord, gate, store, secrets, chain)
    haystack += caplog.text + " ".join(str(r.args) for r in caplog.records)
    assert TOKEN not in haystack
    assert "fake-private-key" not in haystack


def test_the_token_appears_nowhere_after_a_successful_run(caplog):
    caplog.set_level(logging.DEBUG)
    clock, store, secrets, chain, gate, coord = build()
    pending = begin(coord)
    approved(gate, pending)
    result = coord.run(pending, connect=lambda v: FakeAdmin(), token=token(clock), connection_test=lambda r: (True, []))
    haystack = json.dumps([e.model_dump(mode="json") for e in chain.events]) + everything(result, pending, coord, gate, store, secrets, chain) + caplog.text
    assert TOKEN not in haystack and "fake-private-key" not in haystack


def test_two_tenants_set_up_independently_with_nothing_shared():
    clock, store, secrets, chain, gate, coord = build()
    a = coord.begin(tenant_id="ta", admin_id="admin-a", requested_by="x", host="acme.snowflakecomputing.com", account="acme", scope=SCOPE, resolver=PUBLIC)
    b = coord.begin(tenant_id="tb", admin_id="admin-b", requested_by="y", host="other.snowflakecomputing.com", account="other", scope=SCOPE, resolver=PUBLIC)
    approved(gate, a)
    approved(gate, b)
    admin_b = FakeAdmin(fail_on="CREATE USER")
    assert coord.run(b, connect=lambda v: admin_b, token=AdminToken("tb-token-123456789", ttl_seconds=60, clock=clock)).code.startswith("setup_step_")
    assert coord.run(a, connect=lambda v: FakeAdmin(), token=AdminToken("ta-token-123456789", ttl_seconds=60, clock=clock)).ok
    assert secrets.exists("ta", a.secret_name) and not secrets.exists("tb", b.secret_name)
    assert store.get("ta", a.connection_id).status == "pending" and store.get("tb", b.connection_id).status == "removed"
    dump = json.dumps([e.model_dump(mode="json") for e in chain.events])
    assert "ta-token" not in dump and "tb-token" not in dump
    with pytest.raises(Exception):
        store.get("ta", b.connection_id)


def test_cancel_removes_the_pending_setup_and_the_key():
    _, store, secrets, chain, gate, coord = build()
    pending = begin(coord)
    coord.cancel(pending)
    assert not secrets.exists("t1", pending.secret_name) and store.get("t1", pending.connection_id).status == "removed"
    with pytest.raises(ApprovalError):
        gate.approve(pending.request.request_id, admin_id=pending.admin_id, digest=pending.request.digest)
    assert chain.events[-1].event_type == "connect.credential_deleted"
