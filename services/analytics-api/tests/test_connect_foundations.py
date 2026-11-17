"""Connect mode foundations (CM2), on fakes only. No network, no warehouse, no account.

Covers the connection-target controls, the per-tenant store and secret custody, the setup script and grants verifier, the
approval-bound records, and the per-tenant gateway wiring. These prove local behaviour; none of it has run against a live
customer account.
"""

import base64
import json
import re

import pytest
from tests.test_warehouse_gateways import _plan

from app.connect.gateway import ConnectionUnavailable, snowflake_gateway_for
from app.connect.grants import (
    ConnectScope,
    Names,
    ScopeError,
    _parts,
    digest_statements,
    generate_snowflake_setup,
    verify_grants,
    verify_user_roles,
)
from app.connect.records import ApprovalMismatch, SecretInRecord, SetupRecorder
from app.connect.store import (
    ConnectionConflict,
    ConnectionNotFound,
    ConnectionRecord,
    ConnectionStore,
    TenantSecretStore,
)
from app.connect.targets import TargetRejected, validate_target, verify_still_pinned
from app.execution.gateway import QueryRejected
from app.security.audit import AuditChain


def PUBLIC(host):  # noqa: N802 - a fake resolver
    return ["54.230.10.4", "2600:9000:2000::1"]


KEY_BODY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA" + "A" * 120


# ---- targets (T6) --------------------------------------------------------------------------------------------------


def test_a_real_vendor_host_passes_and_is_pinned():
    target = validate_target("snowflake", "Acme-Prod.snowflakecomputing.com", expected_account="acme-prod", resolver=PUBLIC)
    assert target.host == "acme-prod.snowflakecomputing.com" and target.account == "acme-prod"
    assert target.pinned == frozenset({"54.230.10.4", "2600:9000:2000::1"})


@pytest.mark.parametrize(
    "host,code",
    [
        ("", "invalid_host"),
        (" acme.snowflakecomputing.com", "invalid_host"),
        ("169.254.169.254", "ip_address_not_allowed"),
        ("127.0.0.1", "ip_address_not_allowed"),
        ("::1", "invalid_host"),
        ("user@acme.snowflakecomputing.com", "invalid_host"),
        ("acme.snowflakecomputing.com:443", "invalid_host"),
        ("acme.snowflakecomputing.com/path", "invalid_host"),
        ("acme.snowflakecomputing.com.evil.example", "not_a_vendor_domain"),
        ("acmesnowflakecomputing.com", "not_a_vendor_domain"),
        ("evil.example", "not_a_vendor_domain"),
        (".snowflakecomputing.com", "invalid_host"),
        ("snowflakecomputing.com", "not_a_vendor_domain"),
        ("acme.snowflakecomputing.com.", "invalid_host"),
        ("xn--acme-9ua.snowflakecomputing.com", "invalid_host"),
        ("acmé.snowflakecomputing.com", "invalid_host"),
        ("ac me.snowflakecomputing.com", "invalid_host"),
        ("other.snowflakecomputing.com", "account_mismatch"),
    ],
)
def test_hostile_or_wrong_hosts_are_rejected(host, code):
    with pytest.raises(TargetRejected) as caught:
        validate_target("snowflake", host, expected_account="acme", resolver=PUBLIC)
    assert caught.value.code == code


@pytest.mark.parametrize(
    "address", ["10.0.0.5", "192.168.1.1", "127.0.0.1", "169.254.169.254", "::1", "::ffff:10.0.0.5", "100.64.0.1", "0.0.0.0", "fe80::1"]
)
def test_a_name_that_resolves_to_a_non_public_address_is_rejected(address):
    with pytest.raises(TargetRejected) as caught:
        validate_target(
            "snowflake", "acme.snowflakecomputing.com", expected_account="acme", resolver=lambda h: ["54.230.10.4", address]
        )
    assert caught.value.code == "non_public_address"


def test_a_failed_or_empty_lookup_is_a_rejection():
    def boom(host):
        raise OSError("dns")

    for resolver in (boom, lambda h: []):
        with pytest.raises(TargetRejected) as caught:
            validate_target("snowflake", "acme.snowflakecomputing.com", expected_account="acme", resolver=resolver)
        assert caught.value.code == "dns_failed"


