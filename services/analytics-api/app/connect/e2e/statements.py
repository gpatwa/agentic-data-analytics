"""The exact statements of the product-run live end-to-end check, generated, not hand-typed.

The registration script is the product's own (`generate_registration`), with the one declared sandbox deviation: a local
`http://localhost` sign-in redirect and the non-TLS flag that goes with it. Nothing here grants `MANAGE GRANTS`, reads a client
secret, or can lock the owner out: `assert_no_lockout` is the stop rule that refuses any statement that would change the
account's or a user's network policy. Not verified against a live account: `CREATE NETWORK POLICY`, `NETWORK_POLICY` on the
integration, `ALTER NETWORK POLICY`, and `CURRENT_IP_ADDRESS()` as the Compass address.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.connect.grants import ConnectScope
from app.connect.grants import _quote as quote
from app.connect.registration import RegistrationScript, RegNames, generate_registration

TAG = "compass-connect"
PREFIXES = ("COMPASS_REG_", "COMPASS_RO_", "COMPASS_SVC_", "COMPASS_E2E_")
SCHEMA = "DATA"
INSIDE = ("orders", "customers")
OUTSIDE = "payroll_outside"
CALLBACK_PATH = "/api/v2/connect/oauth/callback"
_SUFFIX = re.compile(r"^[0-9a-f]{8}$")


class LockoutRisk(ValueError):
    """A statement would change an account's or a user's network policy, which could lock someone out. Never run."""


def fixture_db(suffix: str) -> str:
    if not _SUFFIX.match(suffix or ""):
        raise ValueError("the run suffix must be 8 lower-case hex characters")
    return f"COMPASS_E2E_DB_{suffix.upper()}"


def ceiling(suffix: str) -> ConnectScope:
    return ConnectScope(fixture_db(suffix), tuple((SCHEMA, t) for t in INSIDE))


def redirect(port: int) -> str:
    return f"http://localhost:{port}{CALLBACK_PATH}"


def _table(suffix: str, name: str) -> str:
    return f"{fixture_db(suffix)}.{SCHEMA}.{quote(name)}"


SWEEP = (
    "SHOW DATABASES LIKE 'COMPASS_E2E%'",
    "SHOW ROLES LIKE 'COMPASS_REG%'",
    "SHOW ROLES LIKE 'COMPASS_RO%'",
    "SHOW USERS LIKE 'COMPASS_SVC%'",
    "SHOW WAREHOUSES LIKE 'COMPASS_REG%'",
    "SHOW RESOURCE MONITORS LIKE 'COMPASS_REG%'",
    "SHOW INTEGRATIONS LIKE 'COMPASS_REG%'",
    "SHOW NETWORK POLICIES",
)


DECOY_COMMENT = "compass-connect decoy for the rollback check"


def decoy(connection_suffix: str) -> tuple[str, str, str]:
    """Rollback check R2, as ACCOUNTADMIN: (create, read back, drop). A plain user with the **planned** connection user's exact name, no key,
    password or network policy, so a real run collides with it. The drop is by exact name and is run only after the read-back shows the decoy
    comment, so a real object is never dropped by mistake."""
    if not _SUFFIX.match(connection_suffix or ""):
        raise ValueError("the connection suffix must be 8 lower-case hex characters")
    name = f"COMPASS_SVC_{connection_suffix.upper()}"
    return (f"CREATE USER {name} COMMENT = '{DECOY_COMMENT}'", f"SHOW USERS LIKE '{name}'", f"DROP USER IF EXISTS {name}")


def preflight() -> tuple[str, ...]:
    """Read only. The run stops if anything under the prefixes exists that this run did not create."""
    return ("SELECT CURRENT_ACCOUNT() AS account_locator, CURRENT_USER() AS user_name, CURRENT_ROLE() AS role_name, CURRENT_IP_ADDRESS() AS client_ip", *SWEEP)


def fixture(suffix: str) -> tuple[str, ...]:
    """The sandbox data: two tables inside the ceiling and one outside it, invented values only."""
    db = fixture_db(suffix)
    return (
        f"CREATE DATABASE {db} COMMENT = '{TAG}'",
        f"CREATE SCHEMA {db}.{SCHEMA}",
        f'CREATE TABLE {_table(suffix, "orders")} ("id" NUMBER, "amount" NUMBER(12, 2))',
        f'CREATE TABLE {_table(suffix, "customers")} ("id" NUMBER, "region" VARCHAR)',
        f'CREATE TABLE {_table(suffix, OUTSIDE)} ("id" NUMBER, "salary" NUMBER)',
        f"INSERT INTO {_table(suffix, 'orders')} SELECT SEQ4(), (SEQ4() + 1) * 7 FROM TABLE(GENERATOR(ROWCOUNT => 30))",
        f"INSERT INTO {_table(suffix, 'customers')} SELECT SEQ4(), IFF(MOD(SEQ4(), 2) = 0, 'EMEA', 'AMER') FROM TABLE(GENERATOR(ROWCOUNT => 30))",
        f"INSERT INTO {_table(suffix, OUTSIDE)} SELECT SEQ4(), 1000 + SEQ4() FROM TABLE(GENERATOR(ROWCOUNT => 30))",
    )


def registration(suffix: str, owner_user: str, port: int, egress: Iterable[str]) -> RegistrationScript:
    """The product's registration script for the fixture ceiling, with the sandbox-only local redirect."""
    return generate_registration(ceiling(suffix), suffix=suffix, redirect_uri=redirect(port), egress_networks=list(egress), setup_users=[owner_user],
                                 credit_quota=1, allow_local_dev_redirect=True)


