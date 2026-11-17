"""Connect-mode Snowflake setup script and grants verification (threats T3, T7, T15).

`generate_snowflake_setup` produces the statements the admin approves (or runs, on the fallback path): one read-only role
with `USAGE` on the named database, schemas and warehouse and `SELECT` on each named table, nothing else. It never emits a
wildcard, an `ALL TABLES` or `FUTURE` grant, a grant option, an ownership transfer or a write privilege. `verify_grants`
reads what the warehouse reports and compares it to the same scope, so the two cannot drift apart.

Every object name carries a per-connection suffix, so the teardown script can only drop what this setup created, never an
object that happened to have the same name. Not verified against a live account: the network-policy statements, the
`SHOW GRANTS` row shapes beyond those the sandbox runs already used, and the statements' behaviour on accounts with
unusual role setups.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

TAG = "compass-connect"
_SUFFIX = re.compile(r"^[0-9a-f]{8}$")
_KEY_BODY = re.compile(r"^[A-Za-z0-9+/]{100,}={0,2}$")


class ScopeError(ValueError):
    """The requested scope cannot be expressed as a safe read-only grant."""


def _quote(part: str) -> str:
    if not isinstance(part, str) or not part or len(part) > 255:
        raise ScopeError("invalid identifier")
    if any(ord(ch) < 32 or ch in "*%" for ch in part):
        raise ScopeError("identifier contains a forbidden character")
    return '"' + part.replace('"', '""') + '"'


@dataclass(frozen=True)
class ConnectScope:
    database: str
    tables: tuple[tuple[str, str], ...]  # (schema, table), exact names; no wildcards

    def __post_init__(self) -> None:
        if not self.tables:
            raise ScopeError("at least one table is required")
        _quote(self.database)
        for schema, table in self.tables:
            _quote(schema)
            _quote(table)
        if len(set(self.tables)) != len(self.tables):
            raise ScopeError("duplicate table")

    @property
    def schemas(self) -> tuple[str, ...]:
        return tuple(sorted({schema for schema, _ in self.tables}))

    @property
    def allowed_tables(self) -> tuple[str, ...]:
        """The exact lower-case names the query gateway's allowlist is built from."""
        return tuple(sorted(f"{self.database}.{schema}.{table}".lower() for schema, table in self.tables))


@dataclass(frozen=True)
class Names:
    role: str
    user: str
    warehouse: str
    monitor: str
    network_policy: str

    @classmethod
    def for_connection(cls, suffix: str) -> Names:
        if not _SUFFIX.match(suffix or ""):
            raise ScopeError("the connection suffix must be 8 lower-case hex characters")
        s = suffix.upper()
        return cls(f"COMPASS_ROLE_{s}", f"COMPASS_USER_{s}", f"COMPASS_WH_{s}", f"COMPASS_MON_{s}", f"COMPASS_NET_{s}")


@dataclass(frozen=True)
class SetupScript:
    statements: tuple[str, ...]
    teardown: tuple[str, ...]
    summary: tuple[str, ...]  # plain-language lines for the approval screen
    digest: str = field(default="")


def digest_statements(statements: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(statements).encode("utf-8")).hexdigest()


def _networks(ips: Iterable[str]) -> tuple[str, ...]:
    result = []
    for value in ips:
        try:
            net = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            raise ScopeError("invalid network address") from exc
        if net.prefixlen == 0 or not net.is_global:
            raise ScopeError("allowed networks must be specific and public")
        result.append(str(net))
    return tuple(result)


def generate_snowflake_setup(
    scope: ConnectScope,
    *,
    suffix: str,
    public_key_body: str,
    credit_quota: int = 1,
    allowed_networks: Iterable[str] = (),
) -> SetupScript:
    names = Names.for_connection(suffix)
    if not _KEY_BODY.match(public_key_body or ""):
        raise ScopeError("public key must be the base64 body of an RSA public key")
    if not isinstance(credit_quota, int) or isinstance(credit_quota, bool) or not 1 <= credit_quota <= 100:
        raise ScopeError("credit quota must be a whole number from 1 to 100")
    networks = _networks(allowed_networks)
    comment = f"COMMENT = '{TAG}'"
    db = _quote(scope.database)
    statements = [
        f"CREATE ROLE {names.role} {comment}",
        f"CREATE RESOURCE MONITOR {names.monitor} WITH CREDIT_QUOTA = {credit_quota} "
        "TRIGGERS ON 80 PERCENT DO NOTIFY ON 100 PERCENT DO SUSPEND",
        f"CREATE WAREHOUSE {names.warehouse} WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE "
        f"INITIALLY_SUSPENDED = TRUE RESOURCE_MONITOR = {names.monitor} {comment}",
        f"GRANT USAGE ON WAREHOUSE {names.warehouse} TO ROLE {names.role}",
        f"GRANT USAGE ON DATABASE {db} TO ROLE {names.role}",
    ]
    statements += [f"GRANT USAGE ON SCHEMA {db}.{_quote(s)} TO ROLE {names.role}" for s in scope.schemas]
    statements += [
        f"GRANT SELECT ON TABLE {db}.{_quote(s)}.{_quote(t)} TO ROLE {names.role}" for s, t in sorted(scope.tables)
    ]
    statements += [
        f"CREATE USER {names.user} TYPE = SERVICE DEFAULT_ROLE = {names.role} DEFAULT_WAREHOUSE = {names.warehouse} {comment}",
        f"ALTER USER {names.user} SET RSA_PUBLIC_KEY = '{public_key_body}'",
        f"GRANT ROLE {names.role} TO USER {names.user}",
    ]
    teardown = [f"DROP USER {names.user}", f"DROP ROLE {names.role}", f"DROP WAREHOUSE {names.warehouse}", f"DROP RESOURCE MONITOR {names.monitor}"]
    if networks:
        listed = ", ".join(f"'{n}'" for n in networks)
        statements += [
            f"CREATE NETWORK POLICY {names.network_policy} ALLOWED_IP_LIST = ({listed}) {comment}",
            f"ALTER USER {names.user} SET NETWORK_POLICY = {names.network_policy}",
        ]
        teardown.insert(1, f"DROP NETWORK POLICY {names.network_policy}")
    summary = (
        f"Create one read-only role ({names.role}) and one service user with a key you can revoke.",
        f"Let it read only {len(scope.tables)} named table(s) in {len(scope.schemas)} schema(s) of {scope.database}.",
        f"Give it its own small warehouse, capped at {credit_quota} credit(s), that suspends after 60 seconds.",
        "It cannot write, change or delete anything, and cannot read any other table."
        + (f" It can sign in only from {len(networks)} listed network address(es)." if networks else ""),
    )
    return SetupScript(tuple(statements), tuple(teardown), summary, digest_statements(statements))


