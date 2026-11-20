"""Start from what a customer's warehouse already says (E6 of `docs/design/SEMANTIC_TRUST_FOUNDATIONS_PLAN.md`).

A Databricks metric view or a Snowflake semantic view already holds someone's decisions about metrics, dimensions, synonyms and comments.
This module reads those definitions **as text the caller supplies** (a metric view's YAML; the rows of `DESCRIBE SEMANTIC VIEW`), keeps the
part that fits this product, and hands it to the same validator that checks a model's proposal. It opens no connection and calls no model.

What fits: one source table the connection can already see, a plain `SUM`, `AVG`, `MIN`, `MAX`, `COUNT(*)` or `COUNT(DISTINCT column)`, and
dimensions that are a bare column. Everything else is listed under "not imported" with a reason, never approximated: joins, ratios,
windows, filtered measures, parameters, wildcards, derived expressions. An imported item is only a *candidate*: it still passes the
validator (key evidence, sensitive columns, the text checks for control characters, URLs and markup, the dry run) and a person still
certifies it. Vendor comments and synonyms are untrusted text and get the same checks as a model's; like a model's, they are not screened for
instruction-like wording, which a reviewer must read for. A source table outside the connection's tables is never read.

Tags can only tighten: an upstream sensitivity tag marks a column restricted; nothing upstream can clear a restriction.

Format facts come from the vendors' public references (Databricks metric view YAML, specification versions 0.1 and 1.1; Snowflake
`CREATE` and `DESCRIBE SEMANTIC VIEW`), checked 2026-11-19. They are tested here on fixtures copied from those pages, not on a workspace.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.ontology_draft.facts import FactColumn, FactTable, SchemaFacts
from app.ontology_draft.proposal import DraftDimension, DraftMetric, ParsedProposal

_AGG = re.compile(r"^\s*(COUNT|SUM|AVG|MIN|MAX)\s*\(\s*(DISTINCT\s+)?(\*|1|`[^`]+`|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*\)\s*$", re.IGNORECASE)
_COLUMN = re.compile(r"^\s*(`[^`]+`|[A-Za-z_]\w*)(?:\s*\.\s*(`[^`]+`|[A-Za-z_]\w*))?\s*$")
_AGG_NAMES = {"sum": "sum", "avg": "average", "min": "min", "max": "max"}
SENSITIVITY_WORDS = ("pii", "sensitive", "confidential", "restricted", "secret", "personal", "privacy", "private")
MAX_NAME = 64


@dataclass(frozen=True)
class NotImported:
    kind: str  # metric | dimension | view
    item: str
    reason: str


@dataclass(frozen=True)
class ImportResult:
    proposal: ParsedProposal
    not_imported: tuple[NotImported, ...]
    sources: tuple[str, ...]  # what was read, with a hash of each definition
    found_metrics: int = 0
    found_dimensions: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or "item"
    return (base if base[0].isalpha() else f"m_{base}")[:MAX_NAME]


def _humanize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", " ", name).strip().title() or "Item"


def _unquote(part: str) -> str:
    return part[1:-1] if part.startswith("`") else part


def _words(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [v.strip(" '\"") for v in value.split(",")]
    return [str(v) for v in value if str(v).strip()][:10] if isinstance(value, list) else []


def _fact_table(facts: SchemaFacts, name: str) -> FactTable | None:
    return next((t for t in facts.tables if t.name.casefold() == name.casefold()), None)


def _fact_column(table: FactTable, name: str) -> FactColumn | None:
    return next((c for c in table.columns if c.name.casefold() == name.casefold()), None)


def _reason_for(expr: str) -> str:
    low = expr.casefold()
    if re.search(r"\bover\s*\(", low):
        return "window"
    if re.search(r"\bfilter\s*\(", low):
        return "filtered_measure"
    if "/" in expr:
        return "ratio"
    if re.search(r"\bcase\b|\bif\s*\(", low):
        return "conditional"
    return "expression"


def _measure(expr: str, table: FactTable, qualifiers: set[str]) -> tuple[str, str | None] | str:
    """(aggregation, column) for a plain aggregate over one column of `table`, or a reason code."""
    m = _AGG.match(expr or "")
    if not m:
        return _reason_for(expr or "")
    func, distinct, arg = m.group(1).casefold(), bool(m.group(2)), m.group(3)
    if arg in {"*", "1"}:
        return ("count", None) if func == "count" and not distinct else "expression"
    parts = [_unquote(p) for p in arg.split(".")] if "`" not in arg else [_unquote(arg)]
    if len(parts) == 2:
        if parts[0].casefold() not in qualifiers:
            return "join"
        parts = parts[1:]
    column = _fact_column(table, parts[0])
    if column is None:
        return "unknown_column"
    if func == "count":
        if distinct:
            return ("count_distinct", column.name)
        # COUNT of the table's own key is the row count (a key is never null); COUNT of any other column skips nulls and is not.
        return ("count", None) if column.name in table.primary_key else "count_of_column"
    return "distinct_aggregate" if distinct else (_AGG_NAMES[func], column.name)


def _dimension_type(column: FactColumn) -> str:
    return {"date": "temporal", "timestamp": "temporal", "decimal": "numeric"}.get(column.data_type, "categorical")


def _description(comment: Any, fallback: str) -> str:
    text = str(comment).strip() if comment else ""
    return (text or fallback)[:500]


def _dimension_expr(expr: str, table: FactTable, qualifiers: set[str]) -> FactColumn | str:
    m = _COLUMN.match(expr or "")
    if not m:
        return "expression_field"
    first, second = _unquote(m.group(1)), (_unquote(m.group(2)) if m.group(2) else None)
    if second is not None:
        if first.casefold() not in qualifiers:
            return "join"
        first = second
    return _fact_column(table, first) or "unknown_column"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# ---- Databricks metric view ------------------------------------------------------------------------------------------------------


def from_metric_view_yaml(text: str, facts: SchemaFacts, *, view_name: str) -> ImportResult:
    """A metric view's YAML (specification 0.1 or 1.1). `dimensions` and `fields` are the same list."""
    source = f"databricks metric view `{view_name}` #{_digest(text)}"
    skipped: list[NotImported] = []
    try:
        spec = yaml.safe_load(text)
    except yaml.YAMLError:
        spec = None
    if not isinstance(spec, dict):
        return ImportResult(ParsedProposal((), (), (), (), (), ()), (NotImported("view", view_name, "unreadable_yaml"),), (source,))
    raw_source = str(spec.get("source") or "")
    parts = raw_source.split(".")
    table = _fact_table(facts, parts[-1]) if len(parts) == 3 and re.fullmatch(r"[\w.]+", raw_source) else None
    measures = [m for m in (spec.get("measures") or []) if isinstance(m, dict)]
    fields = [f for f in (spec.get("fields") or spec.get("dimensions") or []) if isinstance(f, dict)]
    whole = None
    if table is None:
        whole = "source_is_a_query" if " " in raw_source.strip() else "source_not_in_the_connections_tables"
    elif spec.get("joins"):
        whole = None  # measures and fields that use a join are refused one by one; the rest of the view still imports
    metrics: list[DraftMetric] = []
    dims: list[DraftDimension] = []
    if table is None:
        skipped += [NotImported("metric", str(m.get("name") or "?"), whole or "") for m in measures]
        skipped += [NotImported("dimension", str(f.get("name") or "?"), whole or "") for f in fields]
        return ImportResult(ParsedProposal((), (), (), (), (), ()), tuple(skipped), (source,), len(measures), len(fields))
    quals = {"source", table.name.casefold()}
    params = {str(p.get("name")) for p in (spec.get("parameters") or []) if isinstance(p, dict)}
    key = list(table.primary_key) or [table.columns[0].name]
    for m in measures:
        name = str(m.get("name") or "")
        expr = str(m.get("expr") or "")
        if not name:
            skipped.append(NotImported("metric", "?", "no_name"))
        elif spec.get("filter"):
            skipped.append(NotImported("metric", name, "view_filter"))
        elif m.get("window"):
            skipped.append(NotImported("metric", name, "window"))
        elif any(re.search(rf"\b{re.escape(p)}\b", expr) for p in params):
            skipped.append(NotImported("metric", name, "parameter"))
        else:
            got = _measure(expr, table, quals)
            if isinstance(got, str):
                skipped.append(NotImported("metric", name, got))
            else:
                agg, col = got
                metrics.append(DraftMetric(id=_slug(name), display_name=(str(m.get("display_name") or _humanize(name)))[:120], synonyms=_words(m.get("synonyms")),
                                           table=table.name, aggregation=agg, measure_column=col, grain_key_columns=key,
                                           description=_description(m.get("comment"), f"Imported from the metric view {view_name}.")))
    for f in fields:
        name = str(f.get("name") or "")
        expr = str(f.get("expr") or "")
        if "*" in expr:
            skipped.append(NotImported("dimension", name or expr[:30], "wildcard"))
            continue
        got = _dimension_expr(expr, table, quals)
        if isinstance(got, str):
            skipped.append(NotImported("dimension", name or "?", got))
            continue
        dims.append(DraftDimension(id=_slug(name or got.name), display_name=(str(f.get("display_name") or _humanize(name or got.name)))[:120],
                                   synonyms=_words(f.get("synonyms")), table=table.name, column=got.name, dimension_type=_dimension_type(got),
                                   description=_description(f.get("comment"), f"Imported from the metric view {view_name}.")))
    proposal = ParsedProposal((), tuple(metrics), tuple(dims), (), (), ())
    return ImportResult(proposal, tuple(skipped), (source,), len(measures), len(fields))