def test_unsupported_engine_and_databricks_hosts():
    with pytest.raises(TargetRejected):
        validate_target("postgres", "acme.snowflakecomputing.com", expected_account="acme", resolver=PUBLIC)
    ok = validate_target("databricks", "dbc-1234.cloud.databricks.com", expected_account="dbc-1234", resolver=PUBLIC)
    assert ok.account == "dbc-1234"


def test_a_changed_resolution_is_detected_later():
    target = validate_target("snowflake", "acme.snowflakecomputing.com", expected_account="acme", resolver=PUBLIC)
    verify_still_pinned(target, resolver=PUBLIC)
    with pytest.raises(TargetRejected) as new:
        verify_still_pinned(target, resolver=lambda h: ["54.230.10.99"])
    assert new.value.code == "address_changed"
    with pytest.raises(TargetRejected) as private:
        verify_still_pinned(target, resolver=lambda h: ["10.0.0.1"])
    assert private.value.code == "non_public_address"


# ---- store and secrets (T4, T8, T16) -------------------------------------------------------------------------------


def _record(tenant="t1", cid="c1", host="acme.snowflakecomputing.com", **over):
    base = dict(
        tenant_id=tenant, connection_id=cid, engine="snowflake", host=host, account="acme", user="U", role="R",
        warehouse="W", database="DB", default_schema="S", allowed_tables=("db.s.orders",), secret_name="key-1", status="active",
    )
    base.update(over)
    return ConnectionRecord(**base)


def test_another_tenant_cannot_read_a_connection_and_cannot_tell_it_exists():
    store = ConnectionStore()
    store.add(_record())
    assert store.get("t1", "c1").connection_id == "c1"
    with pytest.raises(ConnectionNotFound) as other:
        store.get("t2", "c1")
    with pytest.raises(ConnectionNotFound) as missing:
        store.get("t2", "nope")
    assert str(other.value) == str(missing.value)
    assert store.list("t2") == []


def test_one_host_cannot_be_claimed_by_two_tenants_but_one_tenant_may_reuse_it():
    store = ConnectionStore()
    store.add(_record())
    with pytest.raises(ConnectionConflict) as claimed:
        store.add(_record(tenant="t2", cid="c2"))
    assert claimed.value.code == "target_claimed"
    with pytest.raises(ConnectionConflict) as taken:
        store.add(_record(cid="c1"))
    assert taken.value.code == "connection_id_taken"
    store.add(_record(cid="c3"))  # same tenant, same host: fine
    store.set_status("t1", "c1", "removed")
    store.set_status("t1", "c3", "removed")
    store.add(_record(tenant="t2", cid="c4"))  # released once removed


@pytest.mark.parametrize(
    "over",
    [
        {"allowed_tables": ()},
        {"allowed_tables": ("db.s.*",)},
        {"allowed_tables": ("DB.S.Orders",)},
        {"allowed_tables": ("orders",)},
        {"allowed_tables": ("db.s.",)},
        {"secret_name": None},
        {"auth_method": "federated"},  # a federated connection stores no secret
        {"status": "weird"},
        {"auth_method": "password"},
    ],
)
def test_invalid_records_are_refused(over):
    with pytest.raises(ValueError):
        _record(**over)


def test_a_federated_record_carries_no_secret_reference():
    assert _record(auth_method="federated", secret_name=None).secret_name is None


def test_secrets_are_tenant_scoped_versioned_and_deletable():
    secrets = TenantSecretStore()
    assert secrets.put("t1", "k", "value-one") == 1
    assert secrets.put("t1", "k", "value-two") == 2  # rotation bumps the version
    assert secrets.read("t1", "k") == "value-two"
    with pytest.raises(KeyError):
        secrets.read("t2", "k")
    assert "value" not in repr(secrets)
    receipt = secrets.delete("t1", "k")
    assert receipt.secret_name == "k" and not secrets.exists("t1", "k")
    with pytest.raises(KeyError):
        secrets.read("t1", "k")
    with pytest.raises(KeyError):
        secrets.delete("t1", "k")


# ---- setup script and grants (T3, T7, T15) -------------------------------------------------------------------------

SCOPE = ConnectScope("ANALYTICS", (("PUBLIC", "ORDERS"), ("PUBLIC", "CUSTOMERS"), ("MART", "DAILY")))
SUFFIX = "a1b2c3d4"
NAMES = Names.for_connection(SUFFIX)


def _script(**kw):
    return generate_snowflake_setup(SCOPE, suffix=SUFFIX, public_key_body=KEY_BODY, **kw)


