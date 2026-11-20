"""How answers change when a contract changes, with the model taken out of the loop.

An eval case pins the question, the **typed intent a model produced for it** (recorded from an approved run) and what the owner expects.
The deterministic part of the pipeline then runs against each version of the contract: resolution, the echo check, the coverage
check and the compiler. No model is called, so the result is repeatable and free. It cannot see a change in a model's behaviour (that
needs a separate, approved live run) and it cannot judge whether a definition is right for the business (that is certification).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.compiler import DuckDBCompilerAdapter
from app.context.registry_context import context_from_contract
from app.runtime.intent_coverage import unaccounted_positions
from app.runtime.intent_echo import echo_problems
from app.runtime.ontology_node import IntentResolutionError, resolve_certified_intent
from packages.platform_contracts.analytics_intent import AnalyticalIntent
from packages.platform_contracts.semantic import SemanticRegistryDocument


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Expect(_Strict):
    outcome: Literal["answered", "refused"]
    metric_id: str | None = None  # for an answer: the metric it must use
    code: str | None = None  # for a refusal: the reason code it must carry


class EvalCase(_Strict):
    case_id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2_000)
    recorded_intent: dict[str, Any]
    expect: Expect


class EvalFile(_Strict):
    contract_id: str = Field(min_length=1, max_length=255)
    cases: tuple[EvalCase, ...] = Field(min_length=1)


@dataclass(frozen=True)
class Verdict:
    outcome: str  # answered | refused
    code: str = ""  # a refusal's reason
    metric_ids: tuple[str, ...] = ()
    sql_hash: str = ""  # of the compiled statement and its parameters, for an answer


@dataclass(frozen=True)
class CaseChange:
    case_id: str
    question: str
    old: Verdict
    new: Verdict
    expectation_met_old: bool
    expectation_met_new: bool

    @property
    def changed(self) -> bool:
        return self.old != self.new


def _met(expect: Expect, v: Verdict) -> bool:
    if expect.outcome != v.outcome:
        return False
    if expect.outcome == "answered":
        return expect.metric_id is None or expect.metric_id in v.metric_ids
    return expect.code is None or expect.code == v.code


def verdict_for(case: EvalCase, document: SemanticRegistryDocument, *, adapter: Any = None, coverage: bool = True) -> Verdict:
    """Run the deterministic pipeline for one case against one certified contract. The recorded intent is bound to this version."""
    contract = document.contract
    try:
        raw = {**case.recorded_intent, "tenant_id": contract.tenant_id,
               "semantic_contract": {"contract_id": contract.id, "contract_version": contract.version}}
        proposed = AnalyticalIntent.model_validate(raw)
        _, ontology = context_from_contract(document)
        resolved = resolve_certified_intent(proposed, contract, ontology)
    except IntentResolutionError as exc:
        return Verdict("refused", exc.code)
    except Exception:  # noqa: BLE001 - an intent the contract cannot even read is a refusal, not a crash
        return Verdict("refused", "invalid_intent")
    if echo_problems(case.question, resolved, ontology):
        return Verdict("refused", "intent_echo_mismatch")
    if coverage and unaccounted_positions(case.question, resolved, ontology):
        return Verdict("refused", "intent_coverage_mismatch")
    try:
        compiled = (adapter or DuckDBCompilerAdapter()).compile(resolved, contract, None)
    except Exception as exc:  # noqa: BLE001
        return Verdict("refused", f"compile_error:{type(exc).__name__}")
    digest = hashlib.sha256(json.dumps([compiled.sql, compiled.parameters], sort_keys=True, default=str).encode()).hexdigest()
    return Verdict("answered", "", tuple(m.metric_id for m in resolved.metrics), digest)


def behaviour_diff(old: SemanticRegistryDocument, new: SemanticRegistryDocument, cases: tuple[EvalCase, ...] | list[EvalCase],
                   *, adapter_factory: Callable[[], Any] | None = None) -> list[CaseChange]:
    make = adapter_factory or DuckDBCompilerAdapter
    out = []
    for case in cases:
        a, b = verdict_for(case, old, adapter=make()), verdict_for(case, new, adapter=make())
        out.append(CaseChange(case.case_id, case.question, a, b, _met(case.expect, a), _met(case.expect, b)))
    return out


__all__ = ["CaseChange", "EvalCase", "EvalFile", "Expect", "Verdict", "behaviour_diff", "verdict_for"]