# ---- Snowflake semantic view -----------------------------------------------------------------------------------------------------


def from_semantic_view_rows(rows: Iterable[Sequence[Any]], facts: SchemaFacts, *, view_name: str) -> ImportResult:
    """The rows of `DESCRIBE SEMANTIC VIEW`: (object_kind, object_name, parent_entity, property, property_value)."""
    rows = [tuple(r) for r in rows if len(r) >= 5]
    source = f"snowflake semantic view `{view_name}` #{_digest(json.dumps(rows, default=str))}"
    objects: dict[tuple[str, str, str], dict[str, Any]] = {}
    for kind, name, parent, prop, value in (r[:5] for r in rows):
        objects.setdefault((str(kind or ""), str(name or ""), str(parent or "")), {})[str(prop or "")] = value
    tables = {n.casefold(): p.get("BASE_TABLE_NAME") for (k, n, _), p in objects.items() if k == "TABLE"}
    skipped: list[NotImported] = []
    metrics: list[DraftMetric] = []
    dims: list[DraftDimension] = []
    found_m = found_d = 0
    for (kind, name, parent), props in sorted(objects.items()):
        if kind not in {"METRIC", "DERIVED_METRIC", "DIMENSION", "FACT"}:
            continue
        if kind == "FACT":
            skipped.append(NotImported("metric", name, "fact_not_a_metric"))
            continue
        found_m, found_d = found_m + (kind != "DIMENSION"), found_d + (kind == "DIMENSION")
        item_kind = "dimension" if kind == "DIMENSION" else "metric"
        if kind == "DERIVED_METRIC":
            skipped.append(NotImported("metric", name, "derived_metric"))
            continue
        if str(props.get("ACCESS_MODIFIER") or "").upper() == "PRIVATE":
            skipped.append(NotImported(item_kind, name, "private"))
            continue
        base = tables.get(parent.casefold())
        table = _fact_table(facts, str(base)) if base else None
        if table is None:
            skipped.append(NotImported(item_kind, name, "source_not_in_the_connections_tables"))
            continue
        quals = {parent.casefold(), table.name.casefold()}
        expr = str(props.get("EXPRESSION") or "")
        comment, words = props.get("COMMENT"), _words(props.get("SYNONYMS"))
        if kind == "METRIC":
            got = _measure(expr, table, quals)
            if isinstance(got, str):
                skipped.append(NotImported("metric", name, got))
                continue
            agg, col = got
            metrics.append(DraftMetric(id=_slug(name), display_name=_humanize(name)[:120], synonyms=words, table=table.name,
                                       aggregation=agg, measure_column=col, grain_key_columns=list(table.primary_key) or [table.columns[0].name],
                                       description=_description(comment, f"Imported from the semantic view {view_name}.")))
        else:
            got = _dimension_expr(expr, table, quals)
            if isinstance(got, str):
                skipped.append(NotImported("dimension", name, got))
                continue
            dims.append(DraftDimension(id=_slug(name), display_name=_humanize(name)[:120], synonyms=words, table=table.name,
                                       column=got.name, dimension_type=_dimension_type(got),
                                       description=_description(comment, f"Imported from the semantic view {view_name}.")))
    for (kind, name, _), _p in sorted(objects.items()):
        if kind == "RELATIONSHIP":
            skipped.append(NotImported("view", name, "relationship_not_imported"))
    return ImportResult(ParsedProposal((), tuple(metrics), tuple(dims), (), (), ()), tuple(skipped), (source,), found_m, found_d)


