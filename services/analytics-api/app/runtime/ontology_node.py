"""Certified-ontology resolution for model-proposed semantic IDs."""

from __future__ import annotations

import hashlib
import json
import re

from app.runtime.intent_coverage import coverage_reference, unaccounted_positions
from app.runtime.intent_echo import echo_problems
from packages.platform_contracts.agent_runtime import EvidenceReference, NodeInput, NodeOutput, RunError
from packages.platform_contracts.analytics_intent import AnalyticalIntent
from packages.platform_contracts.analytics_planning import AnalyticsAmbiguity
from packages.platform_contracts.ontology import OntologySnapshot
from packages.platform_contracts.semantic import SemanticContract


class IntentResolutionError(ValueError):
    def __init__(self, code: str, candidates: tuple[str, ...] = (), ambiguity_code: str = "metric"):
        self.code = code
        self.candidates = candidates
        self.ambiguity_code = ambiguity_code
        super().__init__(code)


def resolve_certified_intent(
    intent: AnalyticalIntent, contract: SemanticContract, ontology: OntologySnapshot
) -> AnalyticalIntent:
    if ontology.tenant_id != contract.tenant_id or intent.tenant_id != contract.tenant_id:
        raise IntentResolutionError("tenant_scope_mismatch")
    allowed = {
        "metric": {item.id for item in contract.metrics if item.certification == "certified"},
        "dimension": {item.id for item in contract.dimensions},
        "field": {item.id for item in contract.fields},
        "dataset": {item.id for item in contract.datasets},
    }
    aliases: dict[str, dict[str, set[str]]] = {kind: {} for kind in allowed}
    for node in ontology.nodes:
        kind = "field" if node.node_type == "field" else node.node_type
        if node.lifecycle != "certified" or kind not in allowed or node.node_id not in allowed[kind]:
            continue
        for alias in (
            node.label,
            *_aliases(node.attributes.get("aliases")),
            *_aliases(node.attributes.get("synonyms")),
        ):
            key = _normalize(alias)
            if key:
                aliases[kind].setdefault(key, set()).add(node.node_id)

    def canonical(kind: str, identifier: str) -> str:
        if identifier in allowed[kind]:
            return identifier
        matches = aliases[kind].get(_normalize(identifier), set())
        if len(matches) == 1:
            return next(iter(matches))
        if len(matches) > 1:
            ambiguity_code = {
                "dimension": "time" if intent.time_range else "grain",
                "field": "filter",
            }.get(kind, kind)
            raise IntentResolutionError("ambiguous_semantic_alias", tuple(sorted(matches)), ambiguity_code)
        raise IntentResolutionError("unknown_or_uncertified_semantic_id")

    values = intent.model_dump(mode="python")
    values["dataset_id"] = canonical("dataset", intent.dataset_id)
    values["metrics"] = [
        {**metric.model_dump(), "metric_id": canonical("metric", metric.metric_id)} for metric in intent.metrics
    ]
    values["group_by"] = [
        {**group.model_dump(), "dimension_id": canonical("dimension", group.dimension_id)} for group in intent.group_by
    ]
    if intent.time_range:
        values["time_range"] = {
            **intent.time_range.model_dump(),
            "dimension_id": canonical("dimension", intent.time_range.dimension_id),
        }
    values["filters"] = [
        {**item.model_dump(), "field_id": canonical("field", item.field_id)} for item in intent.filters
    ]
    resolved = AnalyticalIntent.model_validate(values)
    resolved.validate_against(contract)
    certified_nodes = {node.node_id for node in ontology.nodes if node.lifecycle == "certified"}
    selected = {resolved.dataset_id, *(metric.metric_id for metric in resolved.metrics)}
    selected.update(group.dimension_id for group in resolved.group_by)
    selected.update(item.field_id for item in resolved.filters)
    if resolved.time_range:
        selected.add(resolved.time_range.dimension_id)
    if selected - certified_nodes:
        raise IntentResolutionError("selected_ids_not_ontology_certified")
    uncertified_metrics = {
        metric.metric_id
        for metric in resolved.metrics
        if next(item for item in contract.metrics if item.id == metric.metric_id).certification != "certified"
    }
    if uncertified_metrics:
        raise IntentResolutionError("selected_metrics_not_certified")
    return resolved