def test_the_script_grants_only_usage_and_select_on_the_named_objects():
    script = _script()
    grants = [s for s in script.statements if s.startswith("GRANT")]
    pattern = r"GRANT (USAGE|SELECT) ON (WAREHOUSE|DATABASE|SCHEMA|TABLE) .+ TO ROLE \w+"
    assert all(re.fullmatch(pattern, s) or s.startswith("GRANT ROLE") for s in grants)
    text = "\n".join(script.statements)
    for forbidden in ("ALL ", "FUTURE", "OWNERSHIP", "GRANT OPTION", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "ACCOUNTADMIN", "*"):
        assert forbidden not in text
    assert 'GRANT SELECT ON TABLE "ANALYTICS"."MART"."DAILY"' in text
    assert sum(s.startswith("GRANT SELECT") for s in script.statements) == 3
    assert script.digest == digest_statements(script.statements)
    assert script.digest == _script().digest  # deterministic


def test_every_object_name_carries_the_suffix_and_teardown_drops_only_those():
    script = _script()
    assert all(SUFFIX.upper() in drop for drop in script.teardown)
    assert {d.split()[-1] for d in script.teardown} == {NAMES.user, NAMES.role, NAMES.warehouse, NAMES.monitor}


def test_the_summary_is_plain_language_for_the_approval_screen():
    summary = " ".join(_script(credit_quota=3).summary)
    assert "read-only" in summary and "3 credit" in summary and "cannot write" in summary


def test_allowed_networks_add_a_policy_and_a_teardown_step():
    script = _script(allowed_networks=["35.238.115.103/32", "34.82.248.0/24"])
    text = "\n".join(script.statements)
    assert "ALLOWED_IP_LIST = ('35.238.115.103/32', '34.82.248.0/24')" in text and "SET NETWORK_POLICY" in text
    assert any("DROP NETWORK POLICY" in d for d in script.teardown)
    assert "sign in only from 2" in " ".join(script.summary)


@pytest.mark.parametrize("bad", [["0.0.0.0/0"], ["::/0"], ["10.0.0.0/8"], ["not-an-ip"], ["35.1.1.1/16"]])
def test_open_private_or_malformed_networks_are_refused(bad):
    with pytest.raises(ScopeError):
        _script(allowed_networks=bad)


@pytest.mark.parametrize(
    "build",
    [
        lambda: ConnectScope("DB", ()),
        lambda: ConnectScope("DB", (("S", "*"),)),
        lambda: ConnectScope("DB", (("S%", "T"),)),
        lambda: ConnectScope("", (("S", "T"),)),
        lambda: ConnectScope("DB", (("S", "T\n"),)),
        lambda: ConnectScope("DB", (("S", "T"), ("S", "T"))),
        lambda: generate_snowflake_setup(SCOPE, suffix="XYZ", public_key_body=KEY_BODY),
        lambda: generate_snowflake_setup(SCOPE, suffix="a1b2c3d4; DROP", public_key_body=KEY_BODY),
        lambda: generate_snowflake_setup(SCOPE, suffix=SUFFIX, public_key_body="short"),
        lambda: generate_snowflake_setup(SCOPE, suffix=SUFFIX, public_key_body=KEY_BODY + "'; DROP"),
        lambda: generate_snowflake_setup(SCOPE, suffix=SUFFIX, public_key_body=KEY_BODY, credit_quota=0),
        lambda: generate_snowflake_setup(SCOPE, suffix=SUFFIX, public_key_body=KEY_BODY, credit_quota=True),
    ],
)
def test_unsafe_scope_or_inputs_never_produce_a_script(build):
    with pytest.raises(ScopeError):
        build()


def test_a_quote_in_a_name_is_escaped_not_executed():
    scope = ConnectScope("DB", (('S"; DROP TABLE x; --', "T"),))
    text = "\n".join(generate_snowflake_setup(scope, suffix=SUFFIX, public_key_body=KEY_BODY).statements)
    assert '"S""; DROP TABLE x; --"' in text


def _rows_from(script):
    rows = []
    for s in script.statements:
        m = re.match(r"GRANT (USAGE|SELECT) ON (WAREHOUSE|DATABASE|SCHEMA|TABLE) (.+) TO ROLE", s)
        if m:
            rows.append({"privilege": m[1], "granted_on": m[2], "name": m[3], "grant_option": "false"})
    return rows


