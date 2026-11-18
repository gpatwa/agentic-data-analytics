"""Product-run registration for Snowflake (CM5a): the one-time script an admin runs, its verifier, its teardown, and the
per-connection statements product-run may run afterwards, held inside a scope ceiling.

What the product-run spike showed on one account (docs/design/CONNECT_PRODUCT_RUN_SPIKE_RESULTS.md): a custom role with only
account-level `CREATE ROLE` and `CREATE USER`, plus `SELECT` and `USAGE` held `WITH GRANT OPTION` on a ceiling, can create a
read-only role and a service user and grant only within the ceiling, and a public PKCE client signs in with no client secret.
Statement forms marked "verified in the spike" below ran live; the rest are **not verified**: creating a network policy, setting
`NETWORK_POLICY` on the integration, and an `https` redirect (the spike used a local non-TLS redirect).

Database and schema names are double-quoted here and were unquoted in the spike; for all-capital names those are the same identifier.

The setup role holds `SELECT` on the ceiling tables, so it **can read them** while signed in as it. The review text says so.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.connect.grants import (
    TAG,
    ConnectScope,
    GrantsReport,
    Names,
    ScopeError,
    _label,
    _networks,
    _quote,
    _row,
    digest_statements,
    verify_grants,
)
from app.connect.redirects import is_local_dev_redirect, validate_redirect_uri

_SUFFIX = re.compile(r"^[0-9a-f]{8}$")
ACCESS_TOKEN_SECONDS = 600
SAFE_ACCOUNT_PRIVILEGES = {"CREATE ROLE", "CREATE USER"}
REQUIRED_BLOCKED = {"ACCOUNTADMIN", "ORGADMIN", "SECURITYADMIN"}
COMMENT = f"COMMENT = '{TAG}'"


class OutsideCeiling(ValueError):
    """A connection asked for something the admin did not allow at registration."""


@dataclass(frozen=True)
class RegNames:
    suffix: str
    integration: str
    setup_role: str
    monitor: str
    warehouse: str
    network_policy: str

    @classmethod
    def for_registration(cls, suffix: str) -> RegNames:
        if not _SUFFIX.match(suffix or ""):
            raise ScopeError("the registration suffix must be 8 lower-case hex characters")
        s = suffix.upper()
        return cls(suffix, f"COMPASS_REG_OAUTH_{s}", f"COMPASS_REG_SETUP_{s}", f"COMPASS_REG_MON_{s}", f"COMPASS_REG_WH_{s}", f"COMPASS_REG_NET_{s}")

    @property
    def owned(self) -> tuple[str, ...]:
        return (self.integration, self.setup_role, self.monitor, self.warehouse, self.network_policy)


@dataclass(frozen=True)
class RegistrationScript:
    statements: tuple[str, ...]
    teardown: tuple[str, ...]
    summary: tuple[str, ...]  # plain language for the admin's review page
    digest: str


def validate_redirect(uri: str, *, allow_local_dev: bool = False) -> str:
    """One fixed https callback (see `app/connect/redirects.py`; `allow_local_dev` is the sandbox-only switch, off by default)."""
    try:
        return validate_redirect_uri(uri, allow_local_dev=allow_local_dev)
    except ValueError as exc:
        raise ScopeError(str(exc)) from None


def generate_registration(
    ceiling: ConnectScope,
    *,
    suffix: str,
    redirect_uri: str,
    egress_networks: Iterable[str],
    setup_users: Iterable[str],
    credit_quota: int = 1,
    allow_local_dev_redirect: bool = False,
) -> RegistrationScript:
    names = RegNames.for_registration(suffix)
    redirect = validate_redirect(redirect_uri, allow_local_dev=allow_local_dev_redirect)
    non_tls = "OAUTH_ALLOW_NON_TLS_REDIRECT_URI = TRUE " if is_local_dev_redirect(redirect) else ""
    networks = _networks(egress_networks)
    if not networks:
        raise ScopeError("at least one Compass egress network is required")
    users = tuple(dict.fromkeys(setup_users))
    if not users or len(users) > 10:
        raise ScopeError("name between 1 and 10 Snowflake users who may use the setup role")
    quoted_users = [_quote(u) for u in users]
    if not isinstance(credit_quota, int) or isinstance(credit_quota, bool) or not 1 <= credit_quota <= 100:
        raise ScopeError("credit quota must be a whole number from 1 to 100")
    n, db, role = names, _quote(ceiling.database), names.setup_role
    listed = ", ".join(f"'{x}'" for x in networks)
    statements = (
        f"CREATE NETWORK POLICY {n.network_policy} ALLOWED_IP_LIST = ({listed}) {COMMENT}",  # not verified live
        f"CREATE RESOURCE MONITOR {n.monitor} WITH CREDIT_QUOTA = {credit_quota} TRIGGERS ON 80 PERCENT DO NOTIFY ON 100 PERCENT DO SUSPEND",
        f"CREATE WAREHOUSE {n.warehouse} WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE "
        f"INITIALLY_SUSPENDED = TRUE RESOURCE_MONITOR = {n.monitor} {COMMENT}",
        f"CREATE ROLE {role} {COMMENT}",
        f"GRANT CREATE ROLE ON ACCOUNT TO ROLE {role}",
        f"GRANT CREATE USER ON ACCOUNT TO ROLE {role}",
        f"GRANT USAGE ON DATABASE {db} TO ROLE {role} WITH GRANT OPTION",
        *(f"GRANT USAGE ON SCHEMA {db}.{_quote(s)} TO ROLE {role} WITH GRANT OPTION" for s in ceiling.schemas),
        *(f"GRANT SELECT ON TABLE {db}.{_quote(s)}.{_quote(t)} TO ROLE {role} WITH GRANT OPTION" for s, t in sorted(ceiling.tables)),
        f"GRANT USAGE ON WAREHOUSE {n.warehouse} TO ROLE {role} WITH GRANT OPTION",
        *(f"GRANT ROLE {role} TO USER {u}" for u in quoted_users),
        f"CREATE SECURITY INTEGRATION {n.integration} TYPE = OAUTH ENABLED = TRUE OAUTH_CLIENT = CUSTOM "
        f"OAUTH_CLIENT_TYPE = 'PUBLIC' OAUTH_REDIRECT_URI = '{redirect}' {non_tls}OAUTH_ENFORCE_PKCE = TRUE "
        f"OAUTH_ISSUE_REFRESH_TOKENS = FALSE OAUTH_ACCESS_TOKEN_VALIDITY = {ACCESS_TOKEN_SECONDS} "
        f"NETWORK_POLICY = {n.network_policy} {COMMENT}",  # NETWORK_POLICY and the https redirect: not verified live
        f"DESCRIBE SECURITY INTEGRATION {n.integration}",  # shows the client id, which is not a secret; the admin gives it to Compass
    )
    teardown = (
        *(f"REVOKE ROLE {role} FROM USER {u}" for u in quoted_users),
        f"DROP INTEGRATION IF EXISTS {n.integration}",
        f"DROP NETWORK POLICY IF EXISTS {n.network_policy}",
        f"DROP ROLE IF EXISTS {role}",
        f"DROP WAREHOUSE IF EXISTS {n.warehouse}",
        f"DROP RESOURCE MONITOR IF EXISTS {n.monitor}",
    )
    summary = (
        "Register Compass once, so it can set up read-only access for you later without you running a script each time.",
        "Creates a sign-in for Compass (OAuth) that works only when one of your named users signs in and approves. It issues no long-lived "
        "tokens: each sign-in lasts 10 minutes and cannot be refreshed. Admin roles (ACCOUNTADMIN, SECURITYADMIN, ORGADMIN) cannot use it.",
        f"Creates a setup role that can only create read-only roles and users and pass on read access to the {len(ceiling.tables)} table(s) "
        f"in {len(ceiling.schemas)} schema(s) of {ceiling.database} that you name here. That is the ceiling: it can never grant more.",
        "While signed in as the setup role, Compass can read those tables as well. Compass uses it only during a setup you approve.",
        f"Allows the setup role to be used by {len(users)} user(s) you name, and the sign-in only from {len(networks)} Compass network address(es).",
        f"Adds a small warehouse capped at {credit_quota} credit(s) that suspends after 60 seconds; every connection Compass sets up uses it.",
        "To remove all of this later, run the removal script. After the last statement runs, give Compass the client id it shows (not a secret).",
        *(("Sandbox only: the sign-in comes back to an address on the developer's own machine over plain http. A real deployment never does this.",) if non_tls else ()),
    )
    return RegistrationScript(statements, teardown, summary, digest_statements(statements))


# ---- verification ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistrationReport:
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    dangerous: tuple[str, ...]
    option_problems: tuple[str, ...]
    integration_problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (self.missing or self.unexpected or self.dangerous or self.option_problems or self.integration_problems)

    @property
    def code(self) -> str | None:
        for code, found in (("registration_dangerous", self.dangerous), ("registration_unexpected", self.unexpected),
                            ("registration_missing", self.missing), ("registration_grant_option", self.option_problems),
                            ("registration_integration", self.integration_problems)):
            if found:
                return code
        return None


def _flag(value: object) -> str:
    return str(value).strip().lower()


def verify_registration(
    role_rows: Iterable[Mapping[str, object]],
    integration_rows: Iterable[Mapping[str, object]],
    ceiling: ConnectScope,
    names: RegNames,
    *,
    account: str,
    redirect_uri: str,
    allow_local_dev_redirect: bool = False,
) -> RegistrationReport:
    """Compare `SHOW GRANTS TO ROLE <setup role>` and `DESCRIBE SECURITY INTEGRATION` with exactly what the script creates."""
    grants = _verify_role_grants(role_rows, ceiling, names, account)
    return _with_integration(grants, integration_rows, names, redirect_uri, allow_local_dev_redirect and is_local_dev_redirect(redirect_uri))


def verify_setup_role_grants(
    role_rows: Iterable[Mapping[str, object]], ceiling: ConnectScope, names: RegNames, *, account: str
) -> RegistrationReport:
    """The grants half of `verify_registration`, for a signed-in setup role that cannot describe the integration (it does not own it)."""
    return RegistrationReport(*_verify_role_grants(role_rows, ceiling, names, account), ())


def _verify_role_grants(
    role_rows: Iterable[Mapping[str, object]], ceiling: ConnectScope, names: RegNames, account: str
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    acct = account.upper()
    expected: dict[tuple[str, str, tuple[str, ...]], bool] = {
        ("CREATE ROLE", "ACCOUNT", (acct,)): False,
        ("CREATE USER", "ACCOUNT", (acct,)): False,
        ("USAGE", "DATABASE", (ceiling.database,)): True,
        ("USAGE", "WAREHOUSE", (names.warehouse,)): True,
    }
    expected.update({("USAGE", "SCHEMA", (ceiling.database, s)): True for s in ceiling.schemas})
    expected.update({("SELECT", "TABLE", (ceiling.database, s, t)): True for s, t in ceiling.tables})
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    missing, unexpected, dangerous, option = [], [], [], []
    for privilege, granted_on, name, grant_option in map(_row, role_rows):
        key = (privilege, granted_on, name)
        wildcard = any(ch in part for part in name for ch in "<>*%")
        if (privilege not in {"USAGE", "SELECT"} | SAFE_ACCOUNT_PRIVILEGES) or granted_on == "ROLE" or wildcard:
            dangerous.append(_label(*key))
        elif key in expected:
            seen.add(key)
            if grant_option != expected[key]:
                option.append(_label(*key) + (" without grant option" if expected[key] else " with grant option"))
        else:
            unexpected.append(_label(*key))
    missing = [_label(*key) for key in sorted(expected.keys() - seen)]
    return tuple(missing), tuple(sorted(unexpected)), tuple(sorted(dangerous)), tuple(sorted(option))


def _with_integration(
    grants: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    integration_rows: Iterable[Mapping[str, object]],
    names: RegNames,
    redirect_uri: str,
    local_dev: bool = False,
) -> RegistrationReport:
    props = {str(r.get("property", "")).upper(): str(r.get("property_value", "")) for r in integration_rows}
    problems = []

    def need(prop: str, ok: bool) -> None:
        if not ok:
            problems.append(prop.lower())

    need("oauth_client_type", props.get("OAUTH_CLIENT_TYPE", "").upper() == "PUBLIC")
    need("oauth_enforce_pkce", _flag(props.get("OAUTH_ENFORCE_PKCE")) == "true")
    need("oauth_issue_refresh_tokens", _flag(props.get("OAUTH_ISSUE_REFRESH_TOKENS")) == "false")
    need("oauth_access_token_validity", props.get("OAUTH_ACCESS_TOKEN_VALIDITY", "").isdigit() and int(props["OAUTH_ACCESS_TOKEN_VALIDITY"]) <= ACCESS_TOKEN_SECONDS)
    need("oauth_redirect_uri", props.get("OAUTH_REDIRECT_URI") == redirect_uri)
    need("oauth_alternate_redirect_uris", not props.get("OAUTH_ALTERNATE_REDIRECT_URIS", "").strip("[] "))
    need("oauth_allow_non_tls_redirect_uri", _flag(props.get("OAUTH_ALLOW_NON_TLS_REDIRECT_URI", "false")) == ("true" if local_dev else "false"))
    need("blocked_roles_list", REQUIRED_BLOCKED <= {r.strip().upper() for r in props.get("BLOCKED_ROLES_LIST", "").split(",")})
    need("pre_authorized_roles_list", not props.get("PRE_AUTHORIZED_ROLES_LIST", "").strip("[] "))
    need("allowed_roles_list", not props.get("ALLOWED_ROLES_LIST", "").strip("[] "))
    need("oauth_use_secondary_roles", props.get("OAUTH_USE_SECONDARY_ROLES", "NONE").upper() == "NONE")
    need("network_policy", props.get("NETWORK_POLICY", "").upper() == names.network_policy)
    need("enabled", _flag(props.get("ENABLED")) == "true")
    return RegistrationReport(*grants, tuple(problems))


# ---- per connection, inside the ceiling -----------------------------------------------------------------------------


def per_connection_statements(
    names: RegNames, ceiling: ConnectScope, scope: ConnectScope, *, connection_suffix: str, public_key_body: str
) -> tuple[tuple[str, ...], Names]:
    """What product-run runs as the setup role for one connection. Refuses anything outside the ceiling."""
    if not _SUFFIX.match(connection_suffix or ""):
        raise ScopeError("the connection suffix must be 8 lower-case hex characters")
    if not re.fullmatch(r"[A-Za-z0-9+/]{100,}={0,2}", public_key_body or ""):
        raise ScopeError("public key must be the base64 body of an RSA public key")
    if scope.database != ceiling.database or not set(scope.tables) <= set(ceiling.tables):
        raise OutsideCeiling("the requested tables are not all inside the ceiling the admin allowed")
    c = connection_suffix.upper()
    ro, svc, db = f"COMPASS_RO_{c}", f"COMPASS_SVC_{c}", _quote(scope.database)
    statements = (
        f"CREATE ROLE {ro} {COMMENT}",
        f"CREATE USER {svc} TYPE = SERVICE DEFAULT_ROLE = {ro} DEFAULT_WAREHOUSE = {names.warehouse} {COMMENT}",
        f"ALTER USER {svc} SET RSA_PUBLIC_KEY = '{public_key_body}'",
        f"GRANT USAGE ON DATABASE {db} TO ROLE {ro}",
        *(f"GRANT USAGE ON SCHEMA {db}.{_quote(s)} TO ROLE {ro}" for s in scope.schemas),
        *(f"GRANT SELECT ON TABLE {db}.{_quote(s)}.{_quote(t)} TO ROLE {ro}" for s, t in sorted(scope.tables)),
        f"GRANT USAGE ON WAREHOUSE {names.warehouse} TO ROLE {ro}",
        f"GRANT ROLE {ro} TO USER {svc}",
    )
    return statements, Names(role=ro, user=svc, warehouse=names.warehouse, monitor=names.monitor, network_policy=names.network_policy)


def verify_connection_grants(role_rows: Iterable[Mapping[str, object]], scope: ConnectScope, conn_names: Names) -> GrantsReport:
    """The same read-only verification as the script path, for the role product-run created."""
    return verify_grants(role_rows, scope, conn_names)


def render_markdown() -> str:
    """The reviewable example. A test keeps `docs/design/CONNECT_REGISTRATION_SCRIPT.md` equal to this."""
    ceiling = ConnectScope("ANALYTICS", (("PUBLIC", "ORDERS"), ("PUBLIC", "CUSTOMERS"), ("MART", "DAILY")))
    script = generate_registration(ceiling, suffix="a1b2c3d4", redirect_uri="https://connect.example.com/oauth/callback",
                                   egress_networks=["35.238.115.103/32"], setup_users=["ADMIN_USER"])

    def block(title: str, lines) -> str:
        return f"### {title}\n\n```sql\n" + "\n".join(f"{line};" for line in lines) + "\n```\n"

    return "\n".join([
        "# Product-run registration script: an example (generated)\n",
        "Generated by `services/analytics-api/app/connect/registration.py`; a test keeps this file equal to the generator. The names, "
        "tables, redirect URL, Compass network address and user are examples. The forms of the role, grants, warehouse, monitor and "
        "integration statements ran live in the product-run spike; creating the network policy, setting `NETWORK_POLICY` on the "
        "integration and the `https` redirect did **not** (docs/design/CONNECT_PRODUCT_RUN_SPIKE_RESULTS.md).\n",
        "### What the admin is told\n\n" + "\n".join(f"- {line}" for line in script.summary) + "\n",
        block("Registration (run once, as ACCOUNTADMIN)", script.statements),
        block("Removal", script.teardown),
        f"Digest `{script.digest}`.\n",
    ])
