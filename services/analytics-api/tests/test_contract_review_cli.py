"""E4 wiring: the command a certifier runs before signing a new version of a contract."""

from __future__ import annotations

import json

from reference_stack import synonym_proposal as sp

from app.contract_review.__main__ import main


def test_the_real_proposal_against_the_certified_v1_shows_only_synonym_changes(capsys):
    assert main(["--old", str(sp.V1), "--new", str(sp.PROPOSED)]) == 0
    out = capsys.readouterr().out
    assert "Review of a new contract version" in out and "Blockers" in out and "- none" in out
    assert "does not call a model" in out and "certification is a person's decision" in out


def test_a_prior_eval_file_without_one_for_the_new_version_blocks(tmp_path, capsys):
    ev = tmp_path / "e.json"
    ev.write_text(json.dumps({"contract_id": "sandbox-databricks", "cases": [{
        "case_id": "c1", "question": "How many orders are there?", "recorded_intent": {
            "query_id": "q", "tenant_id": "sandbox", "dataset_id": "orders", "metrics": [{"metric_id": "order_count"}],
            "semantic_contract": {"contract_id": "sandbox-databricks", "contract_version": "2026-11-18"}},
        "expect": {"outcome": "refused", "code": "unsupported_request"}}]}))
    assert main(["--old", str(sp.V1), "--new", str(sp.PROPOSED), "--old-eval", str(ev)]) == 1
    assert "eval_file_missing_for_new_version" in capsys.readouterr().out


def test_a_bad_file_is_refused_by_type_only(tmp_path, capsys):
    bad = tmp_path / "b.json"
    bad.write_text('{"secret": "do-not-echo"}')
    assert main(["--old", str(bad), "--new", str(sp.PROPOSED)]) == 2
    err = capsys.readouterr().err
    assert "refused:" in err and "do-not-echo" not in err
