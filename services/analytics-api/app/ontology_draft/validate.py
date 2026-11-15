"""Deterministic validation and emission for a drafted proposal (auto-ontology milestone 1).

Every candidate is checked against the schema facts and, for metrics and dimensions, compiled through the real compiler and
checked by the real read-only SQL validator. A candidate that fails is dropped with a coded reason; the model is never asked to
repair it. What survives is written as a `draft` registry document with `candidate` metrics, and candidate ontology nodes.
Nothing is certified here, and nothing is executed.

Scope decision (owner, 2026-11-14): single-dataset only. Drafted joins are returned as non-executable candidates; the compiler
has no join or ratio support, so a join cannot be dry-run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from app.compiler.postgres import CompilationError, PostgreSQLCompiler
from app.execution.validation import QueryRejected, validate_read_only_sql
from app.ontology_draft.facts import FactColumn, FactTable, SchemaFacts, is_sensitive_name
from app.ontology_draft.profile import SchemaProfile, screen_proposal
from app.ontology_draft.proposal import (
    DraftDatasetText,
    DraftDimension,
    DraftJoin,
    DraftMetric,
    DraftTerm,
    Drop,
    ParsedProposal,
)
from packages.platform_contracts.analytics_intent import (
    AnalyticalIntent,
    IntentGrouping,
    IntentMetric,
    SemanticContractReference,
)
from packages.platform_contracts.ontology import (
    OntologyEdge,
    OntologyNode,
    OntologyProvenance,
    OntologySnapshot,
)
from packages.platform_contracts.semantic import (
    SemanticContract,
    SemanticDataset,
    SemanticDimension,
    SemanticField,
    SemanticGrain,
    SemanticMetric,
    SemanticOwner,
    SemanticRegistryDocument,
)

DRAFTER_VERSION = "0.1.0"
SOURCE_SYSTEM = "auto-ontology-drafter"
NUMERIC = {"integer", "decimal"}
ORDERED = {"integer", "decimal", "date", "timestamp"}
_URL = re.compile(r"(https?:|www\.|ftp:)", re.IGNORECASE)
_HTML = re.compile(r"<[a-zA-Z/!]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Called with (sql, parameters) for one compiled statement. It must run EXPLAIN only. Raise to reject. Milestone 1 injects a
# fake; a real probe against a read-only role arrives with the live milestones.
ExplainProbe = Callable[[str, dict], None]


@dataclass(frozen=True)
class DraftContext:
    tenant_id: str
    domain: str
    contract_id: str
    owner_id: str
    owner_name: str
    model_id: str
    prompt_version: str


@dataclass(frozen=True)
class CandidateJoin:
    """A drafted join with key evidence. It is never executable: the compiler does not support joins."""

    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]
    cardinality: str
    evidence: str = "foreign_key"
    executable: bool = False


@dataclass(frozen=True)
class DraftResult:
    document: SemanticRegistryDocument | None
    ontology: OntologySnapshot | None
    candidate_joins: tuple[CandidateJoin, ...]
    dropped: tuple[Drop, ...]
    schema_fingerprint: str
    provenance: dict = field(default_factory=dict)


def validate_and_emit(
    proposal: ParsedProposal,
    facts: SchemaFacts,
    ctx: DraftContext,
    *,
    explain: ExplainProbe | None = None,
    now: datetime | None = None,
    profile: SchemaProfile | None = None,
) -> DraftResult:
    now = now or datetime.now(timezone.utc)
    proposal = screen_proposal(proposal, profile)  # no kept sample value may be persisted in draft text
    drops: list[Drop] = list(proposal.drops)
    fingerprint = facts.fingerprint()
    used_ids: set[str] = {table.name for table in facts.tables}
    used_ids |= {f"{table.name}.{column.name}" for table in facts.tables for column in table.columns}

    metrics = _check_metrics(proposal.metrics, facts, used_ids, drops)
    dimensions = _check_dimensions(proposal.dimensions, facts, used_ids, drops)
    metrics, dimensions = _dry_run(metrics, dimensions, facts, ctx, explain, drops)
    joins = _check_joins(proposal.joins, facts, drops)
    terms = _check_terms(proposal.glossary, drops)

    tables_in_use = sorted({m.table for m in metrics} | {d.table for d in dimensions})
    document: SemanticRegistryDocument | None = None
    ontology: OntologySnapshot | None = None
    if tables_in_use:
        texts = {item.table: item for item in proposal.datasets}
        contract = _build_contract(metrics, dimensions, tables_in_use, texts, facts, ctx, fingerprint, drops)
        document = SemanticRegistryDocument(lifecycle="draft", contract=contract)
        ontology = _build_ontology(contract, terms, ctx, fingerprint, now)
    elif terms:
        ontology = _build_ontology(None, terms, ctx, fingerprint, now)

    provenance = {
        "schema_fingerprint": fingerprint,
        "model_id": ctx.model_id,
        "prompt_version": ctx.prompt_version,
        "drafter_version": DRAFTER_VERSION,
        "profile_fingerprint": profile.aggregate_fingerprint() if profile else None,  # aggregate stats only, never samples
    }
    return DraftResult(document, ontology, tuple(joins), tuple(drops), fingerprint, provenance)


# ----------------------------------------------------------------------------------------------------------------- checks


def bad_text(value: str) -> str | None:
    if _CONTROL.search(value):
        return "control_characters"
    if _URL.search(value):
        return "contains_url"
    if _HTML.search(value) or "](" in value:
        return "contains_markup"
    return None


def _column(table: FactTable, name: str | None) -> FactColumn | None:
    return table.column(name) if name else None


def _check_metrics(
    items: tuple[DraftMetric, ...], facts: SchemaFacts, used_ids: set[str], drops: list[Drop]
) -> list[DraftMetric]:
    accepted: list[DraftMetric] = []
    for metric in items:
        code = _metric_problem(metric, facts, used_ids)
        if code:
            drops.append(Drop("metric", metric.id, code))
            continue
        used_ids.add(metric.id)
        accepted.append(metric)
    return accepted


def _metric_problem(metric: DraftMetric, facts: SchemaFacts, used_ids: set[str]) -> str | None:
    if metric.id in used_ids:
        return "duplicate_id"
    table = facts.table(metric.table)
    if table is None:
        return "unknown_table"
    if bad_text(metric.description):
        return "bad_description"
    if len(set(metric.grain_key_columns)) != len(metric.grain_key_columns):
        return "duplicate_grain_column"
    if not table.primary_key:
        return "no_key_evidence"
    if set(metric.grain_key_columns) != set(table.primary_key):
        return "grain_not_primary_key"
    if metric.aggregation == "count":
        return "count_with_measure" if metric.measure_column else None
    column = _column(table, metric.measure_column)
    if column is None:
        return "measure_required" if not metric.measure_column else "unknown_column"
    if is_sensitive_name(column.name):
        return "sensitive_column"
    if metric.aggregation in {"sum", "average"} and column.data_type not in NUMERIC:
        return "measure_type_mismatch"
    if metric.aggregation in {"min", "max"} and column.data_type not in ORDERED:
        return "measure_type_mismatch"
    return None


def _check_dimensions(
    items: tuple[DraftDimension, ...], facts: SchemaFacts, used_ids: set[str], drops: list[Drop]
) -> list[DraftDimension]:
    accepted: list[DraftDimension] = []
    for dimension in items:
        code = _dimension_problem(dimension, facts, used_ids)
        if code:
            drops.append(Drop("dimension", dimension.id, code))
            continue
        used_ids.add(dimension.id)
        accepted.append(dimension)
    return accepted


def _dimension_problem(dimension: DraftDimension, facts: SchemaFacts, used_ids: set[str]) -> str | None:
    if dimension.id in used_ids:
        return "duplicate_id"
    table = facts.table(dimension.table)
    if table is None:
        return "unknown_table"
    column = table.column(dimension.column)
    if column is None:
        return "unknown_column"
    if bad_text(dimension.description):
        return "bad_description"
    if is_sensitive_name(column.name):
        return "sensitive_column"
    key_columns = set(table.primary_key) | {name for fk in table.foreign_keys for name in fk.columns}
    if column.name in key_columns:
        return "identifier_not_dimension"
    allowed = {
        "temporal": {"date", "timestamp"},
        "numeric": NUMERIC,
        "categorical": {"string", "boolean", "integer"},
    }[dimension.dimension_type]
    if column.data_type not in allowed:
        return "dimension_type_mismatch"
    return None


def _check_joins(items: tuple[DraftJoin, ...], facts: SchemaFacts, drops: list[Drop]) -> list[CandidateJoin]:
    accepted: list[CandidateJoin] = []
    for join in items:
        label = f"{join.from_table}->{join.to_table}"[:120]
        source, target = facts.table(join.from_table), facts.table(join.to_table)
        if source is None or target is None:
            drops.append(Drop("join", label, "unknown_table"))
            continue
        if any(source.column(c) is None for c in join.from_columns) or any(target.column(c) is None for c in join.to_columns):
            drops.append(Drop("join", label, "unknown_column"))
            continue
        supported = any(
            fk.ref_table == target.name and fk.columns == tuple(join.from_columns) and fk.ref_columns == tuple(join.to_columns)
            for fk in source.foreign_keys
        )
        if not supported:
            drops.append(Drop("join", label, "no_key_evidence"))
            continue
        # Cardinality comes from the keys, not from the model. A foreign key onto the target's primary key is many-to-one.
        if set(target.primary_key) != set(join.to_columns):
            drops.append(Drop("join", label, "no_unique_target"))
            continue
        if join.cardinality != "many_to_one":
            drops.append(Drop("join", label, "cardinality_mismatch"))
            continue
        accepted.append(
            CandidateJoin(source.name, tuple(join.from_columns), target.name, tuple(join.to_columns), "many_to_one")
        )
    return accepted


def _check_terms(items: tuple[DraftTerm, ...], drops: list[Drop]) -> list[DraftTerm]:
    accepted: list[DraftTerm] = []
    seen: set[str] = set()
    for term in items:
        slug = _slug(term.term)
        if not slug:
            drops.append(Drop("term", "#", "bad_term"))
        elif slug in seen:
            drops.append(Drop("term", slug, "duplicate_term"))
        elif bad_text(term.term) or bad_text(term.definition):
            drops.append(Drop("term", slug, "bad_description"))
        else:
            seen.add(slug)
            accepted.append(term)
    return accepted


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:64]


# -------------------------------------------------------------------------------------------------------------- dry run


def _dry_run(
    metrics: list[DraftMetric],
    dimensions: list[DraftDimension],
    facts: SchemaFacts,
    ctx: DraftContext,
    explain: ExplainProbe | None,
    drops: list[Drop],
) -> tuple[list[DraftMetric], list[DraftDimension]]:
    """Compile each candidate through the real compiler, check the SQL with the real validator, optionally EXPLAIN it."""
    if not metrics:
        for dimension in dimensions:
            drops.append(Drop("dimension", dimension.id, "no_metric_for_dimension"))
        return [], []

    working = _probe_contract(metrics, dimensions, facts, ctx)
    kept_metrics: list[DraftMetric] = []
    for metric in metrics:
        code = _probe(working, ctx, metric.table, [metric.id], None, explain)
        if code:
            drops.append(Drop("metric", metric.id, code))
        else:
            kept_metrics.append(metric)
    first_metric_by_table: dict[str, str] = {}
    for metric in kept_metrics:
        first_metric_by_table.setdefault(metric.table, metric.id)

    kept_dimensions: list[DraftDimension] = []
    for dimension in dimensions:
        metric_id = first_metric_by_table.get(dimension.table)
        if metric_id is None:
            # Without a metric in its own dataset a dimension cannot be used: joins are not supported yet.
            drops.append(Drop("dimension", dimension.id, "no_metric_for_dimension"))
            continue
        code = _probe(working, ctx, dimension.table, [metric_id], dimension, explain)
        if code:
            drops.append(Drop("dimension", dimension.id, code))
        else:
            kept_dimensions.append(dimension)
    return kept_metrics, kept_dimensions


def _probe_contract(
    metrics: list[DraftMetric], dimensions: list[DraftDimension], facts: SchemaFacts, ctx: DraftContext
) -> SemanticContract:
    tables = sorted({m.table for m in metrics} | {d.table for d in dimensions})
    return _build_contract(metrics, dimensions, tables, {}, facts, ctx, "probe", [])


def _probe(
    contract: SemanticContract,
    ctx: DraftContext,
    table: str,
    metric_ids: list[str],
    dimension: DraftDimension | None,
    explain: ExplainProbe | None,
) -> str | None:
    group_by = []
    if dimension is not None:
        granularity = "month" if dimension.dimension_type == "temporal" else None
        group_by = [IntentGrouping(dimension_id=dimension.id, time_granularity=granularity)]
    try:
        intent = AnalyticalIntent(
            query_id="draft-probe",
            tenant_id=ctx.tenant_id,
            dataset_id=table,
            semantic_contract=SemanticContractReference(contract_id=contract.id, contract_version=contract.version),
            metrics=[IntentMetric(metric_id=metric_id) for metric_id in metric_ids],
            group_by=group_by,
            limit=1,
        )
        compiled = PostgreSQLCompiler().compile(intent, contract)
    except (CompilationError, ValueError):
        return "compile_failed"
    try:
        validate_read_only_sql(compiled.sql, dialect="postgres", allowed_tables=[table], case_sensitive_quoted=True)
    except QueryRejected:
        return "sql_rejected"
    if explain is not None:
        try:
            explain(compiled.sql, dict(compiled.parameters))
        except Exception as exc:  # the probe is injected; report only the class, never its message or any value
            return f"explain_failed:{type(exc).__name__}"[:60]
    return None


# ----------------------------------------------------------------------------------------------------------------- emit


def _build_contract(
    metrics: list[DraftMetric],
    dimensions: list[DraftDimension],
    tables: list[str],
    texts: dict[str, DraftDatasetText],
    facts: SchemaFacts,
    ctx: DraftContext,
    fingerprint: str,
    drops: list[Drop],
) -> SemanticContract:
    owner = SemanticOwner(id=ctx.owner_id, display_name=ctx.owner_name, owner_type="team")
    datasets: list[SemanticDataset] = []
    fields: list[SemanticField] = []
    for name in tables:
        table = facts.table(name)
        assert table is not None  # tables come from validated items
        text = texts.get(name)
        description = text.description if text and not bad_text(text.description) else None
        if text and description is None:
            drops.append(Drop("dataset", name, "bad_description", "used the schema comment instead"))
        datasets.append(
            SemanticDataset(
                id=name,
                display_name=text.display_name if text and not bad_text(text.display_name) else name,
                source_asset_id=table.asset_id,
                physical_name=name,
                description=description or _safe_comment(table.comment) or "No description provided. Draft for review.",
                owner_ids=[owner.id],
            )
        )
        for column in table.columns:
            fields.append(
                SemanticField(
                    id=f"{name}.{column.name}",
                    dataset_id=name,
                    physical_name=column.name,
                    data_type=column.data_type,
                    classification="restricted" if is_sensitive_name(column.name) else "internal",
                )
            )
    return SemanticContract(
        id=ctx.contract_id,
        tenant_id=ctx.tenant_id,
        domain=ctx.domain,
        version=f"draft-{fingerprint[:8]}",
        owners=[owner],
        datasets=datasets,
        fields=fields,
        dimensions=[
            SemanticDimension(
                id=d.id,
                dataset_id=d.table,
                field_id=f"{d.table}.{d.column}",
                dimension_type=d.dimension_type,
                owner_ids=[owner.id],
            )
            for d in dimensions
        ],
        metrics=[
            SemanticMetric(
                id=m.id,
                dataset_id=m.table,
                aggregation=m.aggregation,
                measure_field_id=f"{m.table}.{m.measure_column}" if m.measure_column else None,
                grain=SemanticGrain(kind="row", key_field_ids=[f"{m.table}.{c}" for c in m.grain_key_columns]),
                certification="candidate",
                owner_ids=[owner.id],
            )
            for m in metrics
        ],
        metadata={
            "draft": {
                "status": "draft, not certified",
                "schema_fingerprint": fingerprint,
                "model_id": ctx.model_id,
                "prompt_version": ctx.prompt_version,
                "drafter_version": DRAFTER_VERSION,
            }
        },
    )


def _safe_comment(comment: str | None) -> str | None:
    if not comment or bad_text(comment):
        return None
    return comment[:500]


def _build_ontology(
    contract: SemanticContract | None,
    terms: list[DraftTerm],
    ctx: DraftContext,
    fingerprint: str,
    now: datetime,
) -> OntologySnapshot:
    provenance = (
        OntologyProvenance(
            source_system=SOURCE_SYSTEM,
            source_id=ctx.contract_id,
            source_version=f"{DRAFTER_VERSION}/{ctx.prompt_version}",
            observed_at=now,
            fingerprint=fingerprint,
        ),
    )
    nodes: list[OntologyNode] = []
    edges: list[OntologyEdge] = []

    def node(node_id: str, node_type: str, label: str, attributes: dict) -> None:
        nodes.append(
            OntologyNode(
                node_id=node_id,
                tenant_id=ctx.tenant_id,
                node_type=node_type,  # type: ignore[arg-type]
                label=label[:255],
                lifecycle="candidate",
                valid_from=now,
                provenance=provenance,
                attributes=attributes,
            )
        )

    def edge(parent: str, child: str) -> None:
        edges.append(
            OntologyEdge(
                edge_id=f"contains.{parent}.{child}",
                tenant_id=ctx.tenant_id,
                edge_type="contains",
                from_node_id=f"dataset.{parent}",
                to_node_id=child,
                valid_from=now,
                provenance=provenance,
            )
        )

    if contract is not None:
        for dataset in contract.datasets:
            node(f"dataset.{dataset.id}", "dataset", dataset.display_name, {"description": dataset.description})
        for metric in contract.metrics:
            node(f"metric.{metric.id}", "metric", metric.id, {"aggregation": metric.aggregation})
            edge(metric.dataset_id, f"metric.{metric.id}")
        for dimension in contract.dimensions:
            node(f"dimension.{dimension.id}", "dimension", dimension.id, {"dimension_type": dimension.dimension_type})
            edge(dimension.dataset_id, f"dimension.{dimension.id}")
    for term in terms:
        node(f"term.{_slug(term.term)}", "glossary_term", term.term, {"definition": term.definition})
    return OntologySnapshot(
        snapshot_id=f"draft-{fingerprint[:12]}",
        tenant_id=ctx.tenant_id,
        captured_at=now,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


__all__ = ["CandidateJoin", "DraftContext", "DraftResult", "ExplainProbe", "bad_text", "validate_and_emit"]
