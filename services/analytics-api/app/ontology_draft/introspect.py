"""Deterministic introspection into `SchemaFacts` (auto-ontology milestone 2).

Reads table, column, key and comment metadata through the SQLAlchemy inspector (the information schema). It reads no row values.
The existing `MetadataProvider` snapshot has columns but no primary or foreign keys, which the drafter needs as key evidence, so
this uses the inspector directly on the same read-only engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.ontology_draft.facts import FactColumn, FactForeignKey, FactTable, SchemaFacts, normalize_type
from app.ontology_draft.proposal import DraftError

MAX_TABLES = 200
MAX_COMMENT_CHARS = 2_000


@dataclass(frozen=True)
class Introspection:
    facts: SchemaFacts
    skipped: tuple[str, ...]  # "table.column" names whose type was not recognised, or whole tables with no usable column


def _comment(value: Any) -> str | None:
    text = value.get("text") if isinstance(value, dict) else value
    return str(text)[:MAX_COMMENT_CHARS] if text else None


def introspect_engine(
    engine: sa.engine.Engine, *, schema: str | None = None, tables: list[str] | None = None
) -> Introspection:
    """Build schema facts for `tables` (default: every table in `schema`). Raises `DraftError` when nothing usable is found."""
    try:
        inspector = sa.inspect(engine)
        names = sorted(tables if tables is not None else inspector.get_table_names(schema=schema))[:MAX_TABLES]
        raw = {name: _read_table(inspector, name, schema) for name in names}
    except SQLAlchemyError:
        raise DraftError("introspection_failed") from None

    skipped: list[str] = []
    kept: dict[str, tuple[list[FactColumn], dict[str, Any]]] = {}
    for name, (columns, details) in raw.items():
        usable: list[FactColumn] = []
        for column in columns:
            semantic = normalize_type(str(column["type"]))
            if semantic is None:
                skipped.append(f"{name}.{column['name']}")
                continue
            usable.append(
                FactColumn(
                    name=column["name"],
                    data_type=semantic,
                    nullable=bool(column.get("nullable", True)),
                    comment=_comment(column.get("comment")),
                )
            )
        if usable:
            kept[name] = (usable, details)
        else:
            skipped.append(name)

    fact_tables: list[FactTable] = []
    for name, (columns, details) in kept.items():
        known = {column.name for column in columns}
        primary_key = tuple(details["pk"]) if details["pk"] and set(details["pk"]) <= known else ()
        foreign_keys = tuple(
            FactForeignKey(columns=tuple(fk["constrained_columns"]), ref_table=fk["referred_table"], ref_columns=tuple(fk["referred_columns"]))
            for fk in details["fks"]
            if fk.get("referred_table") in kept
            and fk.get("referred_schema") in (None, schema)
            and fk["constrained_columns"]
            and set(fk["constrained_columns"]) <= known
            and len(fk["constrained_columns"]) == len(fk["referred_columns"])
        )
        fact_tables.append(
            FactTable(
                name=name,
                asset_id=f"{schema}.{name}" if schema else name,
                comment=details["comment"],
                columns=tuple(columns),
                primary_key=primary_key,
                foreign_keys=foreign_keys,
            )
        )
    if not fact_tables:
        raise DraftError("no_usable_tables")
    return Introspection(SchemaFacts(tables=tuple(fact_tables)), tuple(sorted(skipped)))


def _read_table(inspector: sa.engine.reflection.Inspector, name: str, schema: str | None) -> tuple[list[dict], dict[str, Any]]:
    try:
        comment = _comment(inspector.get_table_comment(name, schema=schema))
    except NotImplementedError:  # the dialect has no table comments (SQLite)
        comment = None
    details = {
        "pk": list(inspector.get_pk_constraint(name, schema=schema).get("constrained_columns") or []),
        "fks": inspector.get_foreign_keys(name, schema=schema),
        "comment": comment,
    }
    return list(inspector.get_columns(name, schema=schema)), details


__all__ = ["Introspection", "introspect_engine"]
