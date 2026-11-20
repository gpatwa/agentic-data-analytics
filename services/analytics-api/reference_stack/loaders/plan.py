"""A load plan: every statement, every check, a guard that keeps each statement inside one schema, a dry run, and an executor over an injected session.

Nothing here opens a connection. `execute` takes a `session` (run, query) and an `uploader`, so the logic is tested on fakes; wiring a real
connector is part of G5 and needs the owner's approval. Stop rules: any failed statement, any count or value that differs, a schema that is
not empty, or a statement the guard refuses. Failures report the exception type only (driver messages can quote data).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import duckdb

from reference_stack.loaders.engines import Dialect, PlanError

ALLOWED_VERBS = ("CREATE SCHEMA", "CREATE VOLUME", "CREATE STAGE", "CREATE TABLE", "COPY INTO", "COMMENT ON", "PUT")
_QUALIFIED = re.compile(r"([`\"])([A-Za-z_]\w*)\1\.([`\"])([A-Za-z_]\w*)\3(?:\.([`\"])([A-Za-z_]\w*)\5)?")


class Session(Protocol):
    def run(self, sql: str) -> None: ...

    def query(self, sql: str) -> list[tuple[Any, ...]]: ...


@dataclass(frozen=True)
class Step:
    kind: str  # sql | upload
    text: str  # the statement, or a description of the upload
    local: str = ""  # upload: the local file
    remote: str = ""  # upload: the path inside the staging volume


@dataclass(frozen=True)
class Check:
    name: str
    sql: str
    expected: Any  # an int, or a dict of key -> int for a breakdown
    derived_from: tuple[str, str] = ()  # (a, b): the value is a minus b, computed from other checks


@dataclass(frozen=True)
class LoadPlan:
    dialect: Dialect
    catalog: str
    schema: str
    preflight: str
    steps: tuple[Step, ...]
    checks: tuple[Check, ...]
    cleanup: tuple[str, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def prefix(self) -> tuple[str, str]:
        return (self.catalog, self.schema)


def guard(plan: LoadPlan, sql: str) -> None:
    """Refuse any statement that is not an allowed verb, or that names an object outside `catalog.schema`."""
    head = sql.lstrip().upper()
    if not any(head.startswith(v) for v in ALLOWED_VERBS):
        raise PlanError("statement_not_allowed")
    for match in _QUALIFIED.finditer(sql):
        catalog, schema = match.group(2), match.group(4)
        if (catalog.casefold(), schema.casefold()) != (plan.catalog.casefold(), plan.schema.casefold()):
            raise PlanError("statement_outside_schema")
    if not _QUALIFIED.search(sql):
        raise PlanError("statement_has_no_qualified_name")


def render_dry_run(plan: LoadPlan) -> str:
    lines = [f"DRY RUN ({plan.dialect.name}): nothing was executed. Target {plan.catalog}.{plan.schema}", "", "-- preflight (must return 0 tables):", plan.preflight + ";", ""]
    for i, step in enumerate(plan.steps, 1):
        lines.append(f"-- step {i}" + (" (upload)" if step.kind == "upload" else ""))
        lines.append(step.text + ("" if step.kind == "upload" else ";"))
    lines += ["", "-- checks after loading:"]
    for c in plan.checks:
        lines.append(f"-- {c.name}: expect " + (f"{c.expected}" if not isinstance(c.expected, dict) else f"{len(c.expected)} entries") + (" (derived)" if c.derived_from else ""))
        if c.sql:
            lines.append(c.sql + ";")
    lines += ["", "-- cleanup, only on the owner's request, and only for the schema this run created:"] + [s + ";" for s in plan.cleanup]
    lines += [""] + [f"-- note: {n}" for n in plan.notes]
    return "\n".join(lines) + "\n"


@dataclass
class Report:
    done: list[str] = field(default_factory=list)
    stopped: str = ""
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.stopped and not self.failures


def _num(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def execute(plan: LoadPlan, session: Session, uploader: Callable[[str, str], None] | None = None) -> Report:
    """Run the plan. Stops at the first problem and says why; never retries and never drops anything."""
    report = Report()
    try:
        rows = session.query(plan.preflight)
    except Exception as exc:  # noqa: BLE001
        report.stopped = f"preflight_failed:{type(exc).__name__}"
        return report
    if not rows or _num(rows[0][0]) != 0:
        report.stopped = "schema_not_empty"
        return report
    for i, step in enumerate(plan.steps, 1):
        try:
            if step.kind == "upload":
                if uploader is None:
                    raise PlanError("no_uploader")
                if not step.remote.startswith(f"/Volumes/{plan.catalog}/{plan.schema}/") or ".." in step.remote:
                    raise PlanError("upload_outside_schema")
                uploader(step.local, step.remote)
            else:
                guard(plan, step.text)
                session.run(step.text)
        except PlanError as exc:
            report.stopped = f"step_{i}_refused:{exc}"
            return report
        except Exception as exc:  # noqa: BLE001
            report.stopped = f"step_{i}_failed:{type(exc).__name__}"
            return report
        report.done.append(f"step_{i}")
    results: dict[str, Any] = {}
    for c in plan.checks:
        try:
            if c.derived_from:
                a, b = c.derived_from
                got: Any = results[a] - results[b]
            elif isinstance(c.expected, dict):
                got = {str(k): _num(n) for k, n in session.query(c.sql)}
            else:
                got = _num(session.query(c.sql)[0][0])
        except Exception as exc:  # noqa: BLE001
            report.failures.append(f"{c.name}:query_failed:{type(exc).__name__}")
            continue
        results[c.name] = got
        if got != c.expected:
            report.failures.append(f"{c.name}:mismatch")
        else:
            report.done.append(f"check:{c.name}")
    return report


class DuckSession:
    """A session over DuckDB, used only to test the verification SQL's logic offline."""

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con

    def run(self, sql: str) -> None:
        self.con.execute(sql)

    def query(self, sql: str) -> list[tuple[Any, ...]]:
        return self.con.execute(sql).fetchall()