def read_backs(suffix: str) -> tuple[str, ...]:
    """Read only: what the verifier needs after the registration script ran."""
    names = RegNames.for_registration(suffix)
    return (
        f"DESCRIBE SECURITY INTEGRATION {names.integration}",
        f"SHOW GRANTS TO ROLE {names.setup_role}",
        f"DESCRIBE NETWORK POLICY {names.network_policy}",
        "SELECT CURRENT_ACCOUNT() AS account_locator",
    )


def widen(suffix: str) -> tuple[str, str]:
    """(do, undo): give the setup role one table outside the ceiling. The product must then refuse with `registration_changed`."""
    role = RegNames.for_registration(suffix).setup_role
    table = _table(suffix, OUTSIDE)
    return f"GRANT SELECT ON TABLE {table} TO ROLE {role}", f"REVOKE SELECT ON TABLE {table} FROM ROLE {role}"


def wrong_address(suffix: str, egress: Iterable[str]) -> tuple[str, str]:
    """(do, undo): point the integration's policy at an address that is not ours, then restore it. Optional (E2 part 2)."""
    policy = RegNames.for_registration(suffix).network_policy
    listed = ", ".join(f"'{x}'" for x in egress)
    return f"ALTER NETWORK POLICY {policy} SET ALLOWED_IP_LIST = ('192.0.2.1/32')", f"ALTER NETWORK POLICY {policy} SET ALLOWED_IP_LIST = ({listed})"


def teardown(suffix: str, owner_user: str, egress: Iterable[str], port: int) -> tuple[str, ...]:
    """G3, as ACCOUNTADMIN: the registration's own teardown, then the fixture. The connection's removal statements come from the
    product at run time (its DROP USER and DROP ROLE) and are run first."""
    script = registration(suffix, owner_user, port, egress)
    return (*script.teardown, f"DROP DATABASE IF EXISTS {fixture_db(suffix)}", *SWEEP)


_FORBIDDEN = (
    re.compile(r"\bALTER\s+ACCOUNT\b", re.I),
    re.compile(r"\bALTER\s+USER\b", re.I),
    re.compile(r"\bSET\s+NETWORK_POLICY\b", re.I),
    re.compile(r"\bUNSET\s+NETWORK_POLICY\b", re.I),
    re.compile(r"\bMANAGE\s+GRANTS\b", re.I),
    re.compile(r"\bSHOW_OAUTH_CLIENT_SECRETS\b", re.I),
)


