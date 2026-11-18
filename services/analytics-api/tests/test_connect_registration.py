"""Product-run registration (CM5a): the one-time script, its verifier, its teardown, and the per-connection statements held
inside the scope ceiling. Offline only; nothing here contacts an account. The statement forms for the role, grants, warehouse,
monitor and integration are the ones the product-run spike ran live; the network policy and the https redirect are not."""

import re
from pathlib import Path

import pytest

from app.connect.grants import ConnectScope, ScopeError
from app.connect.registration import (
    ACCESS_TOKEN_SECONDS,
    OutsideCeiling,
    RegNames,
    generate_registration,
    per_connection_statements,
    render_markdown,
    validate_redirect,
    verify_connection_grants,
    verify_registration,
)
from app.connect.spike import statements as spike

ROOT = Path(__file__).resolve().parents[3]
SUFFIX = "a1b2c3d4"
NAMES = RegNames.for_registration(SUFFIX)
CEILING = ConnectScope("ANALYTICS", (("PUBLIC", "ORDERS"), ("PUBLIC", "CUSTOMERS"), ("MART", "DAILY")))
REDIRECT = "https://connect.example.com/oauth/callback"
ACCOUNT = "EI45621"
KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA" + "A" * 120


def script(**over):
    args = dict(suffix=SUFFIX, redirect_uri=REDIRECT, egress_networks=["35.238.115.103/32", "34.82.248.0/24"], setup_users=["ADMIN_USER", "OTHER"])
    args.update(over)
    return generate_registration(CEILING, **args)


# ---- names and the generated script -------------------------------------------------------------------------------


def test_every_name_carries_the_prefix_and_suffix_and_none_repeat():
    assert all(n.startswith("COMPASS_REG_") and SUFFIX.upper() in n for n in NAMES.owned)
    assert len(set(NAMES.owned)) == len(NAMES.owned)


@pytest.mark.parametrize("bad", ["", "XYZ", "A1B2C3D4", "a1b2c3d", "a1b2c3d4e", "a1b2c3d;"])
def test_a_bad_suffix_is_refused(bad):
    with pytest.raises(ScopeError):
        RegNames.for_registration(bad)


def test_the_ceiling_grants_carry_grant_option_and_the_account_privileges_do_not():
    s = script().statements
    with_option = [x for x in s if "WITH GRANT OPTION" in x]
    assert len(with_option) == 1 + 2 + 3 + 1  # database, two schemas, three tables, warehouse
    assert all(f"TO ROLE {NAMES.setup_role} WITH GRANT OPTION" in x for x in with_option)
    account = [x for x in s if x.endswith("ON ACCOUNT TO ROLE " + NAMES.setup_role) or " ON ACCOUNT " in x]
    assert sorted(account) == sorted([f"GRANT CREATE ROLE ON ACCOUNT TO ROLE {NAMES.setup_role}", f"GRANT CREATE USER ON ACCOUNT TO ROLE {NAMES.setup_role}"])


def test_nothing_grants_power_an_admin_role_or_a_secret():
    text = "\n".join(script().statements).upper()
    for forbidden in ("MANAGE GRANTS", "OWNERSHIP", "ACCOUNTADMIN", "SYSADMIN", "SECURITYADMIN", "ORGADMIN", " ALL ", "FUTURE",
                      "CONFIDENTIAL", "CLIENT_SECRET", "SHOW_OAUTH_CLIENT_SECRETS", "NON_TLS", "BLOCKED_ROLES_LIST",
                      "PRE_AUTHORIZED", "ALTER ACCOUNT", "CREATE INTEGRATION", "OAUTH_ANY_ROLE_MODE"):
        assert forbidden not in text, forbidden


def test_the_integration_is_a_public_pkce_client_with_short_tokens_no_refresh_a_fixed_https_redirect_and_a_network_policy():
    (integration,) = [x for x in script().statements if x.startswith("CREATE SECURITY INTEGRATION")]
    for part in ("OAUTH_CLIENT_TYPE = 'PUBLIC'", "OAUTH_ENFORCE_PKCE = TRUE", "OAUTH_ISSUE_REFRESH_TOKENS = FALSE",
                 f"OAUTH_ACCESS_TOKEN_VALIDITY = {ACCESS_TOKEN_SECONDS}", f"OAUTH_REDIRECT_URI = '{REDIRECT}'",
                 f"NETWORK_POLICY = {NAMES.network_policy}", "COMMENT = 'compass-connect'"):
        assert part in integration


