"""The exact statements of the product-run verification spike, generated, not hand-typed.

Every object name is `COMPASS_SPIKE_...` plus a run suffix, and carries the tag comment. Phase D can only drop names this
module generated. Nothing here reads or requests a client secret, and nothing grants `MANAGE GRANTS`, ownership or an admin
role except the negative tests, which are expected to be refused. Not verified against a live account: the syntax.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TAG = "compass-spike"
PREFIX = "COMPASS_SPIKE_"
_SUFFIX = re.compile(r"^[0-9a-f]{8}$")


class SpikeError(ValueError):
    pass


def quote(name: str) -> str:
    if not name or len(name) > 255 or any(ord(c) < 32 for c in name):
        raise SpikeError("invalid identifier")
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class Names:
    suffix: str
    db: str
    schema: str
    monitor: str
    warehouse: str
    setup_role: str
    integration: str
    ro_role: str
    svc_user: str
    neg_integration: str
    neg_monitor: str
    neg_db: str

    @classmethod
    def for_run(cls, suffix: str) -> Names:
        if not _SUFFIX.match(suffix or ""):
            raise SpikeError("the run suffix must be 8 lower-case hex characters")
        s = suffix.upper()
        return cls(
            suffix, f"{PREFIX}DB_{s}", "DATA", f"{PREFIX}MON_{s}", f"{PREFIX}WH_{s}", f"{PREFIX}SETUP_{s}", f"{PREFIX}OAUTH_{s}",
            f"{PREFIX}RO_{s}", f"{PREFIX}SVC_{s}", f"{PREFIX}NEG_OAUTH_{s}", f"{PREFIX}NEG_MON_{s}", f"{PREFIX}NEG_DB_{s}",
        )

    def table(self, name: str) -> str:
        return f"{self.db}.{self.schema}.{quote(name)}"

    @property
    def owned(self) -> tuple[str, ...]:
        return (self.db, self.monitor, self.warehouse, self.setup_role, self.integration, self.ro_role, self.svc_user,
                self.neg_integration, self.neg_monitor, self.neg_db)


COMMENT = f"COMMENT = '{TAG}'"
INSIDE = ("orders", "customers")
OUTSIDE = "payroll_outside"
SHOWS = (
    ("databases", "SHOW DATABASES LIKE 'COMPASS_SPIKE%'"),
    ("roles", "SHOW ROLES LIKE 'COMPASS_SPIKE%'"),
    ("users", "SHOW USERS LIKE 'COMPASS_SPIKE%'"),
    ("warehouses", "SHOW WAREHOUSES LIKE 'COMPASS_SPIKE%'"),
    ("resource monitors", "SHOW RESOURCE MONITORS LIKE 'COMPASS_SPIKE%'"),
    ("integrations", "SHOW INTEGRATIONS LIKE 'COMPASS_SPIKE%'"),
)


def preflight() -> tuple[str, ...]:
    """Read-only. The run stops if anything under the prefix exists that this run did not create."""
    return ("SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE()", *(show for _, show in SHOWS))


def phase_a(names: Names, owner_user: str, port: int) -> tuple[str, ...]:
    """G1, as ACCOUNTADMIN in the worksheet: the sandbox, the delegated role, the ceiling and the OAuth integration."""
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise SpikeError("port must be between 1024 and 65535")
    n, role = names, names.setup_role
    return (
        f"CREATE DATABASE {n.db} {COMMENT}",
        f"CREATE SCHEMA {n.db}.{n.schema}",
        f'CREATE TABLE {n.table("orders")} ("id" NUMBER, "amount" NUMBER(12, 2))',
        f'CREATE TABLE {n.table("customers")} ("id" NUMBER, "region" VARCHAR)',
        f'CREATE TABLE {n.table(OUTSIDE)} ("id" NUMBER, "salary" NUMBER)',
        f"INSERT INTO {n.table('orders')} SELECT SEQ4(), (SEQ4() + 1) * 7 FROM TABLE(GENERATOR(ROWCOUNT => 30))",
        f"INSERT INTO {n.table('customers')} SELECT SEQ4(), IFF(MOD(SEQ4(), 2) = 0, 'EMEA', 'AMER') FROM TABLE(GENERATOR(ROWCOUNT => 30))",
        f"INSERT INTO {n.table(OUTSIDE)} SELECT SEQ4(), 1000 + SEQ4() FROM TABLE(GENERATOR(ROWCOUNT => 30))",
        f"CREATE RESOURCE MONITOR {n.monitor} WITH CREDIT_QUOTA = 1 TRIGGERS ON 80 PERCENT DO NOTIFY ON 100 PERCENT DO SUSPEND",
        f"CREATE WAREHOUSE {n.warehouse} WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE "
        f"INITIALLY_SUSPENDED = TRUE RESOURCE_MONITOR = {n.monitor} {COMMENT}",
        f"CREATE ROLE {role} {COMMENT}",
        f"GRANT CREATE ROLE ON ACCOUNT TO ROLE {role}",
        f"GRANT CREATE USER ON ACCOUNT TO ROLE {role}",
        # The ceiling: what product-run may ever pass on, and nothing else.
        f"GRANT USAGE ON DATABASE {n.db} TO ROLE {role} WITH GRANT OPTION",
        f"GRANT USAGE ON SCHEMA {n.db}.{n.schema} TO ROLE {role} WITH GRANT OPTION",
        *(f"GRANT SELECT ON TABLE {n.table(t)} TO ROLE {role} WITH GRANT OPTION" for t in INSIDE),
        f"GRANT USAGE ON WAREHOUSE {n.warehouse} TO ROLE {role} WITH GRANT OPTION",
        f"GRANT ROLE {role} TO USER {quote(owner_user)}",
        f"CREATE SECURITY INTEGRATION {n.integration} TYPE = OAUTH ENABLED = TRUE OAUTH_CLIENT = CUSTOM "
        f"OAUTH_CLIENT_TYPE = 'PUBLIC' OAUTH_REDIRECT_URI = 'http://localhost:{port}/callback' "
        f"OAUTH_ALLOW_NON_TLS_REDIRECT_URI = TRUE OAUTH_ENFORCE_PKCE = TRUE OAUTH_ISSUE_REFRESH_TOKENS = FALSE "
        f"OAUTH_ACCESS_TOKEN_VALIDITY = 600 {COMMENT}",
        f"DESCRIBE SECURITY INTEGRATION {n.integration}",
        "SHOW PARAMETERS LIKE 'OAUTH_ADD_PRIVILEGED_ROLES_TO_BLOCKED_LIST' IN ACCOUNT",
    )


def per_setup(names: Names, public_key_body: str) -> tuple[str, ...]:
    """G2, as the setup role over OAuth: what product-run would run for one connection. Expected to succeed."""
    if not re.fullmatch(r"[A-Za-z0-9+/]{100,}={0,2}", public_key_body or ""):
        raise SpikeError("public key must be the base64 body of an RSA public key")
    n = names
    return (
        f"CREATE ROLE {n.ro_role} {COMMENT}",
        f"CREATE USER {n.svc_user} TYPE = SERVICE DEFAULT_ROLE = {n.ro_role} DEFAULT_WAREHOUSE = {n.warehouse} {COMMENT}",
        f"ALTER USER {n.svc_user} SET RSA_PUBLIC_KEY = '{public_key_body}'",
        f"GRANT USAGE ON DATABASE {n.db} TO ROLE {n.ro_role}",
        f"GRANT USAGE ON SCHEMA {n.db}.{n.schema} TO ROLE {n.ro_role}",
        *(f"GRANT SELECT ON TABLE {n.table(t)} TO ROLE {n.ro_role}" for t in INSIDE),
        f"GRANT USAGE ON WAREHOUSE {n.warehouse} TO ROLE {n.ro_role}",
        f"GRANT ROLE {n.ro_role} TO USER {n.svc_user}",
    )


def negative_tests(names: Names) -> tuple[tuple[str, str], ...]:
    """G2, as the setup role. Each is expected to be REFUSED. A success is a stop and a finding."""
    n = names
    return (
        ("grant a table outside the ceiling", f"GRANT SELECT ON TABLE {n.table(OUTSIDE)} TO ROLE {n.ro_role}"),
        ("grant MANAGE GRANTS", f"GRANT MANAGE GRANTS ON ACCOUNT TO ROLE {n.ro_role}"),
        ("grant SYSADMIN", f"GRANT ROLE SYSADMIN TO ROLE {n.ro_role}"),
        ("grant ACCOUNTADMIN", f"GRANT ROLE ACCOUNTADMIN TO ROLE {n.ro_role}"),
        ("transfer ownership of a ceiling table", f"GRANT OWNERSHIP ON TABLE {n.table(INSIDE[0])} TO ROLE {n.ro_role}"),
        ("create a security integration",
         f"CREATE SECURITY INTEGRATION {n.neg_integration} TYPE = OAUTH ENABLED = TRUE OAUTH_CLIENT = CUSTOM "
         f"OAUTH_CLIENT_TYPE = 'PUBLIC' OAUTH_REDIRECT_URI = 'http://localhost:1/callback' "
         f"OAUTH_ALLOW_NON_TLS_REDIRECT_URI = TRUE {COMMENT}"),
        ("create a resource monitor", f"CREATE RESOURCE MONITOR {n.neg_monitor} WITH CREDIT_QUOTA = 1"),
        ("create a database", f"CREATE DATABASE {n.neg_db} {COMMENT}"),
    )


def service_user_checks(names: Names) -> tuple[tuple[bool, str], ...]:
    """As the new service user with its key. (expected_to_succeed, statement)."""
    n = names
    return (
        (True, f"SHOW GRANTS TO ROLE {n.ro_role}"),
        *((True, f"DESCRIBE TABLE {n.table(t)}") for t in INSIDE),
        *((True, f"SELECT COUNT(*) FROM {n.table(t)}") for t in INSIDE),
        (False, f"SELECT COUNT(*) FROM {n.table(OUTSIDE)}"),
        (False, f"INSERT INTO {n.table(INSIDE[0])} VALUES (999, 999)"),
    )


def setup_role_teardown(names: Names) -> tuple[str, ...]:
    """G2, as the setup role: drop what it created. Created-only."""
    return (f"DROP USER {names.svc_user}", f"DROP ROLE {names.ro_role}")


def phase_d(names: Names, owner_user: str) -> tuple[str, ...]:
    """G3, as ACCOUNTADMIN: remove everything of this run. `IF EXISTS` only on names unique to this run."""
    n = names
    return (
        f"REVOKE ROLE {n.setup_role} FROM USER {quote(owner_user)}",
        f"DROP INTEGRATION IF EXISTS {n.integration}",
        f"DROP INTEGRATION IF EXISTS {n.neg_integration}",
        f"DROP USER IF EXISTS {n.svc_user}",
        f"DROP ROLE IF EXISTS {n.ro_role}",
        f"DROP ROLE IF EXISTS {n.setup_role}",
        f"DROP WAREHOUSE IF EXISTS {n.warehouse}",
        f"DROP RESOURCE MONITOR IF EXISTS {n.monitor}",
        f"DROP RESOURCE MONITOR IF EXISTS {n.neg_monitor}",
        f"DROP DATABASE IF EXISTS {n.db}",
        f"DROP DATABASE IF EXISTS {n.neg_db}",
        *(show for _, show in SHOWS),
    )


def render_markdown(suffix: str = "a1b2c3d4", owner_user: str = "<YOUR_SNOWFLAKE_USER>", port: int = 8765) -> str:
    """The reviewable text of every statement. A test keeps `docs/design/CONNECT_PRODUCT_RUN_SPIKE_STATEMENTS.md` equal to this."""
    n = Names.for_run(suffix)
    key = "<RSA public key body, generated at run time>"

    def block(title: str, lines) -> str:
        return f"### {title}\n\n```sql\n" + "\n".join(f"{line};" for line in lines) + "\n```\n"

    parts = [
        "# Product-run spike: the exact statements (generated)\n",
        "Generated by `services/analytics-api/app/connect/spike/statements.py`; a test keeps this file equal to the generator. "
        f"`{suffix}` stands for the fresh 8-hex run suffix chosen at run time, `{owner_user}` for your Snowflake user "
        f"(read from `CURRENT_USER()` in the preflight) and `{port}` for the local callback port. Nothing here has been run. "
        "Syntax is unverified until G1. See `docs/design/CONNECT_PRODUCT_RUN_SPIKE_PLAN.md`.\n",
        block("Preflight (read only, G1)", preflight()),
        block("Phase A (G1, as ACCOUNTADMIN)", phase_a(n, owner_user, port)),
        block(
            "Per-setup statements (G2, as the setup role over OAuth; expected to succeed)",
            tuple(statement.replace("A" * 100, key) for statement in per_setup(n, "A" * 100)),
        ),
        "### Negative tests (G2, as the setup role; each expected to be REFUSED)\n\n```sql\n"
        + "\n".join(f"-- {label}\n{stmt};" for label, stmt in negative_tests(n)) + "\n```\n",
        "### Checks as the new service user (G2)\n\n```sql\n"
        + "\n".join(f"-- expected to {'succeed' if ok else 'FAIL'}\n{stmt};" for ok, stmt in service_user_checks(n)) + "\n```\n",
        block("Teardown by the setup role (G2)", setup_role_teardown(n)),
        block("Phase D (G3, as ACCOUNTADMIN)", phase_d(n, owner_user)),
    ]
    return "\n".join(parts)
