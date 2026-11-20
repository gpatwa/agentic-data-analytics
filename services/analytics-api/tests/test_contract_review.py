"""E4: a new contract version is shown as definition changes and as answer changes, with no model in the loop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.contract_review import (
    EvalCase,
    EvalFile,
    Expect,
    behaviour_diff,
    diff_contracts,
    render_summary,
    review,
    verdict_for,
)
from app.contract_review.diff import render_diff
from packages.platform_contracts.semantic import SemanticRegistryDocument

SANDBOX = json.loads((Path(__file__).resolve().parent.parent / "semantic_registry_sandbox/contracts/sandbox-databricks-v1.json").read_text())
BASE_WORDS = {"order_count": ["how many orders", "number of orders"], "order_amount_sum": ["total order amount"]}


def doc(words=None, version="2026-11-18", edit=None):
    raw = json.loads(json.dumps(SANDBOX))
    raw["contract"]["version"] = version
    for m in raw["contract"]["metrics"]:
        m["synonyms"] = (words or BASE_WORDS).get(m["id"], [])
    if edit:
        edit(raw["contract"])
    return SemanticRegistryDocument.model_validate(raw)


def intent(metric="order_count"):
    return {"query_id": "q", "dataset_id": "orders", "metrics": [{"metric_id": metric}]}


def case(cid, question, expect, metric="order_count"):
    return EvalCase(case_id=cid, question=question, recorded_intent=intent(metric), expect=expect)


CASES = (
    case("count", "How many orders are there?", Expect(outcome="answered", metric_id="order_count")),
    case("sum", "What is the total order amount?", Expect(outcome="answered", metric_id="order_amount_sum"), "order_amount_sum"),
    case("refunded", "How many refunded orders are there?", Expect(outcome="refused")),
)
EVAL = EvalFile(contract_id="sandbox-databricks", cases=CASES)


# ---- the contract diff ------------------------------------------------------------------------------------------------------------


def test_an_unchanged_contract_has_an_empty_diff_whatever_the_order():
    old = doc()
    reordered = json.loads(old.model_dump_json())
    reordered["contract"]["metrics"].reverse()
    assert diff_contracts(old.contract, SemanticRegistryDocument.model_validate(reordered).contract).empty


def test_added_removed_and_changed_items_and_synonym_edits_are_listed():
    new = doc({"order_count": ["how many orders", "refunded orders"], "order_amount_sum": ["total order amount"]}, version="2026-12-01",
              edit=lambda c: c["metrics"][1].update(aggregation="max"))
    d = diff_contracts(doc().contract, new.contract)
    by = {(c.kind, c.item_id): c for c in d.changes}
    assert by[("metrics", "order_count")].change == "changed" and by[("metrics", "order_count")].details == ("synonyms: +refunded orders, -number of orders",)
    assert "aggregation" in by[("metrics", "order_amount_sum")].details[0] and "sum" in by[("metrics", "order_amount_sum")].details[0]
    text = render_diff(d)
    assert "`2026-11-18` to `2026-12-01`" in text and d.old_digest != d.new_digest


def test_the_version_label_alone_is_not_a_change_in_definitions():
    d = diff_contracts(doc().contract, doc(version="2026-12-01").contract)
    assert d.empty and d.old_digest != d.new_digest  # the digest still binds the version, the diff reports definitions


def test_two_different_contracts_are_not_compared():
    other = doc(edit=lambda c: c.update(id="another"))
    with pytest.raises(ValueError):
        diff_contracts(doc().contract, other.contract)


# ---- the answer diff --------------------------------------------------------------------------------------------------------------


def test_each_case_is_judged_by_the_deterministic_pipeline_and_is_repeatable():
    v = [verdict_for(c, doc()) for c in CASES]
    assert [(x.outcome, x.code) for x in v] == [("answered", ""), ("answered", ""), ("refused", "intent_echo_mismatch")]
    assert v[0].metric_ids == ("order_count",) and len(v[0].sql_hash) == 64 and v == [verdict_for(c, doc()) for c in CASES]


def test_a_new_synonym_that_turns_a_refusal_into_a_wrong_answer_is_a_blocker():
    bad = doc({"order_count": ["how many orders", "refunded orders"], "order_amount_sum": ["total order amount"]}, version="2026-12-01")
    s = review(doc(), bad, old_eval=EVAL, new_eval=EVAL)
    moved = [c for c in s.changes if c.changed]
    assert [(c.case_id, c.old.outcome, c.new.outcome) for c in moved] == [("refunded", "refused", "answered")]
    assert s.blockers == ("regression:refunded",)
    page = render_summary(s)
    assert "refused:intent_echo_mismatch -> answered" in page and "regression:refunded" in page and "does not call a model" in page


def test_a_change_to_the_compiled_statement_is_shown_even_when_the_outcome_is_the_same():
    new = doc(version="2026-12-01", edit=lambda c: c["datasets"][0].update(physical_name="orders_v2"))
    changes = behaviour_diff(doc(), new, CASES)
    assert [c.case_id for c in changes if c.changed] == ["count", "sum"]
    assert all(c.old.outcome == c.new.outcome == "answered" and c.old.sql_hash != c.new.sql_hash for c in changes if c.changed)
    assert "the compiled statement changed" in render_summary(review(doc(), new, old_eval=EVAL, new_eval=EVAL))


def test_a_removed_term_that_makes_a_valid_question_refuse_is_a_blocker():
    new = doc({"order_count": ["number of orders"], "order_amount_sum": ["total order amount"]}, version="2026-12-01")
    s = review(doc(), new, old_eval=EVAL, new_eval=EVAL)
    assert s.blockers == ("regression:count",)


def test_a_new_version_needs_an_eval_file_when_the_previous_one_had_one():
    assert review(doc(), doc(version="v2"), old_eval=EVAL, new_eval=None).blockers == ("eval_file_missing_for_new_version",)
    first = review(doc(), doc(version="v2"), old_eval=None, new_eval=None)
    assert first.blockers == () and "no_eval_file" in first.notes[0]  # a first draft is not blocked


def test_an_eval_file_for_another_contract_is_a_blocker_and_checks_nothing():
    wrong = EvalFile(contract_id="other", cases=CASES)
    s = review(doc(), doc(version="v2"), old_eval=EVAL, new_eval=wrong)
    assert s.blockers == ("eval_file_is_for_another_contract",) and s.changes == ()


def test_an_intent_the_contract_cannot_read_is_a_refusal_not_a_crash():
    bad = EvalCase(case_id="bad", question="How many orders?", recorded_intent={"nonsense": 1}, expect=Expect(outcome="refused"))
    assert verdict_for(bad, doc()).outcome == "refused"
    unknown = EvalCase(case_id="u", question="How many orders?", recorded_intent=intent("no_such_metric"), expect=Expect(outcome="refused"))
    assert verdict_for(unknown, doc()).outcome == "refused"


def test_the_model_is_never_called_and_the_eval_schema_rejects_extras():
    with pytest.raises(ValueError):
        EvalCase.model_validate({**CASES[0].model_dump(), "model": "x"})
    import app.contract_review as pkg

    assert not [p for p in Path(pkg.__file__).parent.glob("*.py") if "model_factory" in p.read_text() or "complete_json" in p.read_text()]


def test_the_coverage_check_is_part_of_the_verdict_and_can_be_switched_off_for_comparison():
    dropped = case("d", "How many orders were refunded?", Expect(outcome="refused", code="intent_coverage_mismatch"))
    assert verdict_for(dropped, doc()).code == "intent_coverage_mismatch"  # the echo check passes on "how many orders"; coverage catches "refunded"
    assert verdict_for(dropped, doc(), coverage=False).outcome == "answered"


def test_a_case_that_failed_before_and_still_fails_is_a_note_not_a_blocker():
    odd = EvalFile(contract_id="sandbox-databricks", cases=(case("odd", "How many orders are there?", Expect(outcome="refused")),))
    s = review(doc(), doc(version="v2"), old_eval=odd, new_eval=odd)
    assert s.blockers == () and s.notes == ("expectation_not_met_before_or_after:odd",)