def test_the_network_policy_exists_before_the_integration_uses_it_and_describe_is_last():
    s = script().statements
    assert s.index(next(x for x in s if x.startswith("CREATE NETWORK POLICY"))) < s.index(next(x for x in s if x.startswith("CREATE SECURITY INTEGRATION")))
    assert s[-1] == f"DESCRIBE SECURITY INTEGRATION {NAMES.integration}"
    assert "ALLOWED_IP_LIST = ('35.238.115.103/32', '34.82.248.0/24')" in "\n".join(s)


def test_every_created_object_is_tagged():
    creates = [x for x in script().statements if re.match(r"CREATE (NETWORK POLICY|WAREHOUSE|ROLE|SECURITY INTEGRATION)", x)]
    assert len(creates) == 4 and all("COMMENT = 'compass-connect'" in x for x in creates)


def test_named_users_get_the_setup_role_and_only_that():
    s = script().statements
    grants_to_users = [x for x in s if " TO USER " in x]
    assert grants_to_users == [f'GRANT ROLE {NAMES.setup_role} TO USER "ADMIN_USER"', f'GRANT ROLE {NAMES.setup_role} TO USER "OTHER"']


def test_the_digest_is_deterministic_and_changes_with_any_input():
    assert script().digest == script().digest
    assert script().digest != script(egress_networks=["35.238.115.103/32"]).digest
    assert script().digest != script(credit_quota=2).digest


def test_the_summary_is_honest_that_the_setup_role_can_read_the_ceiling_tables():
    summary = " ".join(script(credit_quota=3).summary)
    assert "can read those tables as well" in summary and "ceiling" in summary and "3 table(s) in 2 schema(s)" in summary
    assert "3 credit(s)" in summary and "removal script" in summary and "not a secret" in summary
    assert "10 minutes and cannot be refreshed" in summary


# ---- inputs --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        "http://connect.example.com/cb", "https://10.0.0.1/cb", "https://connect.example.com:8443/cb", "https://connect.example.com/cb?x=1",
        "https://connect.example.com/cb#frag", "https://user:pw@connect.example.com/cb", "https://connect.example.com", "https://localhost/cb",
        "https://connect.example.com/../cb", "https://connect.example.com/c b", "https://connect.example.com/c'b", "", "ftp://x.example.com/cb",
        "https://connect.example.com/cb;DROP", "https://" + "a" * 200 + ".example.com/cb",
    ],
)
def test_the_redirect_must_be_one_plain_https_url(uri):
    with pytest.raises(ScopeError):
        validate_redirect(uri)


def test_a_good_redirect_passes_unchanged():
    assert validate_redirect(REDIRECT) == REDIRECT


@pytest.mark.parametrize(
    "over",
    [
        {"egress_networks": []}, {"egress_networks": ["0.0.0.0/0"]}, {"egress_networks": ["10.0.0.0/8"]}, {"egress_networks": ["nope"]},
        {"setup_users": []}, {"setup_users": [f"U{i}" for i in range(11)]}, {"setup_users": ["bad\nname"]},
        {"credit_quota": 0}, {"credit_quota": 101}, {"credit_quota": True}, {"suffix": "ZZZZZZZZ"},
    ],
)
def test_unsafe_inputs_never_produce_a_script(over):
    with pytest.raises(ScopeError):
        script(**over)


def test_a_hostile_user_name_is_quoted_not_executed():
    s = script(setup_users=['evil"; DROP ROLE x; --']).statements
    assert any(x.endswith('TO USER "evil""; DROP ROLE x; --"') for x in s)


def test_duplicate_users_are_collapsed():
    assert sum(" TO USER " in x for x in script(setup_users=["A", "A", "B"]).statements) == 2


# ---- teardown ------------------------------------------------------------------------------------------------------


