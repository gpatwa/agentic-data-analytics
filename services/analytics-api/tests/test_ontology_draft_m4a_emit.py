"""Auto-ontology drafter, milestone 4a: the draft emitter and the runner's guards. No database, no network."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.ontology_draft import PROMPT_VERSION, DraftContext, draft_ontology, draft_run
from app.ontology_draft.emit import DEFAULT_DRAFTS_DIR, write_draft
from app.ontology_draft.eval_corpus import demo_sales
from app.ontology_draft.evaluation import run_case_full
from app.semantic_registry import SemanticRegistry
from app.semantic_registry.registry import SemanticContractNotFoundError

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
REPLY = {
    "metrics": [
        {"id": "revenue", "table": "demo_sales_orders", "aggregation": "sum", "measure_column": "amount",
         "grain_key_columns": ["id"], "description": "Total order value."},
    ],
    "dimensions": [
        {"id": "status", "table": "demo_sales_orders", "column": "status", "dimension_type": "categorical", "description": "State."},
    ],
}


def ctx():
    return DraftContext(tenant_id="t", domain="d", contract_id="emit-draft", owner_id="team.x", owner_name="X",
                        model_id="scripted", prompt_version=PROMPT_VERSION)


class Scripted:
    def __init__(self, reply):
        self.reply = reply

    def complete_json(self, *, prompt, schema, max_tokens):
        return self.reply


def drafted():
    case = demo_sales()
    report, result = run_case_full(case, Scripted(REPLY), ctx(), now=NOW)
    return case, report, result


def test_a_draft_is_written_as_three_plain_files_with_a_review_note(tmp_path):
    _, report, result = drafted()
    where = write_draft(result, tmp_path, report=report, source_note="test source")
    assert sorted(p.name for p in where.iterdir()) == ["REVIEW.md", "contract.draft.json", "ontology.candidate.json"]
    assert json.loads((where / "contract.draft.json").read_text())["lifecycle"] == "draft"
    note = (where / "REVIEW.md").read_text()
    assert "DRAFT. Not certified" in note and "test source" in note and "validator escapes: 0" in note


def test_the_review_note_holds_no_sample_value_or_secret(tmp_path):
    case, report, result = drafted()
    note = (write_draft(result, tmp_path, report=report) / "REVIEW.md").read_text()
    samples = {s for t in case.profile.tables for c in t.columns for s in c.samples}
    assert samples and not any(sample in note for sample in samples)
    assert "jane.doe@example.com" not in note


def test_nothing_is_written_for_an_empty_draft(tmp_path):
    case = demo_sales()
    empty = draft_ontology(case.facts, Scripted({}), ctx(), now=NOW)
    with pytest.raises(ValueError):
        write_draft(empty, tmp_path)
    assert not list(tmp_path.iterdir())


def test_the_drafts_directory_is_outside_the_registry_root():
    root = Path(Settings().ANALYTICS_SEMANTIC_REGISTRY_PATH).resolve()
    assert root not in DEFAULT_DRAFTS_DIR.resolve().parents and DEFAULT_DRAFTS_DIR.resolve() != root


def test_the_registry_never_resolves_an_emitted_draft(tmp_path):
    _, report, result = drafted()
    write_draft(result, tmp_path / "drafts", report=report)
    registry = SemanticRegistry(Path(Settings().ANALYTICS_SEMANTIC_REGISTRY_PATH))
    contract = result.document.contract
    with pytest.raises(SemanticContractNotFoundError):
        registry.get_certified(contract.id, contract.version)
    assert all(entry.contract_id != contract.id for entry in registry.list_entries())


def test_runner_refuses_without_its_environment(monkeypatch, capsys):
    for name in draft_run.REQUIRED:
        monkeypatch.delenv(name, raising=False)
    assert draft_run.main(["--tables", "t"]) == 2
    err = capsys.readouterr().err
    assert all(name in err for name in draft_run.REQUIRED)


def test_runner_refuses_a_remote_database_without_the_flag(monkeypatch, capsys):
    monkeypatch.setenv("ONTOLOGY_PG_READONLY_URL", "postgresql+psycopg2://ro:secret-pw@db.example.com:5432/w")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.invalid")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "d")
    assert draft_run.main(["--tables", "t"]) == 2
    err = capsys.readouterr().err
    assert "milestone 4b" in err and "secret-pw" not in err and "db.example.com" not in err
