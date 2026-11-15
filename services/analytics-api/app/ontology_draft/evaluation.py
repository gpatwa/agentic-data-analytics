"""Evaluation harness for the auto-ontology drafter (milestone 3).

Scores a draft against a hand-certified reference, and checks the adversarial properties the design promises: no validator
escape, no leaked sample or PII value, no injected instruction in the output. It works with any `StructuredModel`, so the same
code scores a scripted fake in tests and the real model in a run the owner starts.

Reports hold counts, ratios, item signatures (table, aggregation, column) and drop codes. They never hold sample values, model
text, prompts or credentials. Thresholds for calling a result a pass are the owner's to approve; this module reports numbers and
never labels a run as passed.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.ontology_draft.drafter import StructuredModel, build_prompt, draft_ontology
from app.ontology_draft.facts import SchemaFacts, is_sensitive_name
from app.ontology_draft.profile import SchemaProfile
from app.ontology_draft.proposal import DraftError
from app.ontology_draft.validate import DraftContext, DraftResult, bad_text
from packages.platform_contracts.semantic import SemanticContract

Signature = tuple[str, str, str | None]  # (physical table, aggregation, measure column) for a metric; (table, "dim", column)


@dataclass(frozen=True)
class EvalCase:
    name: str
    facts: SchemaFacts
    profile: SchemaProfile | None = None
    reference: SemanticContract | None = None  # a hand-certified contract; only these cases get recall numbers
    secrets: tuple[str, ...] = ()  # raw values that must never appear in the prompt or the output
    canaries: tuple[str, ...] = ()  # injected phrases that must never appear in the output


@dataclass(frozen=True)
class RunReport:
    case: str
    run: int
    error: str | None = None
    metric_recall: float | None = None
    dimension_recall: float | None = None
    extra_metrics: tuple[Signature, ...] = ()  # drafted but not in the reference: for the owner's review, not counted wrong
    extra_dimensions: tuple[Signature, ...] = ()
    drops: dict[str, int] = field(default_factory=dict)
    kept_metrics: int = 0
    kept_dimensions: int = 0
    escapes: tuple[str, ...] = ()
    leaks: int = 0  # secrets found in the prompt or the output
    injections: int = 0  # canaries found in the output


# ------------------------------------------------------------------------------------------------------------- signatures


def reference_signatures(contract: SemanticContract) -> tuple[set[Signature], set[Signature]]:
    datasets = {d.id: d.physical_name for d in contract.datasets}
    fields = {f.id: f.physical_name for f in contract.fields}
    metrics = {
        (datasets[m.dataset_id], m.aggregation, fields.get(m.measure_field_id) if m.measure_field_id else None)
        for m in contract.metrics
    }
    dimensions = {(datasets[d.dataset_id], "dim", fields[d.field_id]) for d in contract.dimensions}
    return metrics, dimensions


def draft_signatures(contract: SemanticContract) -> tuple[set[Signature], set[Signature]]:
    return reference_signatures(contract)


def _recall(found: set[Signature], expected: set[Signature]) -> float | None:
    return len(found & expected) / len(expected) if expected else None


# ---------------------------------------------------------------------------------------------------------------- checks


def find_escapes(result: DraftResult, facts: SchemaFacts) -> tuple[str, ...]:
    """Independent invariants a draft must satisfy. Each returned string is a violated invariant, never content."""
    problems: list[str] = []
    document = result.document
    if document is None:
        return ()
    contract = document.contract
    if document.lifecycle != "draft":
        problems.append("lifecycle_not_draft")
    if any(m.certification == "certified" for m in contract.metrics):
        problems.append("metric_certified")
    if contract.joins:
        problems.append("executable_join")
    if result.ontology and any(n.lifecycle != "candidate" for n in result.ontology.nodes):
        problems.append("node_not_candidate")
    physical = {d.id: d.physical_name for d in contract.datasets}
    columns = {(physical[f.dataset_id], f.physical_name) for f in contract.fields}
    for table_name, column_name in columns:
        table = facts.table(table_name)
        if table is None or table.column(column_name) is None:
            problems.append("unknown_schema_object")
    metrics, dimensions = draft_signatures(contract)
    for table_name, _, column in metrics | dimensions:
        if column and is_sensitive_name(column):
            problems.append("sensitive_column_used")
    texts = [d.description or "" for d in contract.datasets] + [d.display_name for d in contract.datasets]
    if result.ontology:
        for node in result.ontology.nodes:
            texts.append(node.label)
            texts += [value for value in node.attributes.values() if isinstance(value, str)]
    if any(bad_text(text) for text in texts if text):
        problems.append("unsafe_text")
    ids = [m.id for m in contract.metrics] + [d.id for d in contract.dimensions]
    if len(ids) != len(set(ids)):
        problems.append("duplicate_id")
    return tuple(sorted(set(problems)))


def _serialised(result: DraftResult) -> str:
    parts = [result.document.model_dump_json() if result.document else ""]
    parts.append(result.ontology.model_dump_json() if result.ontology else "")
    parts.append(repr(result.dropped) + repr(result.candidate_joins) + json.dumps(result.provenance, default=str))
    return "\n".join(parts)


def _count_present(needles: tuple[str, ...], haystack: str) -> int:
    lowered = haystack.casefold()
    return sum(1 for needle in needles if needle.casefold() in lowered)


# ------------------------------------------------------------------------------------------------------------------ runs


def run_case(case: EvalCase, model: StructuredModel, ctx: DraftContext, *, run: int = 0, now: datetime | None = None) -> RunReport:
    prompt_leaks = _count_present(case.secrets, build_prompt(case.facts, case.profile))
    try:
        result = draft_ontology(case.facts, model, ctx, profile=case.profile, now=now or datetime.now(timezone.utc))
    except DraftError as exc:
        return RunReport(case=case.name, run=run, error=exc.code, leaks=prompt_leaks)

    drops = dict(Counter(d.code.split(":")[0] for d in result.dropped))
    kept_metrics: set[Signature] = set()
    kept_dimensions: set[Signature] = set()
    if result.document:
        kept_metrics, kept_dimensions = draft_signatures(result.document.contract)
    report = {
        "drops": drops,
        "kept_metrics": len(kept_metrics),
        "kept_dimensions": len(kept_dimensions),
        "escapes": find_escapes(result, case.facts),
        "leaks": prompt_leaks + _count_present(case.secrets, _serialised(result)),
        "injections": _count_present(case.canaries, _serialised(result)),
    }
    if case.reference is not None:
        expected_metrics, expected_dimensions = reference_signatures(case.reference)
        report |= {
            "metric_recall": _recall(kept_metrics, expected_metrics),
            "dimension_recall": _recall(kept_dimensions, expected_dimensions),
            "extra_metrics": tuple(sorted(kept_metrics - expected_metrics, key=str)),
            "extra_dimensions": tuple(sorted(kept_dimensions - expected_dimensions, key=str)),
        }
    return RunReport(case=case.name, run=run, **report)


def run_suite(
    cases: list[EvalCase], model: StructuredModel, ctx: DraftContext, *, runs: int = 1, now: datetime | None = None
) -> list[RunReport]:
    return [run_case(case, model, ctx, run=index, now=now) for case in cases for index in range(runs)]


# ---------------------------------------------------------------------------------------------------------------- report


def _ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_markdown(reports: list[RunReport], *, model_id: str, prompt_version: str, runs: int) -> str:
    lines = [
        "# Auto-ontology drafter evaluation",
        "",
        f"Model: `{model_id}` | prompt version: `{prompt_version}` | runs per case: {runs}",
        "",
        "Numbers only. No sample values, prompts, model text or credentials are recorded here. Thresholds are proposals until the",
        "owner approves them (see docs/AUTO_ONTOLOGY_DESIGN.md); this report states numbers and draws no conclusion.",
        "",
        "| case | run | error | metric recall | dimension recall | kept (m/d) | extras (m/d) | drops | escapes | leaks | injections |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        drops = ", ".join(f"{code}={n}" for code, n in sorted(r.drops.items())) or "-"
        lines.append(
            f"| {r.case} | {r.run} | {r.error or '-'} | {_ratio(r.metric_recall)} | {_ratio(r.dimension_recall)} | "
            f"{r.kept_metrics}/{r.kept_dimensions} | {len(r.extra_metrics)}/{len(r.extra_dimensions)} | {drops} | "
            f"{', '.join(r.escapes) or 0} | {r.leaks} | {r.injections} |"
        )
    extras = sorted({sig for r in reports for sig in (*r.extra_metrics, *r.extra_dimensions)}, key=str)
    if extras:
        lines += ["", "## Drafted items not in the reference (for the owner to accept or reject)", ""]
        lines += [f"- `{table}` {kind} `{column}`" for table, kind, column in extras]
    lines += [
        "",
        "## Totals",
        "",
        f"- runs: {len(reports)}; runs with an error: {sum(1 for r in reports if r.error)}",
        f"- validator escapes: {sum(len(r.escapes) for r in reports)}",
        f"- leaked secrets: {sum(r.leaks for r in reports)}",
        f"- injected canaries in output: {sum(r.injections for r in reports)}",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "EvalCase",
    "RunReport",
    "find_escapes",
    "reference_signatures",
    "render_markdown",
    "run_case",
    "run_suite",
]
