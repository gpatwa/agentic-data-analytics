"""S6: ask the requester to confirm a reading instead of refusing on a wording mismatch. Off unless listed; humans only; bound to the exact reading
shown; a "no" is the refusal it would have been; evidence holds no words. End to end through the governed runtime on the fake reference stack."""

from __future__ import annotations

import pytest
from test_intent_coverage import PlainRevenue, ask  # noqa: F401
from test_intent_coverage import runtime as base_runtime

from app.runtime.ontology_node import describe_interpretation, interpretation_token


def runtime(tmp_path, *, coverage=frozenset({"tenant-a"}), confirm=frozenset({"tenant-a"})):
    client, stack, rt = base_runtime(tmp_path, coverage)
    object.__setattr__(rt, "confirm_tenants", confirm) if rt.__dataclass_params__.frozen else setattr(rt, "confirm_tenants", confirm)
    return client, stack, rt


def clarify(client, stack, run_id, selected, user="requester"):
    auth = {"Authorization": f"Bearer {stack.token(user)}"}
    return client.post(f"/api/v2/analytics/runs/{run_id}/clarify", json={"purpose": "analytics", "ambiguity_code": "interpretation", "selected_id": selected}, headers=auth)


def question(body):
    assert body["state"] == "waiting_clarification"
    (q,) = body["outcome"]["questions"]
    assert q["id"] == "interpretation"
    return q


def test_off_by_default_the_coverage_refusal_is_unchanged(tmp_path):
    client, stack, _ = runtime(tmp_path, confirm=frozenset())
    assert ask(client, stack, "Show revenue for refunded orders", "s6-key-0000001")["outcome"]["outcome"] == "refuse"


def test_a_mismatch_asks_instead_of_refusing_and_the_prompt_is_plain_words_with_no_data(tmp_path):
    client, stack, _ = runtime(tmp_path)
    q = question(ask(client, stack, "Show revenue for refunded orders", "s6-key-0000002"))
    assert q["prompt"].startswith("I read this as:") and "no other filter" in q["prompt"] and "Is that what you meant?" in q["prompt"]
    assert [c["label"] for c in q["choices"]] == ["Yes, that is what I meant", "No, that is not what I meant"] and q["choices"][1]["id"] == "none"


def test_yes_answers_the_confirmed_reading_and_no_is_the_refusal_it_would_have_been(tmp_path):
    client, stack, _ = runtime(tmp_path)
    body = ask(client, stack, "Show revenue for refunded orders", "s6-key-0000003")
    yes = question(body)["choices"][0]["id"]
    done = clarify(client, stack, body["run_id"], yes).json()
    assert done["outcome"]["outcome"] == "answer"  # the habitual-confirmer risk, measured in the report: the dropped "refunded" is shown as "no filter"
    other = ask(client, stack, "Show revenue for refunded orders", "s6-key-0000004")
    refused = clarify(client, stack, other["run_id"], "none").json()
    assert refused["outcome"]["outcome"] == "refuse" and refused["outcome"]["reason_code"] == "unsupported_request"


def test_a_confirmation_for_another_reading_is_rejected(tmp_path):
    client, stack, _ = runtime(tmp_path)
    body = ask(client, stack, "Show revenue for refunded orders", "s6-key-0000005")
    assert clarify(client, stack, body["run_id"], "I:0000000000000000").status_code == 422


def test_an_agent_can_never_confirm_its_own_interpretation(tmp_path):
    client, stack, _ = runtime(tmp_path)
    auth = {"Authorization": f"Bearer {stack.token('agent:bot')}", "Idempotency-Key": "s6-key-0000006"}
    body = client.post("/api/v2/analytics/analyze", json={"request_text": "Show revenue for refunded orders", "purpose": "analytics"}, headers=auth).json()
    yes = question(body)["choices"][0]["id"]
    r = clarify(client, stack, body["run_id"], yes, user="agent:bot")
    assert r.status_code == 403 and "human_required" in r.text


def test_a_covered_question_is_never_asked_and_another_tenant_is_untouched(tmp_path):
    client, stack, _ = runtime(tmp_path)
    assert ask(client, stack, "Show revenue", "s6-key-0000007")["outcome"]["outcome"] == "answer"
    other, stack2, _ = runtime(tmp_path / "o", confirm=frozenset({"some-other-tenant"}))
    assert ask(other, stack2, "Show revenue for refunded orders", "s6-key-0000008")["outcome"]["outcome"] == "refuse"


def test_the_stored_evidence_never_holds_the_words(tmp_path):
    from sqlalchemy import text

    client, stack, _ = runtime(tmp_path)
    body = ask(client, stack, "Show revenue for refunded orders", "s6-key-0000009")
    clarify(client, stack, body["run_id"], question(body)["choices"][0]["id"])
    with stack.control.engine.connect() as c:
        stored = " ".join(str(v) for row in c.execute(text("SELECT * FROM analytics_run_transitions")).mappings() for v in row.values())
        stored += " ".join(str(v) for row in c.execute(text("SELECT * FROM analytics_evidence_envelopes")).mappings() for v in row.values())
    assert "refunded" not in stored.lower()


