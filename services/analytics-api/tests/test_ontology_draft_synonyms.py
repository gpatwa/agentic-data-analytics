"""S2: the drafter proposes display names and synonyms; the per-word rules and the cross-object rules keep or drop each one with a code,
the review note lists the words a person would be certifying, and nothing is certified. Scripted model, fake session: no network."""

from __future__ import annotations

import copy

import pytest
from tests.test_connect_certification_c1 import GOOD, certified, draft

from app.context.registry_context import context_from_contract
from app.ontology_draft.drafter import INSTRUCTIONS, PROMPT_VERSION
from app.ontology_draft.emit import write_draft
from app.runtime.intent_echo import echo_problems
from packages.platform_contracts.analytics_intent import AnalyticalIntent


def reply(metric_words=None, dim_words=None):
    out = copy.deepcopy(GOOD)
    out["metrics"][0].update(metric_words or {})
    out["dimensions"][0].update(dim_words or {})
    return out


def codes(result):
    return {d.item: d.code for d in result.dropped if d.kind in ("synonym", "display_name")}


def test_good_words_are_kept_on_the_draft_contract_and_the_candidate_ontology():
    result, _, _ = draft(reply=reply({"display_name": "Revenue", "synonyms": ["sales", "total sales"]}, {"synonyms": ["order state"]}))
    contract = result.document.contract
    assert contract.metrics[0].display_name == "Revenue" and contract.metrics[0].synonyms == ["sales", "total sales"]
    assert contract.dimensions[0].synonyms == ["order state"] and codes(result) == {}
    assert result.document.lifecycle == "draft" and all(m.certification == "candidate" for m in contract.metrics)
    nodes = {n.node_id: n for n in result.ontology.nodes}
    assert nodes["metric.revenue"].label == "Revenue" and nodes["metric.revenue"].attributes["synonyms"] == ["sales", "total sales"]
    assert all(n.lifecycle == "candidate" for n in result.ontology.nodes)


@pytest.mark.parametrize(("words", "code"), [
    (["paid"], "sample_value_in_text"),  # a kept sample value from the profile
    (["order 1234567"], "synonym_identifier_like"),
    (["ref a1b2c3d4e5"], "synonym_identifier_like"),
    (["x@y.com"], "synonym_shape"),
    (["sales; DROP TABLE"], "synonym_shape"),
    (["revenue by region"], "synonym_grouping_word"),
    (["monthly"], "synonym_time_word"),
    (["Good Name"], "synonym_duplicate"),
    (["status"], "synonym_is_another_id"),  # the id of the dimension
    (["orders amount"], "synonym_is_another_id"),  # the folded id of the field `orders.amount`
])
def test_a_bad_word_is_dropped_with_a_code_and_never_sinks_the_draft(words, code):
    result, _, _ = draft(reply=reply({"synonyms": ["good name"] + words}))
    contract = result.document.contract
    assert contract.metrics[0].synonyms == ["good name"], codes(result)
    assert code in codes(result).values() and "paid" not in repr(result.dropped) and "x@y.com" not in repr(result.dropped)
    assert contract.metrics and result.document is not None


def test_more_than_five_synonyms_keep_the_first_five():
    result, _, _ = draft(reply=reply({"synonyms": [f"name {c}" for c in "abcdefg"]}))
    assert len(result.document.contract.metrics[0].synonyms) == 5
    assert sorted(codes(result).values()) == ["too_many_synonyms"] * 2


def test_two_metrics_may_not_claim_one_word_but_a_metric_and_a_dimension_may():
    out = reply({"synonyms": ["order tally"]}, {"synonyms": ["order tally"]})
    out["metrics"].append({"id": "orders_total", "table": "orders", "aggregation": "count", "grain_key_columns": ["id"], "description": "Rows.", "synonyms": ["order tally"]})
    result, _, _ = draft(reply=out)
    contract = result.document.contract
    assert [m.synonyms for m in contract.metrics] == [["order tally"], []] and contract.dimensions[0].synonyms == ["order tally"]
    assert codes(result) == {"orders_total#0": "synonym_overlaps_another_term"}


def test_a_bad_display_name_is_dropped_but_the_metric_stays():
    result, _, _ = draft(reply=reply({"display_name": "Revenue by region"}))
    assert result.document.contract.metrics[0].display_name is None and codes(result) == {"revenue": "synonym_grouping_word"}


def test_a_malformed_word_list_drops_that_item_only():
    result, _, _ = draft(reply=reply({"synonyms": [f"n{i} word" for i in range(11)]}))  # over the schema's own bound
    assert any(d.code == "malformed_item" for d in result.dropped)
    assert result.document is None or "revenue" not in [m.id for m in result.document.contract.metrics]


def test_the_prompt_asks_for_words_from_names_and_comments_only_and_the_version_moved():
    assert PROMPT_VERSION == "m2-v2" and "synonyms" in INSTRUCTIONS and "taken from the table, column and comment names only" in INSTRUCTIONS
    _, model, _ = draft(reply=reply({"synonyms": ["sales"]}))
    assert "synonyms" in model.prompts[0] and "A person reviews every synonym" in model.prompts[0]


def test_the_review_note_lists_every_word_a_person_would_be_certifying(tmp_path):
    result, _, _ = draft(reply=reply({"display_name": "Revenue", "synonyms": ["sales", "paid"]}))
    where = write_draft(result, tmp_path, report=None, skipped_columns=(), source_note="test")
    note = (where / "REVIEW.md").read_text()
    assert "Terms you would be certifying" in note and 'display name "Revenue"' in note and '"sales"' in note
    assert "revenue#1 (sample_value_in_text)" in note or "revenue#1" in note
    plain, _, _ = draft()
    assert "none proposed or kept" in (write_draft(plain, tmp_path / "b", report=None, skipped_columns=(), source_note="t") / "REVIEW.md").read_text()


def test_a_certified_copy_of_a_worded_draft_lets_a_natural_phrasing_through_and_still_refuses_a_near_miss():
    """`certified` is a TEST FIXTURE standing in for the owner's certification; the drafter never certifies."""
    result, _, _ = draft(reply=reply({"synonyms": ["total sales"]}))
    doc = certified(result)
    ontology = context_from_contract(doc)[1]
    intent = AnalyticalIntent.model_validate({"query_id": "q", "tenant_id": "ta", "dataset_id": "orders", "metrics": [{"metric_id": "revenue"}],
                                              "semantic_contract": {"contract_id": "sales-dbx", "contract_version": "1.0.0"}})
    assert echo_problems("What were total sales last year?", intent, ontology) == []
    assert echo_problems("What was total profit?", intent, ontology) == ["metric_not_in_question:revenue"]