def test_teardown_drops_only_this_registrations_names_in_a_safe_order():
    td = script().teardown
    targets = [x.split()[-1] for x in td if x.startswith("DROP")]
    assert set(targets) == set(NAMES.owned)
    assert td.index(f"DROP INTEGRATION IF EXISTS {NAMES.integration}") < td.index(f"DROP NETWORK POLICY IF EXISTS {NAMES.network_policy}")
    assert [x for x in td if x.startswith("REVOKE")] == [f'REVOKE ROLE {NAMES.setup_role} FROM USER "ADMIN_USER"', f'REVOKE ROLE {NAMES.setup_role} FROM USER "OTHER"']
    assert all(x.startswith(("REVOKE ROLE", "DROP ")) for x in td)


# ---- verification --------------------------------------------------------------------------------------------------


def grant_rows(statements, account=ACCOUNT):
    rows = []
    for x in statements:
        m = re.match(r"GRANT (CREATE ROLE|CREATE USER|USAGE|SELECT) ON (ACCOUNT|WAREHOUSE|DATABASE|SCHEMA|TABLE)(?: (.+?))? TO ROLE \w+( WITH GRANT OPTION)?$", x)
        if m:
            rows.append({"privilege": m[1], "granted_on": m[2], "name": account if m[2] == "ACCOUNT" else m[3], "grant_option": "true" if m[4] else "false"})
    return rows


def integration_rows(**override):
    props = {
        "ENABLED": "true", "OAUTH_REDIRECT_URI": REDIRECT, "OAUTH_ALTERNATE_REDIRECT_URIS": "", "OAUTH_CLIENT_TYPE": "PUBLIC",
        "OAUTH_ISSUE_REFRESH_TOKENS": "false", "OAUTH_ACCESS_TOKEN_VALIDITY": "600", "OAUTH_ENFORCE_PKCE": "true",
        "OAUTH_USE_SECONDARY_ROLES": "NONE", "PRE_AUTHORIZED_ROLES_LIST": "", "BLOCKED_ROLES_LIST": "ACCOUNTADMIN,ORGADMIN,SECURITYADMIN",
        "ALLOWED_ROLES_LIST": "", "OAUTH_ALLOW_NON_TLS_REDIRECT_URI": "false", "NETWORK_POLICY": NAMES.network_policy,
    }
    props.update(override)
    return [{"property": k, "property_value": v} for k, v in props.items()]


def check(grants=None, integration=None):
    s = script().statements
    return verify_registration(grants if grants is not None else grant_rows(s), integration if integration is not None else integration_rows(),
                               CEILING, NAMES, account=ACCOUNT, redirect_uri=REDIRECT)


def test_what_the_script_creates_verifies_clean():
    report = check()
    assert report.ok and report.code is None
    assert len(grant_rows(script().statements)) == 2 + 1 + 2 + 3 + 1


def test_a_missing_grant_is_reported():
    report = check(grant_rows(script().statements)[:-1])
    assert report.code == "registration_missing" and len(report.missing) == 1


def test_an_extra_table_is_unexpected():
    extra = {"privilege": "SELECT", "granted_on": "TABLE", "name": 'ANALYTICS.PUBLIC."SECRETS"', "grant_option": "true"}
    assert check([*grant_rows(script().statements), extra]).code == "registration_unexpected"


@pytest.mark.parametrize(
    "row",
    [
        {"privilege": "MANAGE GRANTS", "granted_on": "ACCOUNT", "name": ACCOUNT},
        {"privilege": "CREATE INTEGRATION", "granted_on": "ACCOUNT", "name": ACCOUNT},
        {"privilege": "OWNERSHIP", "granted_on": "TABLE", "name": 'ANALYTICS.PUBLIC."ORDERS"'},
        {"privilege": "INSERT", "granted_on": "TABLE", "name": 'ANALYTICS.PUBLIC."ORDERS"'},
        {"privilege": "USAGE", "granted_on": "ROLE", "name": "SYSADMIN"},
        {"privilege": "SELECT", "granted_on": "TABLE", "name": "ANALYTICS.PUBLIC.<TABLE>"},
        {"privilege": "SELECT", "granted_on": "TABLE", "name": "ANALYTICS.PUBLIC.*"},
    ],
)
def test_power_ownership_inheritance_future_and_wildcard_grants_are_dangerous(row):
    assert check([*grant_rows(script().statements), {"grant_option": "false", **row}]).code == "registration_dangerous"