# ---- tags (E6d): they can only tighten -------------------------------------------------------------------------------------------


def restricted_columns(tags: Mapping[tuple[str, str], Iterable[str]]) -> set[tuple[str, str]]:
    """(table, column) pairs whose upstream tags or tag values name a sensitivity class. `tags` maps (table, column) to tag text."""
    return {key for key, values in tags.items() if any(w in str(v).casefold() for v in values for w in SENSITIVITY_WORDS)}


def apply_restrictions(facts: SchemaFacts, restricted: set[tuple[str, str]]) -> SchemaFacts:
    """Mark the named columns restricted. Never clears a mark, so it can only make the draft stricter."""
    wanted = {(t.casefold(), c.casefold()) for t, c in restricted}
    tables = []
    for table in facts.tables:
        columns = tuple(c.model_copy(update={"restricted": True}) if (table.name.casefold(), c.name.casefold()) in wanted else c for c in table.columns)
        tables.append(table.model_copy(update={"columns": columns}))
    return SchemaFacts(tables=tuple(tables))


# ---- what we found (E6e) ---------------------------------------------------------------------------------------------------------


def render_found(facts: SchemaFacts, results: Sequence[ImportResult], *, kept_metrics: int, kept_dimensions: int) -> str:
    """The first screen of onboarding: what existed, what became a candidate, what could not, in counts and reasons."""
    commented = sum(1 for t in facts.tables for c in t.columns if c.comment)
    declared = sum(1 for t in facts.tables if t.key_evidence == "declared")
    restricted = sum(1 for t in facts.tables for c in t.columns if c.restricted)
    skipped = Counter(n.reason for r in results for n in r.not_imported)
    lines = ["## What we found in your warehouse", "",
             f"- Existing definitions read: {len(results)} (" + "; ".join(s for r in results for s in r.sources) + ")" if results else "- Existing definitions read: none",
             f"- Metrics found: {sum(r.found_metrics for r in results)}, dimensions found: {sum(r.found_dimensions for r in results)}",
             f"- Became candidates after the same checks as any draft: {kept_metrics} metric(s), {kept_dimensions} dimension(s)",
             f"- Columns with comments: {commented}; tables with a declared key: {declared}; columns restricted by an upstream tag: {restricted}", ""]
    lines += ["Not imported, by reason:"] + ([f"- {reason}: {count}" for reason, count in sorted(skipped.items())] or ["- nothing was left out"])
    lines += ["", "Everything above is a candidate. A person reviews and certifies it; nothing here is certified."]
    return "\n".join(lines) + "\n"


__all__ = ["ImportResult", "NotImported", "apply_restrictions", "from_metric_view_yaml", "from_semantic_view_rows", "render_found", "restricted_columns"]
