"""E6 wiring: the offline command that starts a draft from existing metric views. No connection, no model."""

from __future__ import annotations

import json

from test_existing_assets import YAML, facts

from app.ontology_draft.import_assets import main


def files(tmp_path, key=("id",)):
    (tmp_path / "facts.json").write_text(facts(key=key).model_dump_json())
    (tmp_path / "mv.yaml").write_text(YAML)
    return ["--facts", str(tmp_path / "facts.json"), "--contract-id", "orders-imported", "--tenant", "t", "--domain", "sales",
            "--metric-view", f"orders_mv={tmp_path / 'mv.yaml'}", "--out", str(tmp_path / "drafts")]


def test_it_writes_a_draft_and_says_what_it_found_and_what_it_left_out(tmp_path, capsys):
    assert main(files(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "What we found in your warehouse" in out and "nothing here is certified" in out and "not imported" in out.lower()
    written = list((tmp_path / "drafts").glob("orders-imported/*/contract.draft.json"))
    assert len(written) == 1 and json.loads(written[0].read_text())["lifecycle"] == "draft"


def test_a_restricting_tag_removes_the_column_from_the_draft(tmp_path, capsys):
    args = files(tmp_path)
    (tmp_path / "tags.json").write_text(json.dumps([["orders", "status", ["pii"]]]))
    assert main([*args, "--tags", str(tmp_path / "tags.json")]) == 0
    doc = json.loads(next((tmp_path / "drafts").glob("orders-imported/*/contract.draft.json")).read_text())
    assert "order_status" not in {d["id"] for d in doc["contract"]["dimensions"]}
    capsys.readouterr()


def test_without_a_key_nothing_is_kept_and_no_draft_is_written(tmp_path, capsys):
    assert main(files(tmp_path, key=())) == 1
    assert not (tmp_path / "drafts").exists() and "no draft was written" in capsys.readouterr().err


def test_bad_input_is_refused_by_type_only(tmp_path, capsys):
    (tmp_path / "facts.json").write_text('{"tables": "secret-value"}')
    (tmp_path / "mv.yaml").write_text(YAML)
    assert main(["--facts", str(tmp_path / "facts.json"), "--contract-id", "c", "--tenant", "t", "--domain", "d", "--metric-view", f"v={tmp_path / 'mv.yaml'}"]) == 2
    assert "secret-value" not in capsys.readouterr().err
    assert main(["--facts", str(tmp_path / "facts.json"), "--contract-id", "c", "--tenant", "t", "--domain", "d"]) == 2
