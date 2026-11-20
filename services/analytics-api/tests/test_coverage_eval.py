"""K2: the coverage corpus evaluation. Pinned numbers (a change that moves them is deliberate), the published report equals the generator,
the owner's hold-out is read when filled in, and the measures are shown able to fail."""

from __future__ import annotations

import json
from pathlib import Path

from reference_stack import coverage_eval as ev

REPORT = Path(__file__).resolve().parents[3] / "docs/evaluation/intent-coverage-k2-report.md"


def test_the_pinned_measures():
    r = ev.run()
    assert r["valid"] == ((20, 20), (16, 20)) and r["faithful"] == ((3, 3), (3, 3))
    assert r["dropped"] == ((11, 12), (0, 12)) and r["omitted"] == ((5, 5), (0, 5)) and r["near_miss"] == ((0, 8), (0, 8))
    # the owner's hold-out (20 questions, adopted 2026-11-20): pinned as measured, never tuned to
    assert r["holdout"] == {"n": 20, "wrong_answered": 0, "flagged_valid": 1, "refused_valid": 9}


def test_the_published_report_equals_the_generator_and_says_what_it_is():
    text = REPORT.read_text()
    assert text == ev.render_markdown() and "not changed after seeing their results" in text  and "not approved" in text and "20 questions: answered although the owner expected a refusal 0" in text


def test_the_owners_holdout_file_is_filled_in_with_a_stated_origin_and_the_published_contract_is_untouched():
    filled = json.loads(ev.HOLDOUT.read_text())
    cases = filled["cases"]
    assert len(cases) == 20 and sum(c["expect"] == "answer" for c in cases) == 10 and "YOURS" in filled["_instructions"]
    assert {c["metric"] for c in cases} <= {"order_count", "order_amount_sum"} and "not written by a human colleague" in filled["_provenance"]
    assert "synonyms" not in ev.CONTRACT.read_text()


def test_a_filled_holdout_is_scored(tmp_path, monkeypatch):
    holdout = tmp_path / "h.json"
    holdout.write_text(json.dumps({"cases": [
        {"question": "How many orders are there?", "metric": "order_count", "expect": "answer"},
        {"question": "How many orders were refunded?", "metric": "order_count", "expect": "refuse"},
        {"question": "Hi, what is the order count?", "metric": "order_count", "expect": "answer"},
    ]}))
    monkeypatch.setattr(ev, "HOLDOUT", holdout)
    h = ev.holdout()
    assert h == {"n": 3, "wrong_answered": 0, "flagged_valid": 1, "refused_valid": 1}
    assert "3 questions" in ev.render_markdown()


def test_the_measures_can_fail(monkeypatch):
    monkeypatch.setattr(ev, "unaccounted_positions", lambda *a, **k: [])
    r = ev.run()
    assert r["dropped"][1][0] == 11 and r["omitted"][1][0] == 5  # with the check disabled the holes reopen


def test_the_neutral_list_was_not_tuned_to_these_cases():
    from app.runtime.intent_coverage import NEUTRAL_WORDS

    assert not NEUTRAL_WORDS & {"right", "now", "quickly", "hi", "hello", "received", "placed"}
