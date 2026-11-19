"""K1: the intent coverage check. Unit behaviour on the sandbox contract with a reviewed synonym set (a TEST FIXTURE, not a certification),
the neutral list, the evidence reference, and the wiring end to end through the governed runtime (off by default, per tenant, refusal naming
the requester's own words, no words in evidence, and only ever stricter)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from reference_stack.fakes import ScriptedIntentClient, seed_lake
from reference_stack.stack import build_stack
from test_m7_pilot_wiring import DOCUMENT, REGISTRY, _client

from app.compiler import DuckDBCompilerAdapter
from app.compiler.service import CertifiedIntentCompiler
from app.context.registry_context import CertifiedContracts, context_from_contract
from app.execution import DuckDBGateway
from app.runtime import intent_coverage as ic
from app.runtime.evidence_store import EvidenceStore
from app.runtime.pilot_wiring import GovernedRuntime
from packages.platform_contracts.analytics_intent import AnalyticalIntent
from packages.platform_contracts.semantic import SemanticRegistryDocument
from packages.platform_contracts.semantic_terms import tokens

SANDBOX = json.loads((Path(__file__).resolve().parent.parent / "semantic_registry_sandbox/contracts/sandbox-databricks-v1.json").read_text())
WORDS = {"order_count": ["how many orders", "number of orders", "orders placed", "orders received"], "order_amount_sum": ["total order amount", "order total"]}
SORT_DESC = [{"target_kind": "metric", "target_id": "order_count", "direction": "desc"}]


def build(words):
    raw = json.loads(json.dumps(SANDBOX))
    for m in raw["contract"]["metrics"]:
        m["synonyms"] = words.get(m["id"], [])
    return context_from_contract(SemanticRegistryDocument.model_validate(raw))[1]


ONT = build(WORDS)


def intent(metric="order_count", **kw):
    return AnalyticalIntent.model_validate({"query_id": "q", "tenant_id": "sandbox", "dataset_id": "orders", "metrics": [{"metric_id": metric}],
                                            "semantic_contract": {"contract_id": "sandbox-databricks", "contract_version": "2026-11-18"}, **kw})


def left(question, metric="order_count", ontology=ONT, **kw):
    words = tokens(question)
    return [words[i] for i in ic.unaccounted_positions(question, intent(metric, **kw), ontology)]


# ---- what is covered -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("question", [
    "How many orders are there?", "What is the number of orders?", "Show me the order count", "orders placed",
    "Give me the number of orders please", "Tell me how many orders we have", "What's the order count?",
    "How many orders do we have in total?", "How many orders received?",
])
def test_a_fully_covered_question_has_nothing_left(question):
    assert left(question) == []


@pytest.mark.parametrize(("question", "metric", "expected"), [
    ("How many orders were refunded?", "order_count", {"refunded"}),
    ("How many orders were placed last year?", "order_count", {"last", "year"}),
    ("How many orders were placed in EMEA?", "order_count", {"emea"}),
    ("What is the total profit?", "order_amount_sum", {"profit"}),
    ("How many orders and what is the total order amount?", "order_count", {"total", "order", "amount"}),
    ("Show the order count and the order amount sum", "order_count", {"amount", "sum"}),
])
def test_a_constraint_or_second_metric_the_intent_omits_is_left(question, metric, expected):
    assert expected <= set(left(question, metric))


def test_time_words_are_accounted_only_when_the_intent_has_a_time_range_or_a_time_grouping():
    rng = {"time_range": {"dimension_id": "amount", "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}}
    assert {"last", "month"} <= set(left("How many orders last month?"))
    assert left("How many orders last month?", **rng) == []
    assert left("How many orders in January 2024?", **rng) == []
    grouped = {"group_by": [{"dimension_id": "amount", "time_granularity": "month"}]}
    assert left("How many orders per month?", **grouped) == [] and "last" in left("How many orders per month last year?", **grouped)


def test_a_grouping_without_a_time_granularity_does_not_account_for_time_words():
    assert "month" in left("How many orders per month by amount?", group_by=[{"dimension_id": "amount"}])


def test_sort_and_limit_words_are_accounted_only_when_the_intent_holds_them():
    assert {"top", "5"} <= set(left("Top 5 order counts"))
    assert left("Top 5 order counts", limit=5, sort=SORT_DESC) == []
    assert left("Top 5 order counts", sort=SORT_DESC) == ["5"]  # the number needs the limit
    assert left("Top order counts", limit=5) == []  # a changed limit accounts for "top" and its number


def test_a_filter_accounts_for_its_field_and_its_values():
    flt = {"filters": [{"field_id": "orders.amount", "operator": "equals", "values": ["210"]}]}
    assert left("How many orders with order amount 210?", **flt) == []
    assert {"with", "amount", "210"} <= set(left("How many orders with order amount 210?"))  # filter words need a filter


def test_a_domain_word_is_covered_by_a_certified_synonym_never_by_the_neutral_list():
    question = "How many orders were placed?"
    assert left(question) == ["placed"]  # "orders placed" is not contiguous here, and "placed" is a domain word
    assert ic.unaccounted_positions(question, intent(), build({"order_count": ["how many orders were placed"]})) == []
    assert not ic.NEUTRAL_WORDS & {"placed", "received", "refunded", "order", "orders", "amount", "total", "count", "sales"}


def test_the_neutral_list_is_small_generic_and_versioned():
    assert ic.NEUTRAL_VERSION == "neutral-v1" and len(ic.NEUTRAL_WORDS) <= 90 and all(w.isalpha() for w in ic.NEUTRAL_WORDS)
    assert not ic.NEUTRAL_WORDS & {"revenue", "profit", "sales", "customer", "product", "region", "year", "month", "last", "top", "by", "per"}


# ---- evidence and the requester's words -------------------------------------------------------------------------------------------


def test_the_reference_holds_positions_and_a_hash_never_the_words():
    q = "How many orders were refunded in EMEA?"
    pos = ic.unaccounted_positions(q, intent(), ONT)
    ref = ic.coverage_reference(q, pos)
    assert "refund" not in ref and "emea" not in ref.lower() and ref.startswith(f"unaccounted_words:{len(pos)}@")
    assert ic.words_from_reference(q, ref) == [tokens(q)[i] for i in pos]
    assert hashlib.sha256(" ".join(tokens(q)[i] for i in pos).encode()).hexdigest()[:12] in ref


@pytest.mark.parametrize("bad", ["", "nonsense", "unaccounted_words:1@a#x", "unaccounted_words:2@1,2#zzzzzzzzzzzz"])
def test_a_malformed_reference_yields_no_words(bad):
    assert ic.words_from_reference("a b c", bad) == []


def test_a_position_past_the_question_yields_no_word():
    assert ic.words_from_reference("a b", "unaccounted_words:1@99#aaaaaaaaaaaa") == []


# ---- end to end through the governed runtime --------------------------------------------------------------------------------------


class PlainRevenue:
    """A model that always reads the question as plain revenue, dropping any constraint the question adds."""

    def __init__(self, rid):
        self.rid = rid

    def complete_json(self, *, prompt, schema, max_tokens):
        return {"query_id": self.rid, "tenant_id": "tenant-a", "dataset_id": "orders", "semantic_contract": {"contract_id": "sales-core", "contract_version": "v1"},
                "metrics": [{"metric_id": "revenue"}]}


def runtime(tmp_path, coverage=frozenset()):
    stack = build_stack("duckdb", tmp_path / "control")
    parquet = seed_lake(tmp_path / "lake")
    rt = GovernedRuntime(control=stack.control, evidence=EvidenceStore(stack.control.engine), gateway=DuckDBGateway({"sales_orders": parquet}, allowed_root=tmp_path / "lake"),
                         compiler=CertifiedIntentCompiler(REGISTRY, DuckDBCompilerAdapter()), contracts=CertifiedContracts([DOCUMENT]),
                         contexts={"tenant-a": context_from_contract(DOCUMENT)}, model_factory=lambda rid: PlainRevenue(rid), coverage_tenants=coverage)
    return _client(rt, stack.verifier()), stack, rt


def ask(client, stack, text, key):
    auth = {"Authorization": f"Bearer {stack.token('requester')}", "Idempotency-Key": key}
    return client.post("/api/v2/analytics/analyze", json={"request_text": text, "purpose": "analytics"}, headers=auth).json()


def test_off_by_default_a_dropped_constraint_is_answered_as_today(tmp_path):
    client, stack, _ = runtime(tmp_path)
    assert ask(client, stack, "Show revenue for refunded orders", "cov-key-000001")["outcome"]["outcome"] == "answer"  # the hole this check closes


def test_enabled_for_the_tenant_the_same_question_is_refused_naming_the_requesters_words(tmp_path):
    client, stack, _ = runtime(tmp_path, frozenset({"tenant-a"}))
    body = ask(client, stack, "Show revenue for refunded orders", "cov-key-000002")
    out = body["outcome"]
    assert body["state"] == "terminal" and out["outcome"] == "refuse" and out["reason_code"] == "unsupported_request"
    assert '"refunded"' in out["explanation"] and "certified" in out["remediation"]
    assert ask(client, stack, "Show revenue", "cov-key-000003")["outcome"]["outcome"] == "answer"  # a covered question is untouched
    assert ask(client, stack, "What is the revenue please?", "cov-key-000004")["outcome"]["outcome"] == "answer"


def test_the_stored_evidence_never_holds_the_words(tmp_path):
    from sqlalchemy import text

    client, stack, _ = runtime(tmp_path, frozenset({"*"}))
    body = ask(client, stack, "Show revenue for refunded orders", "cov-key-000005")
    with stack.control.engine.connect() as c:
        stored = " ".join(str(v) for row in c.execute(text("SELECT * FROM analytics_run_transitions")).mappings() for v in row.values())
        stored += " ".join(str(v) for row in c.execute(text("SELECT * FROM analytics_evidence_envelopes")).mappings() for v in row.values())
    assert body["outcome"]["outcome"] == "refuse" and "unaccounted_words" in stored and "refunded" not in stored.lower()


def test_another_tenant_is_not_affected_by_the_switch(tmp_path):
    client, stack, _ = runtime(tmp_path, frozenset({"some-other-tenant"}))
    assert ask(client, stack, "Show revenue for refunded orders", "cov-key-000006")["outcome"]["outcome"] == "answer"


def test_the_check_only_ever_adds_refusals(tmp_path):
    """On a question the echo check already refuses, the outcome is the echo refusal either way: coverage never changes it."""
    off, stack, _ = runtime(tmp_path / "a")
    on, stack2, _ = runtime(tmp_path / "b", frozenset({"*"}))
    a, b = ask(off, stack, "Show total profit", "cov-key-000007")["outcome"], ask(on, stack2, "Show total profit", "cov-key-000007")["outcome"]
    assert a["outcome"] == b["outcome"] == "refuse" and a["explanation"] == b["explanation"]
    assert ScriptedIntentClient  # imported to keep the reference stack's scripted model importable beside the custom one