class SnapshotOntologyProvider:
    """Serve the ontology embedded in a published `ContextSnapshot`.

    `snapshots` is anything with `get(snapshot_id, tenant_id) -> ContextSnapshot`, such as
    `ContextSnapshotRegistry`; the exact-ID, tenant-scoped lookup is the registry's. The
    resolution node pins ontology identity to the run's context snapshot, so the graph
    is returned under that snapshot ID (its content fingerprint is already part of the
    snapshot's own). A snapshot with no ontology fails closed rather than resolving
    against nothing.
    """

    def __init__(self, snapshots) -> None:
        self._snapshots = snapshots

    def get(self, snapshot_id: str, tenant_id: str) -> OntologySnapshot:
        snapshot = self._snapshots.get(snapshot_id, tenant_id)
        if snapshot.snapshot_id != snapshot_id or snapshot.tenant_id != tenant_id:
            raise LookupError("context snapshot scope mismatch")
        if snapshot.ontology is None:
            raise LookupError("context snapshot carries no ontology")
        return snapshot.ontology.model_copy(update={"snapshot_id": snapshot.snapshot_id})


def interpretation_token(intent: AnalyticalIntent) -> str:
    """A token that names exactly one reading: the contract, metrics, groupings, filters, time range, sort and limit. A confirmation is only
    valid for the reading it was given for, so a changed intent can never ride on an earlier "yes"."""
    canonical = json.dumps(
        {"c": [intent.semantic_contract.contract_id, intent.semantic_contract.contract_version], "d": intent.dataset_id,
         "m": [m.metric_id for m in intent.metrics], "g": [g.dimension_id for g in intent.group_by],
         "f": [f.model_dump(mode="json") for f in intent.filters], "t": intent.time_range.model_dump(mode="json") if intent.time_range else None,
         "s": [s.model_dump(mode="json") for s in intent.sort], "l": intent.limit}, sort_keys=True, separators=(",", ":"))
    return "I:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def describe_interpretation(intent: AnalyticalIntent, contract: SemanticContract) -> str:
    """Plain words for the person, built from the certified contract and the intent's ids. Never data values from the warehouse."""
    names = {m.id: m for m in contract.metrics}
    dims = {d.id: d for d in contract.dimensions}

    def label(item, fallback: str) -> str:
        return (getattr(item, "display_name", None) or fallback.replace("_", " "))[:80]

    parts = []
    for ref in intent.metrics:
        m = names.get(ref.metric_id)
        how = "a count of rows" if m and m.aggregation == "count" and not m.measure_field_id else (
            f"the {m.aggregation} of {m.measure_field_id.split('.')[-1].replace('_', ' ')}" if m and m.measure_field_id else "a certified measure")
        parts.append(f"{label(m, ref.metric_id)} ({how})")
    text = "I read this as: " + " and ".join(parts)
    if intent.group_by:
        text += ", grouped by " + ", ".join(label(dims.get(g.dimension_id), g.dimension_id) for g in intent.group_by)
    extras = []
    if intent.filters:
        extras.append("filtered by " + "; ".join(f"{f.field_id.split('.')[-1].replace('_', ' ')} {f.operator.replace('_', ' ')} {', '.join(str(v)[:40] for v in f.values)}" for f in intent.filters))
    if intent.time_range:
        extras.append("limited to a time period")
    if intent.sort:
        extras.append("sorted")
    text += "; " + ("; ".join(extras) if extras else "with no filter, no time period and no sort") + ". Is that what you meant?"
    return text[:1_900]


def has_synonyms(contract: SemanticContract) -> bool:
    return any(item.synonyms for kind in (contract.datasets, contract.metrics, contract.dimensions, contract.fields) for item in kind if getattr(item, "synonyms", None))