def test_what_the_script_grants_verifies_clean():
    report = verify_grants(_rows_from(_script()), SCOPE, NAMES)
    assert report.ok and report.code is None


def test_a_missing_grant_is_reported():
    report = verify_grants(_rows_from(_script())[:-1], SCOPE, NAMES)
    assert not report.ok and report.code == "grants_missing" and len(report.missing) == 1


def test_an_extra_table_is_unexpected():
    extra = {"privilege": "SELECT", "granted_on": "TABLE", "name": 'ANALYTICS.PUBLIC."SECRET_TABLE"', "grant_option": "false"}
    report = verify_grants(_rows_from(_script()) + [extra], SCOPE, NAMES)
    assert report.code == "grants_unexpected" and "SECRET_TABLE" in report.unexpected[0]


@pytest.mark.parametrize(
    "row",
    [
        {"privilege": "INSERT", "granted_on": "TABLE", "name": 'ANALYTICS.PUBLIC."ORDERS"'},
        {"privilege": "OWNERSHIP", "granted_on": "TABLE", "name": 'ANALYTICS.PUBLIC."ORDERS"'},
        {"privilege": "ALL", "granted_on": "SCHEMA", "name": "ANALYTICS.PUBLIC"},
        {"privilege": "CREATE TABLE", "granted_on": "SCHEMA", "name": "ANALYTICS.PUBLIC"},
        {"privilege": "SELECT", "granted_on": "TABLE", "name": "ANALYTICS.PUBLIC.<TABLE>"},
        {"privilege": "USAGE", "granted_on": "ROLE", "name": "SYSADMIN"},
        {"privilege": "SELECT", "granted_on": "TABLE", "name": 'ANALYTICS.PUBLIC."ORDERS"', "grant_option": "true"},
        {"privilege": "SELECT", "granted_on": "TABLE", "name": "ANALYTICS.PUBLIC.*"},
    ],
)
def test_write_ownership_inheritance_future_wildcard_and_grant_option_are_dangerous(row):
    report = verify_grants(_rows_from(_script()) + [{"grant_option": "false", **row}], SCOPE, NAMES)
    assert not report.ok and report.code == "grants_dangerous"


def test_names_compare_with_snowflake_folding_and_quoting():
    assert _parts('ANALYTICS.PUBLIC."ORDERS"') == ("ANALYTICS", "PUBLIC", "ORDERS")
    assert _parts("analytics.public.orders") == ("ANALYTICS", "PUBLIC", "ORDERS")
    assert _parts('"a.b".c') == ("a.b", "C")
    assert _parts('"he said ""hi"""') == ('he said "hi"',)


def test_mixed_case_quoted_tables_verify_exactly():
    scope = ConnectScope("db", (("s", "Orders"),))
    script = generate_snowflake_setup(scope, suffix=SUFFIX, public_key_body=KEY_BODY)
    assert verify_grants(_rows_from(script), scope, NAMES).ok
    assert scope.allowed_tables == ("db.s.orders",)


def test_the_user_must_hold_only_our_role():
    assert verify_user_roles([{"role": NAMES.role, "granted_to": "USER"}], NAMES).ok
    assert verify_user_roles([{"role": NAMES.role}, {"role": "SYSADMIN"}], NAMES).code == "grants_unexpected"
    assert verify_user_roles([], NAMES).code == "grants_missing"


# ---- records (T13, T14, T15, T22) ----------------------------------------------------------------------------------


def _recorder(chain=None):
    return SetupRecorder(chain or AuditChain(), tenant_id="t1", connection_id="c1", admin_id="admin@customer.example")


def test_a_full_setup_leaves_a_verifiable_chain_without_statements_or_secrets():
    chain = AuditChain()
    rec = _recorder(chain)
    rec.watch("admin-token-0123456789")
    script = _script()
    digest = rec.consent_shown(script.statements, engine="snowflake", host="acme.snowflakecomputing.com")
    rec.approved(digest)
    rec.require_approved(script.statements)
    rec.executed(steps=len(script.statements), outcome="ok")
    rec.verified(ok=True, failed_checks=[])
    rec.torn_down(dropped=4)
    secrets = TenantSecretStore()
    secrets.put("t1", "key-1", "x" * 20)
    rec.credential_deleted(secrets.delete("t1", "key-1"))
    assert chain.verify() and chain.events[0].event_type == "connect.consent_shown"
    dump = json.dumps([e.model_dump(mode="json") for e in chain.events])
    assert "GRANT" not in dump and "RSA_PUBLIC_KEY" not in dump and "admin-token-0123456789" not in dump
    assert all(e.actor_id == "admin@customer.example" for e in chain.events)