# ---- verification --------------------------------------------------------------------------------------------------

_SAFE_PRIVILEGES = {"USAGE", "SELECT"}


def _parts(name: str) -> tuple[str, ...]:
    """Split a dotted Snowflake name; quoted parts keep case, unquoted fold to upper case."""
    parts, buffer, quoted, i = [], "", False, 0
    was_quoted = False
    while i < len(name):
        ch = name[i]
        if quoted:
            if ch == '"' and name[i + 1 : i + 2] == '"':
                buffer += '"'
                i += 1
            elif ch == '"':
                quoted = False
            else:
                buffer += ch
        elif ch == '"':
            quoted, was_quoted = True, True
        elif ch == ".":
            parts.append(buffer if was_quoted else buffer.upper())
            buffer, was_quoted = "", False
        else:
            buffer += ch
        i += 1
    parts.append(buffer if was_quoted else buffer.upper())
    return tuple(parts)


def _row(row: Mapping[str, object]) -> tuple[str, str, tuple[str, ...], bool]:
    lowered = {str(k).lower(): v for k, v in row.items()}
    return (
        str(lowered.get("privilege", "")).upper(),
        str(lowered.get("granted_on", "")).upper(),
        _parts(str(lowered.get("name", ""))),
        str(lowered.get("grant_option", "false")).lower() == "true",
    )


@dataclass(frozen=True)
class GrantsReport:
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    dangerous: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (self.missing or self.unexpected or self.dangerous)

    @property
    def code(self) -> str | None:
        if self.dangerous:
            return "grants_dangerous"
        if self.unexpected:
            return "grants_unexpected"
        if self.missing:
            return "grants_missing"
        return None


def _label(privilege: str, granted_on: str, name: tuple[str, ...]) -> str:
    return f"{privilege} on {granted_on.lower()} {'.'.join(name)}"


def verify_grants(role_rows: Iterable[Mapping[str, object]], scope: ConnectScope, names: Names) -> GrantsReport:
    """Compare `SHOW GRANTS TO ROLE` output with exactly what the setup grants. Fails closed on anything extra."""
    expected = {("USAGE", "WAREHOUSE", (names.warehouse,)), ("USAGE", "DATABASE", (scope.database,))}
    expected |= {("USAGE", "SCHEMA", (scope.database, s)) for s in scope.schemas}
    expected |= {("SELECT", "TABLE", (scope.database, s, t)) for s, t in scope.tables}
    seen, dangerous, unexpected = set(), [], []
    for privilege, granted_on, name, grant_option in map(_row, role_rows):
        key = (privilege, granted_on, name)
        flagged = (
            privilege not in _SAFE_PRIVILEGES
            or grant_option
            or granted_on == "ROLE"
            or any(ch in part for part in name for ch in "<>*%")
        )
        if flagged:
            dangerous.append(_label(*key))
        elif key in expected:
            seen.add(key)
        else:
            unexpected.append(_label(*key))
    missing = [_label(*key) for key in sorted(expected - seen)]
    return GrantsReport(tuple(missing), tuple(sorted(unexpected)), tuple(sorted(dangerous)))


def verify_user_roles(user_rows: Iterable[Mapping[str, object]], names: Names) -> GrantsReport:
    """`SHOW GRANTS TO USER` must show our role and nothing else."""
    roles = [_parts(str({str(k).lower(): v for k, v in r.items()}.get("role", "")))[-1] for r in user_rows]
    unexpected = tuple(sorted(f"role {r}" for r in roles if r != names.role))
    missing = () if names.role in roles else (f"role {names.role}",)
    return GrantsReport(missing, unexpected, ())
