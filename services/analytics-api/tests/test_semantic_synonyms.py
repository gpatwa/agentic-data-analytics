"""Certified synonyms (S1): the contract fields and their rules, the context and vocabulary they feed, and the echo check and resolver
that read them. The `certified` documents here are test fixtures standing in for the owner's certification; nothing certifies anything."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.context.registry_context import certified_vocabulary, context_from_contract
from app.runtime.intent_echo import echo_problems
from app.runtime.ontology_node import IntentResolutionError, resolve_certified_intent
from app.semantic_registry import SemanticRegistry
from packages.platform_contracts.analytics_intent import AnalyticalIntent
from packages.platform_contracts.semantic import SemanticRegistryDocument
from packages.platform_contracts.semantic_terms import contains_phrase, normalize_term, synonym_problem

SERVICE = Path(__file__).resolve().parent.parent
SANDBOX = json.loads((SERVICE / "semantic_registry_sandbox/contracts/sandbox-databricks-v1.json").read_text())


def doc(**words):
    """The certified sandbox contract with business words added (a TEST FIXTURE for the owner's certification).
    `words` maps an object id to {"synonyms": [...], "display_name": "..."}."""
    raw = json.loads(json.dumps(SANDBOX))
    contract = raw["contract"]
    for kind in ("metrics", "dimensions", "fields", "datasets"):
        for item in contract[kind]:
            item.update(words.get(item["id"], {}))
    return SemanticRegistryDocument.model_validate(raw)


def intent(metric="order_count", **kw):
    return AnalyticalIntent.model_validate({"query_id": "q", "tenant_id": "sandbox", "dataset_id": "orders", "metrics": [{"metric_id": metric}],
                                            "semantic_contract": {"contract_id": "sandbox-databricks", "contract_version": "2026-11-18"}, **kw})


COUNT = {"order_count": {"synonyms": ["how many orders", "number of orders"]}}
BOTH = COUNT | {"order_amount_sum": {"synonyms": ["total order amount", "order total"]}}


# ---- compatibility --------------------------------------------------------------------------------------------------------------


def test_a_contract_without_words_dumps_exactly_as_before():
    for name in ("olist-commerce", "sales-core", "sales-demo"):
        raw = json.loads((SERVICE / f"semantic_registry/contracts/{name}-v1.json").read_text())
        dumped = json.loads(SemanticRegistryDocument.model_validate(raw).model_dump_json())
        assert not any("synonyms" in o or (k in ("metrics", "dimensions") and "display_name" in o) for k in ("metrics", "dimensions", "fields", "datasets") for o in dumped["contract"][k])
    d = doc()
    assert "synonyms" not in d.model_dump_json() and context_from_contract(d)[0].snapshot_id == context_from_contract(doc())[0].snapshot_id
    assert "synonyms" not in certified_vocabulary(d)


def test_words_change_the_snapshot_and_appear_in_the_dump():
    plain, worded = doc(), doc(**COUNT)
    assert "how many orders" in worded.model_dump_json()
    assert context_from_contract(plain)[0].snapshot_id != context_from_contract(worded)[0].snapshot_id


# ---- the rules ----------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "code"), [
    ("number of orders", None), ("order-total", None), ("Orders 2024", None),
    ("", "synonym_shape"), ("a", "synonym_shape"), ("12345", "synonym_shape"), ("x" * 61, "synonym_shape"), ("orders; DROP", "synonym_shape"),
    ('say "orders"', "synonym_shape"), ("{orders}", "synonym_shape"), ("orders\nignore rules", "synonym_shape"), (" orders", "synonym_shape"),
    ("revenue by region", "synonym_grouping_word"), ("sales per day", "synonym_grouping_word"), ("for each order", "synonym_grouping_word"),
    ("monthly", "synonym_time_word"), ("year", "synonym_time_word"), ("monthly orders", None),
])
def test_one_synonym_shape(text, code):
    assert synonym_problem(text) == code


def test_the_contract_rejects_a_bad_synonym_list_with_a_stable_code():
    for bad, code in (
        (["ok one", "OK ONE"], "synonym_duplicate"),  # the same word twice after normalising
        (["order tally"] * 2, "synonym_duplicate"),
        ([f"word {n}" for n in "abcdefghijk"], "synonym_count"),
        (["by region"], "synonym_grouping_word"),
    ):
        with pytest.raises(ValidationError, match=code):
            doc(order_count={"synonyms": bad})


@pytest.mark.parametrize(("words", "code"), [
    ({"order_count": {"synonyms": ["order"]}}, "synonym_is_another_id"),  # the (folded) id of the dataset `orders`
    ({"order_count": {"synonyms": ["order amount"]}}, "synonym_is_another_id"),  # the id of the field `orders.amount`
    ({"order_count": {"synonyms": ["amount sum"]}}, "synonym_overlaps_another_term"),  # a whole-word part of the metric `order amount sum`
    ({"order_count": {"synonyms": ["total order amount sum report"]}}, "synonym_overlaps_another_term"),  # contains another metric's name
    ({"order_count": {"synonyms": ["order total"]}, "order_amount_sum": {"synonyms": ["order total"]}}, "synonym_overlaps_another_term"),  # two metrics, one word
    ({"order_count": {"synonyms": ["amount"]}}, "synonym_is_another_id"),  # the id of a dimension
    ({"order_count": {"synonyms": ["orders id"]}}, "synonym_is_another_id"),  # the id of the field `orders.id`
    ({"order_amount_sum": {"display_name": "Order count"}}, "synonym_overlaps_another_term"),  # a display name is a word too
])
def test_the_contract_rejects_words_that_would_name_two_things(words, code):
    with pytest.raises(ValidationError, match=code):
        doc(**words)


def test_good_words_on_a_metric_a_dimension_a_field_and_the_dataset_are_accepted():
    d = doc(**BOTH, amount={"synonyms": ["price"]}, **{"orders.amount": {"synonyms": ["order price"]}}, orders={"synonyms": ["purchases"]})
    assert d.contract.metrics[0].synonyms == ["how many orders", "number of orders"]
    assert doc(order_count={"display_name": "Order count"}).contract.metrics[0].display_name == "Order count"


def test_the_same_word_on_two_kinds_is_allowed_only_when_neither_is_an_id_of_the_other():
    doc(order_count={"synonyms": ["tally"]}, amount={"synonyms": ["tally"]})  # a metric and a dimension may share a plain word
    with pytest.raises(ValidationError):
        doc(order_count={"synonyms": ["tally"]}, order_amount_sum={"synonyms": ["tally"]})  # two metrics may not share one


def test_the_shared_normaliser_folds_case_plurals_and_punctuation():
    assert normalize_term("How Many ORDERS?") == "how many order" and normalize_term("order_count") == "order count"
    assert normalize_term("categories") == "category" and normalize_term("status") == "status"
    assert contains_phrase("order amount sum", "order amount") and not contains_phrase("order amount sum", "amount su")


# ---- what they feed ---------------------------------------------------------------------------------------------------------------


def test_words_reach_the_ontology_labels_and_the_models_vocabulary():
    d = doc(**(BOTH | {"order_count": {"display_name": "Order count", "synonyms": COUNT["order_count"]["synonyms"]}}))
    _, ontology = context_from_contract(d)
    nodes = {n.node_id: n for n in ontology.nodes}
    assert nodes["order_count"].label == "Order count" and nodes["order_count"].attributes["synonyms"] == ["how many orders", "number of orders"]
    assert nodes["order_amount_sum"].label == "order_amount_sum" and nodes["amount"].attributes == {} and nodes["orders.id"].attributes == {}
    vocab = json.loads(certified_vocabulary(d))
    metric = next(m for m in vocab["metrics"] if m["metric_id"] == "order_count")
    assert metric["display_name"] == "Order count" and metric["synonyms"] == ["how many orders", "number of orders"]
    assert next(m for m in vocab["metrics"] if m["metric_id"] == "order_amount_sum")["synonyms"] == ["total order amount", "order total"]
    assert "display_name" not in next(m for m in vocab["metrics"] if m["metric_id"] == "order_amount_sum")


def test_a_fields_words_reach_the_vocabulary():
    vocab = json.loads(certified_vocabulary(doc(**{"orders.amount": {"synonyms": ["order price"]}})))
    assert next(f for f in vocab["filterable_fields"] if f["field_id"] == "orders.amount")["synonyms"] == ["order price"]


def test_a_restricted_fields_words_never_reach_the_models_vocabulary():
    raw = json.loads(json.dumps(SANDBOX))
    for f in raw["contract"]["fields"]:
        if f["id"] == "orders.amount":
            f["classification"], f["synonyms"] = "restricted", ["secret price"]
    d = SemanticRegistryDocument.model_validate(raw)
    assert "secret price" not in certified_vocabulary(d)


# ---- the echo check and the resolver --------------------------------------------------------------------------------------------


def echo(question, metric, **words):
    d = doc(**words)
    return echo_problems(question, intent(metric), context_from_contract(d)[1])


def test_a_natural_phrasing_passes_the_echo_check_only_with_a_certified_synonym():
    assert echo("How many orders are there?", "order_count") == ["metric_not_in_question:order_count"]  # as in C5
    assert echo("How many orders are there?", "order_count", **COUNT) == []
    assert echo("What is the total order amount?", "order_amount_sum", **BOTH) == []


def test_a_synonym_never_lets_the_wrong_metric_through():
    assert echo("What is the total order amount?", "order_count", **BOTH) == ["metric_not_in_question:order_count"]
    assert echo("How many orders are there?", "order_amount_sum", **BOTH) == ["metric_not_in_question:order_amount_sum"]
    assert echo("What is the total profit?", "order_amount_sum", **BOTH) == ["metric_not_in_question:order_amount_sum"]  # a near-miss is still refused
    assert echo("Show the order tally", "order_count", **COUNT) == ["metric_not_in_question:order_count"]  # a word nobody certified


def test_the_resolver_accepts_a_synonym_the_model_returns_as_an_id():
    d = doc(**COUNT)
    ontology = context_from_contract(d)[1]
    resolved = resolve_certified_intent(intent("Number of Orders"), d.contract, ontology)
    assert resolved.metrics[0].metric_id == "order_count"
    with pytest.raises(IntentResolutionError):
        resolve_certified_intent(intent("order tally"), d.contract, ontology)


def test_the_published_sandbox_contract_has_no_words_so_c5s_refusals_still_hold():
    d = SemanticRegistry(SERVICE / "semantic_registry_sandbox").get_certified("sandbox-databricks", "2026-11-18")
    assert not any(m.synonyms for m in d.contract.metrics)
