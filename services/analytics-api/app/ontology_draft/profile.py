"""Profiling and sample redaction for the auto-ontology drafter (milestone 2).

Implements the redaction rule the owner approved on 2026-11-14 (docs/AUTO_ONTOLOGY_DESIGN.md), as pure functions plus a
read-only profiler. The rule, in short:

- A column contributes samples only if its name and comment are not sensitive, it is a string or boolean column, it has at most
  50 distinct values, and none of those values is longer than 40 characters (otherwise it is free text).
- At most 5 values per column, each present in at least `min_cohort` rows, truncated to 40 characters. A value that looks like
  an email, UUID, card, phone or long digit run is replaced by a type placeholder.
- Numeric and date columns contribute ranges, not values.
- Samples are never persisted or logged. They live only in `ColumnProfile.samples`, which is excluded from serialisation and
  from `repr`, and they reach the model only through the prompt. This module never logs.

Reading of the rule where it is silent: a sensitive column contributes no range either (a minimum and maximum of an account
number or a date of birth would be the same leak), and a column that is not string, boolean, numeric or date contributes
counts only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.ontology_draft.facts import FactColumn, FactTable, SchemaFacts, is_sensitive_name
from app.ontology_draft.proposal import DraftError, Drop, ParsedProposal

MAX_DISTINCT_FOR_SAMPLES = 50
MAX_SAMPLES_PER_COLUMN = 5
MAX_SAMPLE_CHARS = 40
DEFAULT_MIN_COHORT = 10
DEFAULT_ROW_LIMIT = 100_000
DEFAULT_TIMEOUT_MS = 5_000
MIN_GUARDED_SAMPLE_CHARS = 3  # shorter values ("US", "A") are too common to police in free text

SampleStatus = Literal[
    "included",  # samples were kept
    "sensitive_name",
    "sensitive_comment",
    "not_sampled_type",  # numeric, date or another type: ranges or counts only
    "high_cardinality",
    "free_text",
    "small_cohort",  # no value was frequent enough
    "none",  # nothing to sample (empty column)
]

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_CARD = re.compile(r"\d(?:[ -]?\d){12,18}")
_LONG_DIGITS = re.compile(r"\d{9,}")
_PHONE = re.compile(r"\+?\d(?:[\s().-]?\d){6,}")
_PLACEHOLDERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", _EMAIL),
    ("uuid", _UUID),
    ("card", _CARD),
    ("long_number", _LONG_DIGITS),
    ("phone", _PHONE),
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnProfile(_Frozen):
    """Aggregate facts about one column. `samples` is the only field that holds data values, and it is never serialised."""

    name: str
    rows: int = Field(ge=0)
    nulls: int = Field(ge=0)
    distinct: int | None = Field(default=None, ge=0)
    min_value: str | None = None
    max_value: str | None = None
    sample_status: SampleStatus = "none"
    samples: tuple[str, ...] = Field(default=(), exclude=True, repr=False)

    def prompt_view(self) -> dict[str, Any]:
        """What the model may see: coarse counts, a range for numeric and date columns, and redacted samples."""
        view: dict[str, Any] = {"has_nulls": self.nulls > 0}
        if self.distinct is not None:
            view["distinct"] = self.distinct if self.distinct <= MAX_DISTINCT_FOR_SAMPLES else f">{MAX_DISTINCT_FOR_SAMPLES}"
        if self.min_value is not None and self.max_value is not None:
            view["range"] = {"min": self.min_value, "max": self.max_value}
        if self.samples:
            view["sample_values"] = list(self.samples)
        return view


class TableProfile(_Frozen):
    name: str
    columns: tuple[ColumnProfile, ...]

    def column(self, name: str) -> ColumnProfile | None:
        return next((column for column in self.columns if column.name == name), None)


class SchemaProfile(_Frozen):
    tables: tuple[TableProfile, ...] = ()

    def table(self, name: str) -> TableProfile | None:
        return next((table for table in self.tables if table.name == name), None)

    def aggregate_fingerprint(self) -> str:
        """SHA-256 over the profile with samples left out (they are excluded from serialisation)."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def sample_guard(self) -> re.Pattern[str] | None:
        """A pattern matching any kept sample as a whole word, or None when there is nothing to guard."""
        values = {
            sample.casefold()
            for table in self.tables
            for column in table.columns
            for sample in column.samples
            if len(sample) >= MIN_GUARDED_SAMPLE_CHARS and sample not in {"true", "false"} and not _is_placeholder(sample)
        }
        if not values:
            return None
        alternatives = "|".join(re.escape(value) for value in sorted(values, key=lambda v: (-len(v), v)))
        return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])", re.IGNORECASE)


