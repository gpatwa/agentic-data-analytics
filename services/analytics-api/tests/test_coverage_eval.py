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
    assert r["holdout"]["n"] == 0  # the owner has not filled in the hold-out


def test_the_published_report_equals_the_generator_and_says_what_it_is():
    text = REPORT.read_text()
    assert text == ev.render_markdown() and "not changed after seeing their results" in text and "K3 waits for it" in text and "not approved" in text


def test_the_owners_holdout_file_is_a_template_and_the_published_contract_is_untouched():
    template = json.loads(ev.HOLDOUT.read_text())
    assert template["cases"] == [] and "YOURS" in template["_instructions"]
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