def test_a_ceiling_grant_without_grant_option_would_break_product_run_and_is_reported():
    rows = grant_rows(script().statements)
    rows[-1] = {**rows[-1], "grant_option": "false"}
    report = check(rows)
    assert report.code == "registration_grant_option" and "without grant option" in report.option_problems[0]


def test_an_account_privilege_that_can_be_passed_on_is_reported():
    rows = [{**r, "grant_option": "true"} if r["granted_on"] == "ACCOUNT" else r for r in grant_rows(script().statements)]
    assert "with grant option" in check(rows).option_problems[0]


@pytest.mark.parametrize(
    "override,prop",
    [
        ({"OAUTH_CLIENT_TYPE": "CONFIDENTIAL"}, "oauth_client_type"), ({"OAUTH_ENFORCE_PKCE": "false"}, "oauth_enforce_pkce"),
        ({"OAUTH_ISSUE_REFRESH_TOKENS": "true"}, "oauth_issue_refresh_tokens"), ({"OAUTH_ACCESS_TOKEN_VALIDITY": "3600"}, "oauth_access_token_validity"),
        ({"OAUTH_REDIRECT_URI": "https://evil.example/cb"}, "oauth_redirect_uri"), ({"OAUTH_ALTERNATE_REDIRECT_URIS": "https://x.example/cb"}, "oauth_alternate_redirect_uris"),
        ({"OAUTH_ALLOW_NON_TLS_REDIRECT_URI": "true"}, "oauth_allow_non_tls_redirect_uri"), ({"BLOCKED_ROLES_LIST": "ORGADMIN"}, "blocked_roles_list"),
        ({"PRE_AUTHORIZED_ROLES_LIST": "SOME_ROLE"}, "pre_authorized_roles_list"), ({"ALLOWED_ROLES_LIST": "SOME_ROLE"}, "allowed_roles_list"),
        ({"OAUTH_USE_SECONDARY_ROLES": "IMPLICIT"}, "oauth_use_secondary_roles"), ({"NETWORK_POLICY": ""}, "network_policy"),
        ({"NETWORK_POLICY": "SOME_OTHER_POLICY"}, "network_policy"), ({"ENABLED": "false"}, "enabled"),
    ],
)
def test_every_integration_setting_that_matters_is_checked(override, prop):
    report = check(integration=integration_rows(**override))
    assert report.code == "registration_integration" and prop in report.integration_problems


def test_the_defaults_the_spike_observed_verify_clean():
    """The spike's DESCRIBE showed the default blocked list `ACCOUNTADMIN,ORGADMIN,SECURITYADMIN`, secondary roles NONE and
    empty pre-authorized and allowed lists; those must verify."""
    assert check(integration=integration_rows()).ok


def test_the_code_names_the_most_serious_problem_first():
    rows = [*grant_rows(script().statements)[:-1], {"privilege": "MANAGE GRANTS", "granted_on": "ACCOUNT", "name": ACCOUNT, "grant_option": "false"}]
    assert check(rows, integration_rows(OAUTH_ENFORCE_PKCE="false")).code == "registration_dangerous"


# ---- per connection, inside the ceiling -----------------------------------------------------------------------------


def test_a_connection_inside_the_ceiling_gets_read_only_statements_and_verifies():
    scope = ConnectScope("ANALYTICS", (("PUBLIC", "ORDERS"),))
    statements, conn_names = per_connection_statements(NAMES, CEILING, scope, connection_suffix="0f0f0f0f", public_key_body=KEY)
    assert conn_names.role == "COMPASS_RO_0F0F0F0F" and conn_names.warehouse == NAMES.warehouse
    assert not any("WITH GRANT OPTION" in s or "MANAGE GRANTS" in s or "OWNERSHIP" in s for s in statements)
    rows = [{"privilege": m[1], "granted_on": m[2], "name": m[3], "grant_option": "false"}
            for s in statements if (m := re.match(r"GRANT (USAGE|SELECT) ON (WAREHOUSE|DATABASE|SCHEMA|TABLE) (.+) TO ROLE", s))]
    assert verify_connection_grants(rows, scope, conn_names).ok