# -------------------------------------------------------------------------------------------------------------- redaction


def _is_placeholder(value: str) -> bool:
    return value.startswith("<") and value.endswith(">") and value[1:-1] in {name for name, _ in _PLACEHOLDERS}


def redact_value(value: str) -> str:
    """Replace a value that looks like an email, UUID, card, long number or phone with a type placeholder, else shorten it."""
    for name, pattern in _PLACEHOLDERS:
        if pattern.search(value):
            return f"<{name}>"
    return _CONTROL.sub(" ", value)[:MAX_SAMPLE_CHARS].strip()


def select_samples(
    counts: Sequence[tuple[str, int]], *, min_cohort: int = DEFAULT_MIN_COHORT
) -> tuple[tuple[str, ...], SampleStatus]:
    """Apply the cardinality, free-text, cohort and pattern rules to `(value, row_count)` pairs for one text column.

    The caller has already excluded sensitive columns and non-text types. `counts` must hold every distinct value (at most one
    more than the cardinality cap, so a high-cardinality column is recognised).
    """
    if not counts:
        return (), "none"
    if len(counts) > MAX_DISTINCT_FOR_SAMPLES:
        return (), "high_cardinality"
    if any(len(value) > MAX_SAMPLE_CHARS for value, _ in counts):
        return (), "free_text"
    frequent = sorted(
        ((value, count) for value, count in counts if count >= min_cohort), key=lambda pair: (-pair[1], pair[0])
    )
    chosen: list[str] = []
    for value, _ in frequent:
        shown = redact_value(value)
        if shown and shown not in chosen:
            chosen.append(shown)
        if len(chosen) == MAX_SAMPLES_PER_COLUMN:
            break
    return (tuple(chosen), "included") if chosen else ((), "small_cohort")


def sensitive_reason(column: FactColumn) -> SampleStatus | None:
    if is_sensitive_name(column.name):
        return "sensitive_name"
    if column.comment and is_sensitive_name(column.comment):
        return "sensitive_comment"
    return None


# ---------------------------------------------------------------------------------------------------- the text-leak screen


def screen_proposal(proposal: ParsedProposal, profile: SchemaProfile | None) -> ParsedProposal:
    """Drop any proposed text that repeats a kept sample value, so samples are never persisted in a draft.

    This checks descriptions, display names, glossary terms and definitions. Identifiers are model-chosen slugs and are not
    screened. A drop is recorded with a code and the item's label, never the value.
    """
    guard = profile.sample_guard() if profile else None
    if guard is None:
        return proposal

    def leaks(*texts: str) -> bool:
        return any(guard.search(text) for text in texts)

    drops = list(proposal.drops)

    def keep(items, kind, label, texts):
        kept = []
        for index, item in enumerate(items):
            if leaks(*texts(item)):
                drops.append(Drop(kind, label(item, index), "sample_value_in_text"))
            else:
                kept.append(item)
        return tuple(kept)

    return replace(
        proposal,
        datasets=keep(proposal.datasets, "dataset", lambda i, _: i.table[:120], lambda i: (i.display_name, i.description)),
        metrics=keep(proposal.metrics, "metric", lambda i, _: i.id, lambda i: (i.description,)),
        dimensions=keep(proposal.dimensions, "dimension", lambda i, _: i.id, lambda i: (i.description,)),
        glossary=keep(proposal.glossary, "term", lambda _, n: f"#{n}", lambda i: (i.term, i.definition)),
        drops=tuple(drops),
    )


# --------------------------------------------------------------------------------------------------------------- profiler


@dataclass(frozen=True)
class ProfileLimits:
    min_cohort: int = DEFAULT_MIN_COHORT
    row_limit: int = DEFAULT_ROW_LIMIT
    timeout_ms: int = DEFAULT_TIMEOUT_MS


def session_guards(dialect_name: str, timeout_ms: int) -> list[str]:
    """Statements issued first in the profiling transaction. PostgreSQL gets a read-only transaction, a timeout and UTC."""
    if dialect_name == "postgresql":
        # UTC so a timestamp range does not depend on the server's zone (it reaches the prompt and the fingerprint).
        return ["SET TRANSACTION READ ONLY", f"SET LOCAL statement_timeout = {int(timeout_ms)}", "SET LOCAL TIME ZONE 'UTC'"]
    return []


