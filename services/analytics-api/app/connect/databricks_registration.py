"""Databricks Connect mode, the admin-pre-creates path (decision 2026-11-18, docs/design/CONNECT_DATABRICKS_DESIGN.md).

The spike showed a non-admin setup principal cannot create principals, mint their secrets or grant the path and warehouse
access (CONNECT_DATABRICKS_SPIKE_RESULTS.md). So the admin runs one generated script that creates the per-connection service
principal, its OAuth secret, `USE CATALOG`, `USE SCHEMA`, `SELECT` on exactly the named tables, and `CAN_USE` on the named SQL
warehouse. Compass never holds an admin credential; it stores only the principal's application id and secret (shown once).

`verify_registration` compares what the workspace reports with exactly what the script creates. The statement forms are
**not verified live** until the end-to-end check (CONNECT_DATABRICKS_REGISTRATION_STATEMENTS.md) has run.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.connect.grants import ConnectScope, ScopeError

TAG = "compass-connect"
_SUFFIX = re.compile(r"^[0-9a-f]{8}$")
PRINCIPAL_PREFIX = "compass-conn-"
SECRET_LIFETIME_SECONDS = 90 * 24 * 3600  # 90 days: Databricks deletes a secret unused for 90 days anyway; rotate before it ends
SECRET_LIFETIME = f"{SECRET_LIFETIME_SECONDS}s"
DANGEROUS = {"MANAGE", "ALL PRIVILEGES", "MODIFY", "CREATE TABLE", "CREATE SCHEMA", "OWNERSHIP", "WRITE FILES", "EXECUTE"}


def bt(part: str) -> str:
    if not isinstance(part, str) or not part or len(part) > 255 or any(ord(c) < 32 or c in "*%`" for c in part):
        raise ScopeError("invalid identifier")
    return f"`{part}`"


@dataclass(frozen=True)
class Step:
    op: str  # create_principal | create_secret | warehouse_can_use | sql
    text: str  # what the admin reads
    arg: str = ""


@dataclass(frozen=True)
class Script:
    steps: tuple[Step, ...]
    teardown: tuple[Step, ...]
    summary: tuple[str, ...]
    digest: str
    principal: str


def principal_name(suffix: str) -> str:
    if not _SUFFIX.match(suffix or ""):
        raise ScopeError("the suffix must be 8 lower-case hex characters")
    return f"{PRINCIPAL_PREFIX}{suffix}"


def grant_statements(ceiling: ConnectScope, app_id: str) -> tuple[str, ...]:
    who, c = bt(app_id), bt(ceiling.database)
    return (
        f"GRANT USE CATALOG ON CATALOG {c} TO {who}",
        *(f"GRANT USE SCHEMA ON SCHEMA {c}.{bt(s)} TO {who}" for s in ceiling.schemas),
        *(f"GRANT SELECT ON TABLE {c}.{bt(s)}.{bt(t)} TO {who}" for s, t in sorted(ceiling.tables)),
    )


def revoke_statements(ceiling: ConnectScope, app_id: str) -> tuple[str, ...]:
    who, c = bt(app_id), bt(ceiling.database)
    return (
        *(f"REVOKE SELECT ON TABLE {c}.{bt(s)}.{bt(t)} FROM {who}" for s, t in sorted(ceiling.tables)),
        *(f"REVOKE USE SCHEMA ON SCHEMA {c}.{bt(s)} FROM {who}" for s in ceiling.schemas),
        f"REVOKE USE CATALOG ON CATALOG {c} FROM {who}",
    )


def generate(ceiling: ConnectScope, *, suffix: str, warehouse_name: str, application_id: str = "<APPLICATION_ID>") -> Script:
    name = principal_name(suffix)
    if not warehouse_name.strip():
        raise ScopeError("name the SQL warehouse")
    steps = (
        Step("create_principal", f"create the service principal '{name}'  (api: service_principals.create)", name),
        Step("create_secret", f"create an OAuth secret for it, lifetime {SECRET_LIFETIME} (shown once; give it to Compass)", SECRET_LIFETIME),
        Step("warehouse_can_use", f"give it CAN_USE on the SQL warehouse '{warehouse_name}' (nothing else about the warehouse changes)", warehouse_name),
        *(Step("sql", s, s) for s in grant_statements(ceiling, application_id)),
    )
    teardown = (
        *(Step("sql", s, s) for s in revoke_statements(ceiling, application_id)),
        Step("delete_principal", f"delete the service principal '{name}' (its secret and warehouse entry go with it)", name),
    )
    summary = (
        "Creates one service principal for Compass, with a secret you give to Compass once.",
        f"It can read only the {len(ceiling.tables)} table(s) you named in {len(ceiling.schemas)} schema(s) of catalog {ceiling.database}: "
        "USE CATALOG, USE SCHEMA and SELECT, nothing else, no write, no grant option.",
        f"It may use the SQL warehouse '{warehouse_name}'. Compass never edits the warehouse.",
        "Compass holds no admin credential. To remove everything later, run the removal script.",
    )
    digest = hashlib.sha256("\n".join(s.text for s in steps).encode()).hexdigest()[:16]
    return Script(steps, teardown, summary, digest, name)


# ---- verification ------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    dangerous: tuple[str, ...]
    warehouse_problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (self.missing or self.unexpected or self.dangerous or self.warehouse_problems)

    @property
    def code(self) -> str | None:
        for code, found in (("registration_dangerous", self.dangerous), ("registration_unexpected", self.unexpected),
                            ("registration_missing", self.missing), ("registration_warehouse", self.warehouse_problems)):
            if found:
                return code
        return None


def _norm(row: Mapping[str, object]) -> tuple[str, str, str]:
    low = {str(k).lower(): str(v) for k, v in row.items()}
    return low.get("actiontype", "").upper(), low.get("objecttype", "").upper().replace("_", " "), low.get("objectkey", "").replace("`", "").lower()


def verify_registration(grant_rows: Iterable[Mapping[str, object]], warehouse_acl: Iterable[tuple[str, str]], ceiling: ConnectScope,
                        app_id: str) -> Report:
    """`grant_rows`: `SHOW GRANTS <principal> ON ...` rows for the catalog, each schema and each table (and any other securable the
    caller inspects). `warehouse_acl`: (principal, permission) pairs from the warehouse's permission list."""
    c = ceiling.database.lower()
    expected = {("USE CATALOG", "CATALOG", c)}
    expected |= {("USE SCHEMA", "SCHEMA", f"{c}.{s.lower()}") for s in ceiling.schemas}
    expected |= {("SELECT", "TABLE", f"{c}.{s.lower()}.{t.lower()}") for s, t in ceiling.tables}
    seen, unexpected, dangerous = set(), [], []
    for action, kind, key in map(_norm, grant_rows):
        item = (action, kind, key)
        label = f"{action.lower()} on {kind.lower()} {key}"
        if action in DANGEROUS or "*" in key or "%" in key:
            dangerous.append(label)
        elif item in expected:
            seen.add(item)
        else:
            unexpected.append(label)
    missing = sorted(f"{a.lower()} on {k.lower()} {o}" for a, k, o in expected - seen)
    problems = []
    mine = [perm.upper() for who, perm in warehouse_acl if who == app_id]
    if mine != ["CAN_USE"]:
        problems.append("warehouse permission is not exactly CAN_USE" if mine else "no CAN_USE on the warehouse")
    return Report(tuple(missing), tuple(sorted(unexpected)), tuple(sorted(dangerous)), tuple(problems))