def test_the_whole_ceiling_is_allowed_and_a_subset_too():
    for scope in (CEILING, ConnectScope("ANALYTICS", (("MART", "DAILY"), ("PUBLIC", "CUSTOMERS")))):
        per_connection_statements(NAMES, CEILING, scope, connection_suffix="0f0f0f0f", public_key_body=KEY)


@pytest.mark.parametrize(
    "scope",
    [
        ConnectScope("ANALYTICS", (("PUBLIC", "PAYROLL"),)),
        ConnectScope("ANALYTICS", (("PUBLIC", "ORDERS"), ("PUBLIC", "PAYROLL"))),
        ConnectScope("OTHER_DB", (("PUBLIC", "ORDERS"),)),
        ConnectScope("ANALYTICS", (("public", "ORDERS"),)),  # case matters: a different schema
    ],
)
def test_anything_outside_the_ceiling_is_refused_before_a_statement_exists(scope):
    with pytest.raises(OutsideCeiling):
        per_connection_statements(NAMES, CEILING, scope, connection_suffix="0f0f0f0f", public_key_body=KEY)


@pytest.mark.parametrize("kw", [{"connection_suffix": "XYZ"}, {"public_key_body": "short"}, {"public_key_body": KEY + "'; DROP"}])
def test_bad_connection_inputs_are_refused(kw):
    args = dict(connection_suffix="0f0f0f0f", public_key_body=KEY)
    args.update(kw)
    with pytest.raises(ScopeError):
        per_connection_statements(NAMES, CEILING, CEILING, **args)


# ---- the spike's verified forms and the reviewable document --------------------------------------------------------


def test_the_role_and_grant_statements_match_the_forms_the_spike_ran_live():
    s = spike.Names.for_run(SUFFIX)
    spike_ceiling = ConnectScope(s.db, ((s.schema, "orders"), (s.schema, "customers")))
    reg = generate_registration(spike_ceiling, suffix=SUFFIX, redirect_uri=REDIRECT, egress_networks=["35.238.115.103/32"], setup_users=["U"])
    # The registration quotes database and schema names; the spike wrote them unquoted. For all-capital names the two are the
    # same identifier in Snowflake, so quotes around capital-only names are removed before comparing.
    unquote = lambda text: re.sub(r'"([A-Z0-9_]+)"', r"\1", text)  # noqa: E731
    normalise = lambda text: unquote(text.replace(s.setup_role, "ROLE").replace(s.warehouse, "WH"))  # noqa: E731
    spike_forms = {normalise(x) for x in spike.phase_a(s, "U", 8765) if x.startswith("GRANT") and " TO USER " not in x}
    reg_forms = {normalise(x.replace(NAMES.setup_role, s.setup_role).replace(NAMES.warehouse, s.warehouse)) for x in reg.statements if x.startswith("GRANT") and " TO USER " not in x}
    assert spike_forms == reg_forms and len(spike_forms) == 2 + 1 + 1 + 2 + 1
    spike_create = [normalise(x).split(" COMMENT")[0] for x in spike.phase_a(s, "U", 8765) if x.startswith("CREATE ROLE")]
    reg_create = [x.replace(NAMES.setup_role, "ROLE").split(" COMMENT")[0] for x in reg.statements if x.startswith("CREATE ROLE")]
    assert spike_create == reg_create == ["CREATE ROLE ROLE"]


def test_the_reviewable_document_equals_the_generator_and_claims_only_what_was_proven():
    text = (ROOT / "docs/design/CONNECT_REGISTRATION_SCRIPT.md").read_text()
    assert text == render_markdown(), "regenerate: python3 -c 'from app.connect.registration import render_markdown; print(render_markdown(), end=\"\")'"
    assert "did **not**" in text and "can read those tables as well" in text
    assert not re.search(r"supported|certified|SOC ?2|secure by", text, re.I)