def test_approval_binds_the_exact_statements_shown():
    rec = _recorder()
    script = _script()
    digest = rec.consent_shown(script.statements, engine="snowflake", host="h")
    with pytest.raises(ApprovalMismatch):
        rec.approved("0" * 64)
    with pytest.raises(ApprovalMismatch):
        rec.require_approved(script.statements)  # not approved yet
    rec.approved(digest)
    rec.require_approved(script.statements)
    with pytest.raises(ApprovalMismatch):
        rec.require_approved(script.statements + ("GRANT SELECT ON TABLE x.y.z TO ROLE r",))
    with pytest.raises(ApprovalMismatch):
        rec.require_approved(tuple(reversed(script.statements)))


@pytest.mark.parametrize(
    "payload",
    [
        {"admin_token": "x"},
        {"nested": {"password": "x"}},
        {"private_key": "x"},
        {"statements": ["x"]},
        {"note": "-----BEGIN PRIVATE KEY-----"},
        {"blob": b"bytes"},
    ],
)
def test_a_record_that_looks_like_it_holds_secrets_is_refused(payload):
    with pytest.raises(SecretInRecord):
        _recorder()._emit("connect.test", payload)


def test_a_watched_token_is_refused_anywhere_in_a_record_and_forgotten_on_close():
    rec = _recorder()
    rec.watch("tok-abcdefghijkl")
    with pytest.raises(SecretInRecord):
        rec.failed("sign-in failed for tok-abcdefghijkl")
    rec.close()
    rec.failed("sign-in failed for tok-abcdefghijkl")  # no longer watched


# ---- gateway wiring (T7) -------------------------------------------------------------------------------------------


def _wired(**over):
    store, secrets = ConnectionStore(), TenantSecretStore()
    secrets.put("t1", "key-1", base64.b64encode(b"fake-der-bytes").decode())
    store.add(_record(**over))
    return store, secrets


def test_the_gateway_is_built_from_the_tenants_record_only(monkeypatch):
    seen = {}

    def fake(config, *, allowed_tables, connector=None, **options):
        seen.update(config=config, tables=tuple(allowed_tables))
        return "gateway"

    monkeypatch.setattr("app.connect.gateway.snowflake_gateway", fake)
    store, secrets = _wired(allowed_tables=("db.s.orders", "db.s.customers"))
    assert snowflake_gateway_for(store, secrets, "t1", "c1") == "gateway"
    assert seen["tables"] == ("db.s.orders", "db.s.customers")
    assert seen["config"].private_key_der == b"fake-der-bytes" and seen["config"].role == "R"
    assert "fake-der" not in repr(seen["config"])


@pytest.mark.parametrize(
    "tenant,over,code",
    [
        ("t2", {}, "connection_not_found"),
        ("t1", {"status": "pending"}, "connection_not_active"),
        ("t1", {"status": "suspended"}, "connection_not_active"),
        ("t1", {"engine": "databricks"}, "engine_not_supported"),
        ("t1", {"auth_method": "federated", "secret_name": None}, "federation_not_available"),
    ],
)
def test_an_unavailable_connection_gives_a_stable_code(tenant, over, code):
    store, secrets = _wired(**over)
    with pytest.raises(ConnectionUnavailable) as caught:
        snowflake_gateway_for(store, secrets, tenant, "c1")
    assert caught.value.code == code


def test_a_missing_or_corrupt_credential_never_echoes_why():
    store, secrets = _wired()
    secrets.delete("t1", "key-1")
    with pytest.raises(ConnectionUnavailable) as gone:
        snowflake_gateway_for(store, secrets, "t1", "c1")
    secrets.put("t1", "key-1", "not base64 !!")
    with pytest.raises(ConnectionUnavailable) as bad:
        snowflake_gateway_for(store, secrets, "t1", "c1")
    assert gone.value.code == bad.value.code == "credential_unavailable"


def test_the_real_gateway_refuses_a_table_the_admin_did_not_name():
    store, secrets = _wired(allowed_tables=("db.s.somethingelse",))
    gateway = snowflake_gateway_for(store, secrets, "t1", "c1")
    with pytest.raises(QueryRejected):
        gateway.estimate(_plan("snowflake"))  # refused before any connection is opened
