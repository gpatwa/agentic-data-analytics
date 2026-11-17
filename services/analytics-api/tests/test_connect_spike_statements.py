"""The product-run spike statements are generated, bounded to this run's names, and never touch a secret. Offline only."""

import re
from pathlib import Path

import pytest

from app.connect.spike.statements import (
    PREFIX,
    TAG,
    Names,
    SpikeError,
    negative_tests,
    per_setup,
    phase_a,
    phase_d,
    preflight,
    quote,
    render_markdown,
    service_user_checks,
    setup_role_teardown,
)

S = "a1b2c3d4"
N = Names.for_run(S)
KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA" + "A" * 120
USER = "OWNER_USER"
ROOT = Path(__file__).resolve().parents[3]


def everything():
    return [
        *preflight(), *phase_a(N, USER, 8765), *per_setup(N, KEY), *(s for _, s in negative_tests(N)),
        *(s for _, s in service_user_checks(N)), *setup_role_teardown(N), *phase_d(N, USER),
    ]


def test_every_name_carries_the_prefix_and_the_run_suffix():
    assert all(n.startswith(PREFIX) and S.upper() in n for n in N.owned)
    assert len(set(N.owned)) == len(N.owned)


@pytest.mark.parametrize("bad", ["", "XYZ", "A1B2C3D4", "a1b2c3d", "a1b2c3d4e", "a1b2c3d;"])
def test_a_bad_suffix_is_refused(bad):
    with pytest.raises(SpikeError):
        Names.for_run(bad)


def test_no_statement_reads_or_requests_a_client_secret_or_uses_a_confidential_client():
    text = "\n".join(everything()).upper()
    for forbidden in ("SYSTEM$SHOW_OAUTH_CLIENT_SECRETS", "CONFIDENTIAL", "CLIENT_SECRET", "OAUTH_CLIENT_RSA_PUBLIC_KEY"):
        assert forbidden not in text


def test_nothing_outside_the_negative_tests_grants_power_or_an_admin_role():
    risky = ("MANAGE GRANTS", "OWNERSHIP", "ACCOUNTADMIN", "SYSADMIN", "SECURITYADMIN", "ORGADMIN")
    negatives = {s for _, s in negative_tests(N)}
    for statement in everything():
        if statement in negatives:
            continue
        assert not any(word in statement.upper() for word in risky), statement
    assert all(s.startswith(("GRANT", "CREATE")) for s in negatives)


def test_grant_option_appears_only_on_the_ceiling_objects_granted_to_the_setup_role():
    with_option = [s for s in phase_a(N, USER, 8765) if "WITH GRANT OPTION" in s]
    assert len(with_option) == 5
    assert all(f"TO ROLE {N.setup_role} WITH GRANT OPTION" in s for s in with_option)
    assert not any("payroll_outside" in s for s in with_option)
    assert not any("WITH GRANT OPTION" in s for s in per_setup(N, KEY))


def test_the_outside_table_is_never_granted_except_by_the_negative_test():
    granting = [s for s in everything() if "payroll_outside" in s and s.startswith("GRANT")]
    assert granting == [negative_tests(N)[0][1]]


def test_the_integration_is_a_public_pkce_client_with_no_refresh_tokens_and_a_local_non_tls_redirect_only():
    (integration,) = [s for s in phase_a(N, USER, 8765) if s.startswith("CREATE SECURITY INTEGRATION")]
    for part in ("OAUTH_CLIENT_TYPE = 'PUBLIC'", "OAUTH_ENFORCE_PKCE = TRUE", "OAUTH_ISSUE_REFRESH_TOKENS = FALSE",
                 "OAUTH_ACCESS_TOKEN_VALIDITY = 600", "http://localhost:8765/callback", f"COMMENT = '{TAG}'"):
        assert part in integration
    assert "BLOCKED_ROLES_LIST" not in integration  # the default blocked roles are left alone
    assert all("ALTER ACCOUNT" not in s and "SET OAUTH_ADD_PRIVILEGED" not in s for s in everything())  # read, never changed


@pytest.mark.parametrize("port", [0, 80, 1023, 65536, True, "8765", None])
def test_a_bad_port_is_refused(port):
    with pytest.raises(SpikeError):
        phase_a(N, USER, port)


def test_the_resource_monitor_caps_the_spend():
    (monitor,) = [s for s in phase_a(N, USER, 8765) if s.startswith("CREATE RESOURCE MONITOR")]
    assert "CREDIT_QUOTA = 1" in monitor and "DO SUSPEND" in monitor
    (warehouse,) = [s for s in phase_a(N, USER, 8765) if s.startswith("CREATE WAREHOUSE")]
    assert "XSMALL" in warehouse and "AUTO_SUSPEND = 60" in warehouse and f"RESOURCE_MONITOR = {N.monitor}" in warehouse


def test_everything_created_is_tagged_so_a_sweep_can_find_it():
    creates = [s for s in phase_a(N, USER, 8765) if re.match(r"CREATE (DATABASE|WAREHOUSE|ROLE|SECURITY INTEGRATION)", s)]
    assert creates and all(f"COMMENT = '{TAG}'" in s for s in creates)


def test_every_drop_targets_a_name_generated_for_this_run_and_only_if_exists_on_those():
    drops = [s for s in [*setup_role_teardown(N), *phase_d(N, USER)] if s.startswith("DROP")]
    assert drops
    for drop in drops:
        target = drop.split()[-1]
        assert target in N.owned, drop
    assert all("IF EXISTS" in d for d in phase_d(N, USER) if d.startswith("DROP"))


def test_the_owners_user_is_only_granted_and_revoked_the_setup_role():
    touching = [s for s in everything() if USER in s]
    assert touching == [f'GRANT ROLE {N.setup_role} TO USER "{USER}"', f'REVOKE ROLE {N.setup_role} FROM USER "{USER}"']


def test_a_hostile_user_or_table_name_is_quoted_not_executed():
    assert quote('x"; DROP DATABASE y; --') == '"x""; DROP DATABASE y; --"'
    with pytest.raises(SpikeError):
        quote("bad\nname")
    statements = phase_a(N, 'evil"; DROP ROLE x; --', 8765)
    grant = [s for s in statements if "TO USER" in s][0]
    assert grant.endswith('"evil""; DROP ROLE x; --"')


def test_the_per_setup_statements_need_a_real_looking_key():
    with pytest.raises(SpikeError):
        per_setup(N, "short")
    with pytest.raises(SpikeError):
        per_setup(N, KEY + "'; DROP")


def test_service_user_checks_expect_the_outside_table_and_writes_to_fail():
    checks = dict((s, ok) for ok, s in service_user_checks(N))
    assert checks[f"SELECT COUNT(*) FROM {N.table('payroll_outside')}"] is False
    assert [s for s, ok in checks.items() if s.startswith("INSERT") and ok] == []
    assert sum(checks.values()) == 5


def test_the_reviewable_document_equals_the_generator():
    path = ROOT / "docs/design/CONNECT_PRODUCT_RUN_SPIKE_STATEMENTS.md"
    assert path.read_text() == render_markdown(), "regenerate: python3 -c 'from app.connect.spike.statements import render_markdown; print(render_markdown(), end=\"\")'"