def assert_no_lockout(statements: Iterable[str]) -> None:
    """The stop rule. `NETWORK_POLICY = <name>` inside CREATE SECURITY INTEGRATION and `ALTER NETWORK POLICY COMPASS_REG_NET_...`
    are allowed; anything that changes the account's or a user's policy is not."""
    for index, statement in enumerate(statements):
        if any(pattern.search(statement) for pattern in _FORBIDDEN):
            raise LockoutRisk(f"statement {index} is not allowed in this check")
        if re.search(r"\bALTER\s+NETWORK\s+POLICY\b", statement, re.I) and not re.search(r"\bALTER\s+NETWORK\s+POLICY\s+COMPASS_REG_NET_[0-9A-F]{8}\s+SET\s+ALLOWED_IP_LIST\b", statement):
            raise LockoutRisk(f"statement {index} alters a network policy that is not ours")


def everything(suffix: str, owner_user: str, port: int, egress: Iterable[str]) -> tuple[str, ...]:
    egress = list(egress)
    script = registration(suffix, owner_user, port, egress)
    do, undo = widen(suffix)
    bad, good = wrong_address(suffix, egress)
    return (*preflight(), *fixture(suffix), *script.statements, *read_backs(suffix), do, undo, bad, good, *teardown(suffix, owner_user, egress, port))


def _block(title: str, lines: Iterable[str]) -> str:
    return f"### {title}\n\n```sql\n" + "\n".join(f"{line};" for line in lines) + "\n```\n"


def render_markdown(suffix: str = "a1b2c3d4", owner_user: str = "<YOUR_SNOWFLAKE_USER>", port: int = 8765, egress: tuple[str, ...] = ("35.238.115.103/32",)) -> str:
    """The reviewable text of every statement. A test keeps `docs/design/CONNECT_PRODUCT_RUN_E2E_STATEMENTS.md` equal to this."""

    block = _block
    script = registration(suffix, owner_user, port, egress)
    do, undo = widen(suffix)
    bad, good = wrong_address(suffix, egress)
    return "\n".join([
        "# Product-run end-to-end check: the exact statements (generated)\n",
        "Generated by `services/analytics-api/app/connect/e2e/statements.py`; a test keeps this file equal to the generator. "
        f"`{suffix}` stands for the fresh 8-hex run suffix chosen at run time, `{owner_user}` for your Snowflake user (read from `CURRENT_USER()` "
        f"in the preflight), `{port}` for the local callback port and `{egress[0]}` for Compass's own public address (read from "
        "`CURRENT_IP_ADDRESS()` in the preflight; both IPv4 and IPv6 are added if they differ). Nothing here has been run. "
        "See `docs/design/CONNECT_PRODUCT_RUN_E2E_PLAN.md`.\n",
        "**The registration script is the product's own** (`generate_registration`) with one declared sandbox deviation: the sign-in comes back "
        "to `http://localhost` and the integration sets `OAUTH_ALLOW_NON_TLS_REDIRECT_URI = TRUE`. The product's verifier is told about the "
        "deviation and must still pass. The network policy is attached **only to the OAuth integration**; no statement here changes the "
        "account's or any user's network policy (a stop rule refuses one). Not verified against a live account: `CREATE NETWORK POLICY`, "
        "`NETWORK_POLICY` on the integration, `ALTER NETWORK POLICY`, and `CURRENT_IP_ADDRESS()` as the Compass address.\n",
        block("Preflight (read only, G1)", preflight()),
        block("Fixture: the sandbox data (G1, as ACCOUNTADMIN)", fixture(suffix)),
        block("The product's registration script (G1, as ACCOUNTADMIN)", script.statements),
        "### What the admin is told for that script\n\n" + "\n".join(f"- {line}" for line in script.summary) + "\n",
        block("Read-backs for the verifier (G1, read only)", read_backs(suffix)),
        block("Negative check E5: widen the setup role, attempt a connection, undo (G2, as ACCOUNTADMIN)", (do, undo)),
        block("Optional check E2 part 2: a wrong address for the policy, attempt a sign-in, restore (G2, as ACCOUNTADMIN)", (bad, good)),
        "### The product's own removal statements for the connection\n\nThese are produced by the product at run time (`DROP USER` and "
        "`DROP ROLE` for the connection's read-only user and role), shown to you before they run, and run first at G3.\n",
        block("Teardown and sweep (G3, as ACCOUNTADMIN)", teardown(suffix, owner_user, egress, port)),
    ])