def profile_engine(
    engine: sa.engine.Engine,
    facts: SchemaFacts,
    *,
    schema: str | None = None,
    limits: ProfileLimits | None = None,
) -> SchemaProfile:
    """Profile every table in `facts` with SELECT statements built by SQLAlchemy Core (identifiers are quoted, never formatted).

    Run it with the read-only role. It reads at most `row_limit` rows per table and never writes. A database error becomes
    `DraftError("profile_failed")`, without the driver's message, which can quote data.
    """
    limits = limits or ProfileLimits()
    tables: list[TableProfile] = []
    try:
        with engine.connect() as connection:
            for statement in session_guards(engine.dialect.name, limits.timeout_ms):
                connection.execute(sa.text(statement))
            for table in facts.tables:
                tables.append(_profile_table(connection, table, schema, limits))
            connection.rollback()
    except SQLAlchemyError:
        raise DraftError("profile_failed") from None  # the driver's message can quote data, so it is not chained
    return SchemaProfile(tables=tuple(tables))


def _profile_table(connection: sa.engine.Connection, table: FactTable, schema: str | None, limits: ProfileLimits) -> TableProfile:
    source = sa.table(table.name, *[sa.column(column.name) for column in table.columns], schema=schema)
    window = sa.select(*source.c).select_from(source).limit(limits.row_limit).subquery()

    ranged = [c for c in table.columns if sensitive_reason(c) is None and c.data_type in {"integer", "decimal", "date", "timestamp"}]
    aggregates: list[Any] = [sa.func.count().label("rows")]
    for column in table.columns:
        ref = window.c[column.name]
        aggregates.append(sa.func.count(ref).label(f"nn_{len(aggregates)}"))
        aggregates.append(sa.func.count(sa.distinct(ref)).label(f"d_{len(aggregates)}"))
        if column in ranged:
            aggregates.append(sa.func.min(ref).label(f"lo_{len(aggregates)}"))
            aggregates.append(sa.func.max(ref).label(f"hi_{len(aggregates)}"))
    row = connection.execute(sa.select(*aggregates)).one()
    values = list(row)
    rows = int(values[0])
    cursor = 1
    columns: list[ColumnProfile] = []
    for column in table.columns:
        non_null, distinct = int(values[cursor]), int(values[cursor + 1])
        cursor += 2
        low = high = None
        if column in ranged:
            low, high = values[cursor], values[cursor + 1]
            cursor += 2
        profile = ColumnProfile(
            name=column.name,
            rows=rows,
            nulls=rows - non_null,
            distinct=distinct,
            min_value=None if low is None else str(low),
            max_value=None if high is None else str(high),
            sample_status=_status_without_values(column, distinct),
        )
        if profile.sample_status == "none" and non_null:
            profile = _with_samples(connection, window, column, profile, limits)
        columns.append(profile)
    return TableProfile(name=table.name, columns=tuple(columns))


def _status_without_values(column: FactColumn, distinct: int) -> SampleStatus:
    reason = sensitive_reason(column)
    if reason:
        return reason
    if column.data_type not in {"string", "boolean"}:
        return "not_sampled_type"
    if distinct > MAX_DISTINCT_FOR_SAMPLES:
        return "high_cardinality"
    return "none"  # eligible: the caller fetches counts


def _with_samples(
    connection: sa.engine.Connection, window: sa.sql.selectable.Subquery, column: FactColumn, profile: ColumnProfile, limits: ProfileLimits
) -> ColumnProfile:
    ref = window.c[column.name]
    tally = sa.func.count().label("n")
    statement = (
        sa.select(ref, tally).where(ref.is_not(None)).group_by(ref).order_by(tally.desc(), ref).limit(MAX_DISTINCT_FOR_SAMPLES + 1)
    )
    counts = [(_as_text(value, column), int(n)) for value, n in connection.execute(statement)]
    samples, status = select_samples(counts, min_cohort=limits.min_cohort)
    return profile.model_copy(update={"samples": samples, "sample_status": status})


def _as_text(value: Any, column: FactColumn) -> str:
    if column.data_type == "boolean":
        return "true" if value in (True, 1, "t", "true", "True") else "false"
    return str(value)


__all__ = [
    "ColumnProfile",
    "ProfileLimits",
    "SchemaProfile",
    "TableProfile",
    "profile_engine",
    "redact_value",
    "screen_proposal",
    "select_samples",
    "sensitive_reason",
    "session_guards",
]