def test_the_token_changes_with_any_part_of_the_reading():
    from test_intent_coverage import intent

    base = interpretation_token(intent())
    assert base == interpretation_token(intent()) and base.startswith("I:")
    assert base != interpretation_token(intent("order_amount_sum"))
    assert base != interpretation_token(intent(limit=5))
    assert base != interpretation_token(intent(filters=[{"field_id": "orders.amount", "operator": "equals", "values": ["210"]}]))


def test_the_description_names_filters_and_sorts_so_a_person_can_see_what_was_understood():
    import json
    from pathlib import Path

    from test_intent_coverage import SORT_DESC, intent

    from packages.platform_contracts.semantic import SemanticRegistryDocument

    raw = json.loads((Path(__file__).resolve().parent.parent / "semantic_registry_sandbox/contracts/sandbox-databricks-v1.json").read_text())
    contract = SemanticRegistryDocument.model_validate(raw).contract
    plain = describe_interpretation(intent(), contract)
    assert "no other filter, no time period and no sort" in plain and "a count of rows" in plain
    rich = describe_interpretation(intent(sort=SORT_DESC, filters=[{"field_id": "orders.amount", "operator": "equals", "values": ["210"]}]), contract)
    assert "filtered by amount equals 210" in rich and "sorted" in rich and "no other filter" not in rich
    assert pytest  # keep the import used


# ---- the scripted-user evaluation ------------------------------------------------------------------------------------------------------------


def test_the_s6_report_equals_the_generator_and_the_measures_are_pinned():
    from pathlib import Path

    from reference_stack import interpretation_eval as ie

    path = Path(__file__).resolve().parents[3] / "docs/evaluation/interpretation-confirm-s6-report.md"
    assert path.read_text() == ie.render_markdown()
    assert ie.run() == {"corpus_right": 23, "corpus_right_asked": 4, "corpus_wrong": 25, "corpus_wrong_asked": 25, "corpus_wrong_already_answered": 0,
                        "holdout_right": 10, "holdout_right_asked": 9, "holdout_wrong": 10, "holdout_wrong_asked": 10}
    assert "habitual" in path.read_text() and "policy decision" in path.read_text()


# ---- a contract with synonyms needs the coverage check -----------------------------------------------------------------------------------------


def synonym_runtime(tmp_path, coverage):
    from reference_stack.fakes import seed_lake
    from reference_stack.stack import build_stack
    from test_m7_pilot_wiring import DOCUMENT, REGISTRY, _client

    from app.compiler import DuckDBCompilerAdapter
    from app.compiler.service import CertifiedIntentCompiler
    from app.context.registry_context import CertifiedContracts, context_from_contract
    from app.execution import DuckDBGateway
    from app.runtime.evidence_store import EvidenceStore
    from app.runtime.pilot_wiring import GovernedRuntime
    from packages.platform_contracts.semantic import SemanticRegistryDocument

    raw = DOCUMENT.model_dump(mode="json")
    raw["contract"]["metrics"][0]["synonyms"] = ["sales figure"]
    document = SemanticRegistryDocument.model_validate(raw)
    stack = build_stack("duckdb", tmp_path / "control")
    parquet = seed_lake(tmp_path / "lake")
    rt = GovernedRuntime(control=stack.control, evidence=EvidenceStore(stack.control.engine), gateway=DuckDBGateway({"sales_orders": parquet}, allowed_root=tmp_path / "lake"),
                         compiler=CertifiedIntentCompiler(REGISTRY, DuckDBCompilerAdapter()), contracts=CertifiedContracts([document]),
                         contexts={"tenant-a": context_from_contract(document)}, model_factory=lambda rid: PlainRevenue(rid), coverage_tenants=coverage)
    return _client(rt, stack.verifier()), stack


def test_a_contract_with_synonyms_is_refused_for_a_tenant_without_the_coverage_check(tmp_path):
    client, stack = synonym_runtime(tmp_path / "a", frozenset())
    out = ask(client, stack, "Show revenue", "s6-key-0000010")
    assert out["state"] == "terminal" and out["outcome"]["outcome"] != "answer"  # fails closed: synonyms without coverage would turn a dropped constraint into a wrong answer


def test_the_same_contract_answers_once_the_tenant_has_the_coverage_check(tmp_path):
    client, stack = synonym_runtime(tmp_path / "b", frozenset({"tenant-a"}))
    assert ask(client, stack, "Show revenue", "s6-key-0000011")["outcome"]["outcome"] == "answer"
    assert ask(client, stack, "Show revenue for refunded orders", "s6-key-0000012")["outcome"]["outcome"] == "refuse"


def test_the_description_names_the_filter_a_metric_always_applies():
    from reference_stack import e2e_online_retail as retail

    from packages.platform_contracts.analytics_intent import AnalyticalIntent

    doc = retail.document()
    intent = AnalyticalIntent.model_validate({"query_id": "q", "tenant_id": "tenant-a", "dataset_id": "lines", "metrics": [{"metric_id": "sales_amount"}],
                                              "semantic_contract": {"contract_id": "online-retail-ii", "contract_version": "fixture-1"}})
    text = describe_interpretation(intent, doc.contract)
    assert "always limited to: is cancellation equals False" in text and "no other filter" in text  # the person is told cancellations are left out