def render_rollback_markdown(suffix: str = "a1b2c3d4", owner_user: str = "<YOUR_SNOWFLAKE_USER>", port: int = 8765,
                             egress: tuple[str, ...] = ("35.238.115.103/32",), connection_suffix: str = "c0ffee11") -> str:
    """The reviewable text of the rollback check's statements. A test keeps `docs/design/CONNECT_PRODUCT_RUN_ROLLBACK_CHECK_STATEMENTS.md`
    equal to this. Registration, fixture and teardown are the end-to-end check's, with a new run suffix."""
    script = registration(suffix, owner_user, port, egress)
    create, read_back, drop = decoy(connection_suffix)
    return "\n".join([
        "# Product-run rollback check: the exact statements (generated)\n",
        "Generated by `services/analytics-api/app/connect/e2e/statements.py`; a test keeps this file equal to the generator. "
        f"`{suffix}` stands for the fresh 8-hex run suffix chosen at run time, `{owner_user}` for your Snowflake user (read from `CURRENT_USER()` "
        f"in the preflight), `{port}` for the local callback port, `{egress[0]}` for Compass's own public address (read from `CURRENT_IP_ADDRESS()`) "
        f"and `{connection_suffix}` for the planned connection's own 8-hex suffix, which is only known after a connection is planned, so the "
        "decoy statements are shown to you in full, with the real name, before they run. Nothing here has been run. "
        "See `docs/design/CONNECT_PRODUCT_RUN_ROLLBACK_CHECK_PLAN.md`.\n",
        "Registration, fixture, read-backs and teardown are **the same statements as the end-to-end check** "
        "(`CONNECT_PRODUCT_RUN_E2E_STATEMENTS.md`), with a new run suffix; they are repeated here so this file is complete. The stop rule that "
        "refuses any statement that would change an account's or a user's network policy applies to all of them.\n",
        _block("Preflight (read only, G1)", preflight()),
        _block("Fixture: the sandbox data (G1, as ACCOUNTADMIN)", fixture(suffix)),
        _block("The product's registration script (G1, as ACCOUNTADMIN)", script.statements),
        _block("Read-backs for the verifier (G1, read only)", read_backs(suffix)),
        "### R1: the injected failure\n\nNo account statement. The harness is started with `--inject-failure-on-step 8`: the first seven of the "
        "product's per-connection statements run for real, the eighth (`GRANT ROLE ... TO USER ...`) raises once as a driver error would, and the "
        "product's own rollback runs for real. I then read Snowflake (`SHOW ROLES` and `SHOW USERS` for the connection's names) to confirm "
        "nothing is left.\n",
        _block("R2: the decoy (G2, as ACCOUNTADMIN; the name is the planned connection user's)", (create,)),
        _block("R2: read the decoy back after the run, to confirm it is untouched (read only)", (read_back,)),
        _block("R2: drop the decoy (G3, only after the read-back shows the decoy comment)", (drop,)),
        "### The product's own removal statements for any connection\n\nProduced by the product at run time (`DROP USER` and `DROP ROLE`), "
        "shown to you before they run, and run first at G3.\n",
        _block("Teardown and sweep (G3, as ACCOUNTADMIN)", teardown(suffix, owner_user, egress, port)),
    ])
