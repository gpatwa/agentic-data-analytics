"""Draft inputs for a connected Databricks workspace, read through the connection's own principal (docs/design/CONNECT_CERTIFICATION_PLAN.md).

The principal holds `SELECT` on exactly the tables the admin named, so everything here can only ever see the ceiling. It reads
columns and types (`DESCRIBE TABLE EXTENDED`), then bounded aggregate `SELECT`s under a row limit and statement timeout, and
applies the same approved redaction rule as the pilot's profiler (it reuses its helpers). Row values never leave this module except
as the rule's redacted samples, which the profile keeps out of serialisation. A database error becomes `DraftError` with a fixed
code and never the driver's message, which can quote data.

One schema per draft: a contract's tables share a qualified prefix (`catalog.schema.`), which is what turns a table name into the
physical name the gateway allowlist holds.

Not verified against a workspace. Tested on a fake session.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from app.ontology_draft.facts import FactColumn, FactTable, SchemaFacts, normalize_type
from app.ontology_draft.introspect import MAX_COMMENT_CHARS, Introspection
from app.ontology_draft.profile import (
    MAX_DISTINCT_FOR_SAMPLES,
    ColumnProfile,
    ProfileLimits,
    SchemaProfile,
    TableProfile,
    _as_text,
    _status_without_values,
    select_samples,
    sensitive_reason,
)
from app.ontology_draft.proposal import DraftError

_IDENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ \-]{0,254}$")
BATCH = 1000


def _bt(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise DraftError("invalid_identifier")
    return f"`{name}`"


def _rows(session: Any, sql: str) -> list[tuple[Any, ...]]:
    stream = session.execute(sql, {})
    out: list[tuple[Any, ...]] = []
    try:
        while True:
            batch = stream.fetchmany(BATCH)
            if not batch:
                return out
            out.extend(tuple(r) for r in batch)
    finally:
        stream.close()


def qualified_prefix(catalog: str, schema: str) -> str:
    return f"{catalog}.{schema}.".lower()


def _single_schema(tables: Sequence[tuple[str, str]]) -> str:
    schemas = {schema for schema, _ in tables}
    if len(schemas) != 1:
        raise DraftError("single_schema_only")
    return next(iter(schemas))


def introspect_session(session: Any, *, catalog: str, tables: Sequence[tuple[str, str]]) -> Introspection:
    """Facts for exactly `tables` (schema, table). Keys are not read: Unity Catalog constraints are rarely declared, so a draft from
    here has no key evidence and therefore no executable joins, which matches the single-dataset scope already decided."""
    schema = _single_schema(tables)
    skipped: list[str] = []
    fact_tables: list[FactTable] = []
    try:
        for _, table in sorted(tables):
            rows = _rows(session, f"DESCRIBE TABLE EXTENDED {_bt(catalog)}.{_bt(schema)}.{_bt(table)}")
            columns: list[FactColumn] = []
            comment: str | None = None
            in_columns = True
            for row in rows:
                name, kind = str(row[0] or ""), str(row[1] or "")
                comment_text = str(row[2]) if len(row) > 2 and row[2] else None
                if in_columns and (not name.strip() or name.startswith("#")):
                    in_columns = False
                    continue
                if in_columns:
                    semantic = normalize_type(kind)
                    if semantic is None:
                        skipped.append(f"{table}.{name}")
                    else:
                        columns.append(FactColumn(name=name, data_type=semantic, comment=comment_text[:MAX_COMMENT_CHARS] if comment_text else None))
                elif name == "Comment" and kind:
                    comment = kind[:MAX_COMMENT_CHARS]
            if columns:
                fact_tables.append(FactTable(name=table, asset_id=f"{catalog}.{schema}.{table}", comment=comment, columns=tuple(columns)))
            else:
                skipped.append(table)
    except DraftError:
        raise
    except Exception:  # noqa: BLE001 - never echo the driver's message
        raise DraftError("introspection_failed") from None
    if not fact_tables:
        raise DraftError("no_usable_tables")
    return Introspection(SchemaFacts(tables=tuple(fact_tables)), tuple(sorted(skipped)))


def profile_session(session: Any, facts: SchemaFacts, *, catalog: str, schema: str, limits: ProfileLimits | None = None) -> SchemaProfile:
    limits = limits or ProfileLimits()
    tables: list[TableProfile] = []
    try:
        session.prepare(timeout_seconds=limits.timeout_ms / 1000)
        for table in facts.tables:
            tables.append(_profile_table(session, table, catalog, schema, limits))
    except DraftError:
        raise
    except Exception:  # noqa: BLE001
        raise DraftError("profile_failed") from None
    return SchemaProfile(tables=tuple(tables))


def _profile_table(session: Any, table: FactTable, catalog: str, schema: str, limits: ProfileLimits) -> TableProfile:
    source = f"{_bt(catalog)}.{_bt(schema)}.{_bt(table.name)}"
    window = f"(SELECT * FROM {source} LIMIT {int(limits.row_limit)}) w"
    ranged = [c for c in table.columns if sensitive_reason(c) is None and c.data_type in {"integer", "decimal", "date", "timestamp"}]
    parts = ["count(*)"]
    for column in table.columns:
        ref = f"w.{_bt(column.name)}"
        parts += [f"count({ref})", f"count(DISTINCT {ref})"]
        if column in ranged:
            parts += [f"min({ref})", f"max({ref})"]
    rows = _rows(session, f"SELECT {', '.join(parts)} FROM {window}")
    values = list(rows[0])
    total, cursor = int(values[0]), 1
    out: list[ColumnProfile] = []
    for column in table.columns:
        non_null, distinct = int(values[cursor]), int(values[cursor + 1])
        cursor += 2
        low = high = None
        if column in ranged:
            low, high = values[cursor], values[cursor + 1]
            cursor += 2
        profile = ColumnProfile(name=column.name, rows=total, nulls=total - non_null, distinct=distinct,
                                min_value=None if low is None else str(low), max_value=None if high is None else str(high),
                                sample_status=_status_without_values(column, distinct))
        if profile.sample_status == "none" and non_null:
            ref = _bt(column.name)
            counts = _rows(session, f"SELECT {ref}, count(*) AS n FROM (SELECT {ref} FROM {source} LIMIT {int(limits.row_limit)}) w "
                                    f"WHERE {ref} IS NOT NULL GROUP BY {ref} ORDER BY n DESC, {ref} LIMIT {MAX_DISTINCT_FOR_SAMPLES + 1}")
            samples, status = select_samples([(_as_text(v, column), int(n)) for v, n in counts], min_cohort=limits.min_cohort)
            profile = profile.model_copy(update={"samples": samples, "sample_status": status})
        out.append(profile)
    return TableProfile(name=table.name, columns=tuple(out))


def databricks_explain_probe(session_factory: Callable[[], Any]) -> Callable[[str, dict], None]:
    """The drafter's dry-run probe: `EXPLAIN` (never run) of the compiled statement through the connection's own session. Anything
    the principal may not read, or that does not plan, raises and is reported by class name only."""

    def probe(sql: str, parameters: dict) -> None:
        session = session_factory()
        try:
            stream = session.execute("EXPLAIN " + sql, dict(parameters))
            stream.fetchmany(1)
            stream.close()
        finally:
            session.close()

    return probe


def infer_keys(facts: SchemaFacts, profile: SchemaProfile, *, row_limit: int = ProfileLimits().row_limit) -> SchemaFacts:
    """Key evidence for tables with no declared key, which is the usual case on Unity Catalog. A column counts only when the profile saw
    the **whole table** (fewer rows than the row limit), it has no nulls, and every value is distinct; ties go to a column named like
    an id. This is a property of the data today, not a constraint, so a reviewer must confirm the grain at certification. A table
    that may be larger than the window gets no inferred key."""
    tables: list[FactTable] = []
    for table in facts.tables:
        seen = profile.table(table.name)
        if table.primary_key or seen is None:
            tables.append(table)
            continue
        unique = [c.name for c in seen.columns if c.rows and c.rows < row_limit and c.nulls == 0 and c.distinct == c.rows]
        named = [n for n in unique if re.search(r"(^|_)(id|key)$|^(id|key)(_|$)", n.lower())]
        pick = (named or unique or [None])[0]
        tables.append(table.model_copy(update={"primary_key": (pick,)}) if pick else table)
    return SchemaFacts(tables=tuple(tables))


__all__ = ["databricks_explain_probe", "infer_keys", "introspect_session", "profile_session", "qualified_prefix"]
