"""L4: an offline reader of a customer's existing Databricks metric views and Unity Catalog tags, for the E6 importers (`existing_assets.py`).

It takes an injected session (the same shape as `databricks_source._declared_key`: `session.execute(sql, {})` returns a stream with `fetchmany`
and `close`) and reads catalog METADATA only, for a LIST OF NAMED OBJECTS only: every statement names one object by its three-part name, validated
as identifiers. There is no `SHOW ALL`, no wildcard, no `LIKE`, no listing of a schema. Nothing here reads table data. A read that fails gives
nothing (`None`) and never the driver's message, which can quote data.

NOT VERIFIED AGAINST A WORKSPACE. The exact statements are all defined in this one place (`metric_view_statement`, `column_tags_statement`,
`table_tags_statement`), so a correction is one edit:

* Metric view YAML: `DESCRIBE TABLE EXTENDED <catalog>.<schema>.<view>`; the YAML is expected in the row whose first column is `View Text` (its
  second column holds the text). UNSURE: Databricks may expose the metric view's YAML under a different row name, or only through
  `DESCRIBE TABLE EXTENDED ... AS JSON` or `SHOW CREATE TABLE`. If the row is absent the read returns `None` (fails closed: nothing imported).
* Tags: `<catalog>.information_schema.column_tags` (columns `schema_name`, `table_name`, `column_name`, `tag_name`, `tag_value`) and
  `<catalog>.information_schema.table_tags` (no `column_name`). These are the documented Unity Catalog information-schema relations as far as
  recalled; their column names are UNSURE until run once against the owner's workspace.

Tags can only tighten (`existing_assets.restricted_columns`/`apply_restrictions`). A failed tag read is NOT treated as "no tags": the caller must
refuse, because a missing restriction would loosen the draft.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from app.ontology_draft.databricks_source import _bt, _literal, _rows
from app.ontology_draft.facts import SchemaFacts
from app.ontology_draft.proposal import DraftError

MAX_OBJECTS = 50
MAX_YAML_CHARS = 200_000
VIEW_TEXT_ROW = "View Text"  # UNSURE: see the module docstring


def parse_name(name: str) -> tuple[str, str, str]:
    """`catalog.schema.object` exactly. No wildcard, no empty part, no fourth part."""
    parts = (name or "").split(".")
    if len(parts) != 3 or any(not p for p in parts):
        raise DraftError("name_must_be_three_parts")
    for part in parts:
        _bt(part)  # raises DraftError("invalid_identifier") for anything outside the identifier rule, so `*`, `%` and quotes never get through
    return parts[0], parts[1], parts[2]


def named_objects(names: Iterable[str]) -> list[tuple[str, str, str]]:
    out = [parse_name(n) for n in names]
    if not out:
        raise DraftError("no_objects_named")
    if len(out) > MAX_OBJECTS:
        raise DraftError("too_many_objects")
    return out


# ---- the statements: the only place they are written -------------------------------------------------------------------------------


def metric_view_statement(name: tuple[str, str, str]) -> str:
    catalog, schema, view = name
    return f"DESCRIBE TABLE EXTENDED {_bt(catalog)}.{_bt(schema)}.{_bt(view)}"


def column_tags_statement(name: tuple[str, str, str]) -> str:
    catalog, schema, table = name
    return (f"SELECT table_name, column_name, tag_name, tag_value FROM {_bt(catalog)}.information_schema.column_tags "
            f"WHERE schema_name = {_literal(schema)} AND table_name = {_literal(table)}")


def table_tags_statement(name: tuple[str, str, str]) -> str:
    catalog, schema, table = name
    return (f"SELECT table_name, tag_name, tag_value FROM {_bt(catalog)}.information_schema.table_tags "
            f"WHERE schema_name = {_literal(schema)} AND table_name = {_literal(table)}")


def planned_statements(metric_views: Sequence[str], tag_tables: Sequence[str]) -> list[str]:
    """What a live read would run, in order, for exactly the named objects. Printing this is the dry run."""
    out = [metric_view_statement(n) for n in named_objects(metric_views)] if metric_views else []
    for n in (named_objects(tag_tables) if tag_tables else []):
        out += [column_tags_statement(n), table_tags_statement(n)]
    return out


# ---- the readers ---------------------------------------------------------------------------------------------------------------------


def read_metric_view_yaml(session: Any, name: str) -> str | None:
    """The metric view's YAML text, or None when the read fails, the row is absent or the text is implausibly large."""
    statement = metric_view_statement(parse_name(name))
    try:
        for row in _rows(session, statement):
            if str(row[0] or "").strip() == VIEW_TEXT_ROW and len(row) > 1 and row[1]:
                text = str(row[1])
                return text if len(text) <= MAX_YAML_CHARS else None
    except Exception:  # noqa: BLE001 - never echo the driver's message; nothing is the safe answer
        return None
    return None


def read_tag_rows(session: Any, name: str) -> list[tuple[str, str | None, str, str]] | None:
    """`(table, column or None, tag_name, tag_value)` for ONE named table: its column tags and its table-level tags (column None).
    None when either read fails: a partial answer could drop a restriction."""
    parsed = parse_name(name)
    try:
        cols = [(str(r[0]), str(r[1]), str(r[2]), "" if r[3] is None else str(r[3])) for r in _rows(session, column_tags_statement(parsed))]
        tabs = [(str(r[0]), None, str(r[1]), "" if r[2] is None else str(r[2])) for r in _rows(session, table_tags_statement(parsed))]
    except Exception:  # noqa: BLE001
        return None
    return cols + tabs


def tags_by_column(facts: SchemaFacts, rows: Iterable[tuple[str, str | None, str, str]]) -> dict[tuple[str, str], tuple[str, ...]]:
    """The mapping `existing_assets.restricted_columns` takes. A table-level tag applies to every column of that table the facts hold. Tags for
    tables or columns the facts do not hold are dropped; only tag text is kept, never a value from the data."""
    out: dict[tuple[str, str], list[str]] = {}
    columns = {t.name.casefold(): [c.name for c in t.columns] for t in facts.tables}
    for table, column, tag, value in rows:
        targets = [column] if column is not None else columns.get(table.casefold(), [])
        for c in targets:
            out.setdefault((table, c), []).extend([tag, value] if value else [tag])
    return {k: tuple(v) for k, v in out.items()}


__all__ = ["column_tags_statement", "metric_view_statement", "named_objects", "parse_name", "planned_statements", "read_metric_view_yaml",
           "read_tag_rows", "table_tags_statement", "tags_by_column"]
