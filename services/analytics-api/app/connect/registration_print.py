"""Offline printer and verifier for a Databricks Connect registration over an exact table list (approval C, preparation only).

It reuses `databricks_registration.generate` (the one statement generator) and adds two things: a scope check that refuses anything
but plain, exact identifiers (no wildcard, no `ALL TABLES`, no quoting tricks), and `verify_statements`, which compares a statement
list with exactly what the generator emits for that list and refuses a wildcard, a grant option, any other privilege or any table
outside the list. Nothing here connects to a workspace, issues a grant or creates a principal: it prints text for the owner, who runs
it as admin with a per-run principal. The teardown revokes and deletes only what the setup created.

    python -m app.connect.registration_print --warehouse "<warehouse name>" --suffix 0a1b2c3d

Not verified live. A Connect registration over this schema also needs a certified contract (a human gate this module does not touch).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence

from app.connect import databricks_registration as reg
from app.connect.grants import ConnectScope, ScopeError

DEFAULT_CATALOG = "dev"
DEFAULT_SCHEMA = "compass_synthetic_food_delivery"
SYNTHETIC_TABLES = ("restaurants", "couriers", "customers", "orders", "order_items", "payments", "orders_flat")
OPTIONAL_TABLE = "orders_current"  # added with --include-orders-current, once the view exists
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,254}$")
_BROAD = re.compile(r"[*%]|ALL\s+TABLES|ALL\s+PRIVILEGES|FUTURE|GRANT\s+OPTION|WITH\s+GRANT", re.IGNORECASE)


def _plain(name: str, what: str) -> str:
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ScopeError(f"{what} must be a plain identifier (letters, digits, underscore); wildcards and spaces are refused")
    return name


def scope_for(catalog: str, schema: str, tables: Sequence[str]) -> ConnectScope:
    """The exact ceiling: one catalog, one schema, an explicit non-empty table list."""
    if not tables:
        raise ScopeError("name the tables; an empty list is refused")
    return ConnectScope(_plain(catalog, "catalog"), tuple((_plain(schema, "schema"), _plain(t, "table")) for t in tables))


def verify_statements(statements: Iterable[str], catalog: str, schema: str, tables: Sequence[str], *, application_id: str = "<APPLICATION_ID>",
                      revoke: bool = False) -> list[str]:
    """Problems, empty when exact. The statements must equal, as a set, what the generator emits for the list: nothing more, nothing less."""
    scope = scope_for(catalog, schema, tables)
    expected = set((reg.revoke_statements if revoke else reg.grant_statements)(scope, application_id))
    given = [s.strip() for s in statements]
    problems: list[str] = []
    for s in given:
        if _BROAD.search(s):
            problems.append(f"refused (wildcard, grant option or broad privilege): {s}")
        elif s not in expected:
            problems.append(f"outside the list: {s}")
    problems += [f"missing: {s}" for s in sorted(expected - set(given))]
    if len(given) != len(set(given)):
        problems.append("duplicate statement")
    return problems


def verify_script(script: reg.Script, catalog: str, schema: str, tables: Sequence[str], *, application_id: str = "<APPLICATION_ID>") -> list[str]:
    setup_ops = {"create_principal", "create_secret", "warehouse_can_use", "sql"}
    problems = [f"unexpected setup step {s.op}" for s in script.steps if s.op not in setup_ops]
    problems += [f"unexpected teardown step {s.op}" for s in script.teardown if s.op not in {"sql", "delete_principal"}]
    problems += verify_statements([s.arg for s in script.steps if s.op == "sql"], catalog, schema, tables, application_id=application_id)
    problems += verify_statements([s.arg for s in script.teardown if s.op == "sql"], catalog, schema, tables, application_id=application_id, revoke=True)
    return problems


def build(catalog: str, schema: str, tables: Sequence[str], *, suffix: str, warehouse: str, application_id: str = "<APPLICATION_ID>") -> reg.Script:
    """The existing generator's script for exactly `tables`, checked against the verifier before it can be printed."""
    script = reg.generate(scope_for(catalog, schema, tables), suffix=suffix, warehouse_name=warehouse, application_id=application_id)
    problems = verify_script(script, catalog, schema, tables, application_id=application_id)
    if problems:  # the generator and the verifier disagree: never print
        raise ScopeError("generated registration failed its own check: " + "; ".join(problems))
    return script


def render(script: reg.Script) -> str:
    out = [f"-- principal: {script.principal} (per run; created by this script, removed by the teardown below)", f"-- digest: {script.digest}"]
    out += [f"-- {line}" for line in script.summary]
    out += ["", "-- SETUP (run as admin)"] + [f"{i}. {s.text}" for i, s in enumerate(script.steps, 1)]
    out += ["", "-- TEARDOWN (reverses only what the setup created)"] + [f"{i}. {s.text}" for i, s in enumerate(script.teardown, 1)]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Print (never run) the Connect registration and teardown for an exact table list.")
    p.add_argument("--catalog", default=DEFAULT_CATALOG)
    p.add_argument("--schema", default=DEFAULT_SCHEMA)
    p.add_argument("--tables", nargs="+", default=list(SYNTHETIC_TABLES))
    p.add_argument("--include-orders-current", action="store_true", help=f"also grant SELECT on {OPTIONAL_TABLE} (only once it exists)")
    p.add_argument("--warehouse", required=True, help="the SQL warehouse name; CAN_USE only")
    p.add_argument("--suffix", required=True, help="8 lower-case hex characters, one per run")
    args = p.parse_args(argv)
    tables = list(args.tables) + ([OPTIONAL_TABLE] if args.include_orders_current and OPTIONAL_TABLE not in args.tables else [])
    try:
        print(render(build(args.catalog, args.schema, tables, suffix=args.suffix, warehouse=args.warehouse)), end="")
    except ScopeError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