def ontology_resolution_node(contracts, ontologies, *, echo_check: bool = True, coverage_tenants: frozenset[str] | None = None,
                             confirm_tenants: frozenset[str] | None = None):
    """Create a handler whose lookups are exact, tenant-scoped snapshot reads."""

    def handle(node_input: NodeInput) -> NodeOutput:
        try:
            proposed = AnalyticalIntent.model_validate(node_input.payload.get("intent"))
            contract = contracts.get_certified(
                proposed.semantic_contract.contract_id,
                proposed.semantic_contract.contract_version,
            ).contract
            ontology = ontologies.get(node_input.context_snapshot_id, node_input.tenant_id)
            if ontology.snapshot_id != node_input.context_snapshot_id:
                raise IntentResolutionError("ontology_snapshot_mismatch")
            if has_synonyms(contract) and not (coverage_tenants and ("*" in coverage_tenants or node_input.tenant_id in coverage_tenants)):
                # A certified synonym makes the echo check pass more often; without the coverage check a dropped constraint then becomes a confident
                # wrong answer (SEMANTIC_SYNONYMS_DESIGN, "the main finding"). So a contract with synonyms is refused for a tenant without coverage.
                raise IntentResolutionError("synonyms_need_coverage")
            resolved = resolve_certified_intent(proposed, contract, ontology)
            if resolved.tenant_id != node_input.tenant_id:
                raise IntentResolutionError("tenant_scope_mismatch")
            echo = echo_problems(node_input.request_text, resolved, ontology) if echo_check else []
            coverage: list[int] = []
            if not echo and coverage_tenants and ("*" in coverage_tenants or node_input.tenant_id in coverage_tenants):
                coverage = unaccounted_positions(node_input.request_text, resolved, ontology)
        except IntentResolutionError as exc:
            if exc.candidates and exc.code == "ambiguous_semantic_alias":
                previous = node_input.payload.get("clarification_state") or {}
                try:
                    count = int(previous.get("continuation_count", 0))
                    if count >= 2:
                        raise ValueError("clarification limit reached")
                    state = {
                        "query_id": proposed.query_id,
                        "request_fingerprint": hashlib.sha256((node_input.request_text or "").encode()).hexdigest(),
                        "run_id": node_input.run_id,
                        "tenant_id": node_input.tenant_id,
                        "purpose": node_input.purpose,
                        "ambiguities": [
                            AnalyticsAmbiguity(
                                code=exc.ambiguity_code,
                                prompt=f"Select the intended certified {exc.ambiguity_code}.",
                                candidate_ids=list(exc.candidates),
                            ).model_dump(mode="json")
                        ],
                        "continuation_count": count,
                    }
                    return NodeOutput(
                        run_id=node_input.run_id,
                        node_id=node_input.node_id,
                        status="waiting",
                        next_node="clarify",
                        payload={
                            "clarification_state": state,
                            "approval_state": {"reason": "ambiguous_certified_reference"},
                        },
                        evidence=(_evidence(node_input, f"ambiguity:{exc.ambiguity_code}:{','.join(exc.candidates)}"),),
                    )
                except Exception as clarification_exc:  # noqa: BLE001 - ambiguous loops must stop.
                    exc = IntentResolutionError(f"clarification_rejected:{type(clarification_exc).__name__}")
            reason = exc.code
            fingerprint = hashlib.sha256(reason.encode()).hexdigest()
            return NodeOutput(
                run_id=node_input.run_id,
                node_id=node_input.node_id,
                status="failed",
                error=RunError(code="stale_context", message_reference=f"ontology:{reason}"),
                evidence=(
                    EvidenceReference(
                        evidence_id=f"ontology-error:{node_input.run_id}",
                        kind="error",
                        fingerprint=fingerprint,
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - unresolved identifiers fail before planning.
            reason = exc.code if isinstance(exc, IntentResolutionError) else type(exc).__name__
            fingerprint = hashlib.sha256(reason.encode()).hexdigest()
            return NodeOutput(
                run_id=node_input.run_id,
                node_id=node_input.node_id,
                status="failed",
                error=RunError(code="stale_context", message_reference=f"ontology:{reason}"),
                evidence=(
                    EvidenceReference(
                        evidence_id=f"ontology-error:{node_input.run_id}",
                        kind="error",
                        fingerprint=fingerprint,
                    ),
                ),
            )
        confirmed = ""
        if (echo or coverage) and confirm_tenants and ("*" in confirm_tenants or node_input.tenant_id in confirm_tenants):
            token = interpretation_token(resolved)
            previous = node_input.payload.get("clarification_state") or {}
            chosen = (previous.get("selected_choices") or {}).get("interpretation")
            if chosen == token:  # a person confirmed exactly this reading: their statement replaces the lexical match, nothing else is relaxed
                echo, coverage, confirmed = [], [], token
            elif chosen is None and int(previous.get("continuation_count", 0)) < 2:
                state = {
                    "query_id": resolved.query_id,
                    "request_fingerprint": hashlib.sha256((node_input.request_text or "").encode()).hexdigest(),
                    "run_id": node_input.run_id, "tenant_id": node_input.tenant_id, "purpose": node_input.purpose,
                    "ambiguities": [AnalyticsAmbiguity(code="interpretation", prompt=describe_interpretation(resolved, contract),
                                                       candidate_ids=[token, "none"]).model_dump(mode="json")],
                    "continuation_count": int(previous.get("continuation_count", 0)),
                }
                return NodeOutput(
                    run_id=node_input.run_id, node_id=node_input.node_id, status="waiting", next_node="clarify",
                    payload={"clarification_state": state, "approval_state": {"reason": "interpretation_confirmation"}},
                    evidence=(_evidence(node_input, f"interpretation-asked:{token}"),),
                )
            # an answer of "none", or a confirmation for a different reading, ends as the refusal it would have been
        if echo:
            reference = ";".join(echo)[:250]
            return NodeOutput(
                run_id=node_input.run_id,
                node_id=node_input.node_id,
                status="failed",
                error=RunError(code="intent_echo_mismatch", message_reference=reference),
                evidence=(
                    EvidenceReference(
                        evidence_id=f"intent-echo:{node_input.run_id}",
                        kind="error",
                        fingerprint=hashlib.sha256(reference.encode()).hexdigest(),
                    ),
                ),
            )
        if coverage:
            reference = coverage_reference(node_input.request_text, coverage)
            return NodeOutput(
                run_id=node_input.run_id,
                node_id=node_input.node_id,
                status="failed",
                error=RunError(code="intent_coverage_mismatch", message_reference=reference),
                evidence=(
                    EvidenceReference(
                        evidence_id=f"intent-coverage:{node_input.run_id}",
                        kind="error",
                        fingerprint=hashlib.sha256(reference.encode()).hexdigest(),
                    ),
                ),
            )
        fingerprint = hashlib.sha256(resolved.model_dump_json().encode()).hexdigest()
        return NodeOutput(
            run_id=node_input.run_id,
            node_id=node_input.node_id,
            status="completed",
            next_node="plan",
            payload={"intent": resolved.model_dump(mode="json")},
            evidence=(
                EvidenceReference(
                    evidence_id=f"ontology:{node_input.context_snapshot_id}:{resolved.dataset_id}",
                    kind="context",
                    fingerprint=fingerprint,
                ),
                *((_evidence(node_input, f"interpretation-confirmed:{confirmed}"),) if confirmed else ()),
            ),
        )

    return handle


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _aliases(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _evidence(node_input: NodeInput, value: str) -> EvidenceReference:
    fingerprint = hashlib.sha256(value.encode()).hexdigest()
    return EvidenceReference(
        evidence_id=f"ontology:{node_input.run_id}:{fingerprint[:24]}",
        kind="decision",
        fingerprint=fingerprint,
    )
