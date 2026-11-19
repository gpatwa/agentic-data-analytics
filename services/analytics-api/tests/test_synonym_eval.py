"""S3: the phrasing and proposal evaluation. The numbers are pinned so a change to the echo check, the normaliser or the validator that moves
them is a deliberate one, the published report equals the generator, and the measures are shown to be able to fail."""

from __future__ import annotations

from pathlib import Path

from reference_stack import synonym_eval as ev

REPORT = Path(__file__).resolve().parents[3] / "docs/evaluation/semantic-synonyms-s3-report.md"


def test_the_pinned_measures():
    r = ev.run()
    assert r["without"]["answered_paraphrases"] == (2, 11) and r["with"]["answered_paraphrases"] == (7, 11)
    assert r["without"]["near_miss_accepted"] == (0, 8) and r["with"]["near_miss_accepted"] == (0, 8)  # synonyms never let a near-miss through
    assert r["with"]["dropped_constraint_accepted"] == (4, 4) and r["without"]["dropped_constraint_accepted"] == (0, 4)  # the gap synonyms expose
    assert r["with"]["omitted_metric_accepted"] == (2, 2) and r["without"]["omitted_metric_accepted"] == (1, 2)
    assert r["proposals"]["wanted_kept"] == (7, 7) and r["proposals"]["bad_kept"] == (0, 9)


def test_the_published_report_equals_the_generator():
    assert REPORT.read_text() == ev.render_markdown()
    assert "not approved" in REPORT.read_text() and "not a certification" in REPORT.read_text()


def test_the_published_contract_is_not_touched_by_the_evaluation():
    published = ev.CONTRACT.read_text()
    ev.run()
    assert ev.CONTRACT.read_text() == published and "synonyms" not in published


def test_the_near_miss_measure_can_fail(monkeypatch):
    monkeypatch.setattr(ev, "echo_problems", lambda *a, **k: [])
    assert ev.run()["with"]["near_miss_accepted"][0] == 8


def test_the_proposal_measure_can_fail(monkeypatch):
    import packages.platform_contracts.semantic as sem

    monkeypatch.setattr(sem, "_check_term_rules", lambda contract: None)
    monkeypatch.setattr(ev, "synonym_problem", lambda t: None, raising=False)
    import app.ontology_draft.validate as v

    monkeypatch.setattr(v, "synonym_problem", lambda t: None)
    assert ev.proposals()["bad_kept"][0] > 0
