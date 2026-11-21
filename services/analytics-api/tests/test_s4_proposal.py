"""S4: the sandbox synonyms, certified as version 2026-11-20 on the owner's recorded approval, beside the untouched v1. Measured once, never tuned to."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from reference_stack import coverage_eval as ev
from reference_stack import synonym_proposal as sp

SERVICE = Path(__file__).resolve().parent.parent


def test_v2_is_certified_with_a_recorded_approval_and_the_file_equals_the_builder():
    doc = sp.build()
    assert doc.lifecycle == "certified" and doc.contract.version == "2026-11-20"
    assert sp.PROPOSED.read_text() == sp.render()
    cert = doc.contract.metadata["certification"]
    assert "agentic-data-stack-program.yaml" in cert["status"] and cert["words_digest"] == sp.words_digest() and "synonyms only" in cert["change"]


def test_v2_changes_synonyms_and_nothing_else_about_the_definitions():
    old, new = json.loads(sp.V1.read_text())["contract"], json.loads(sp.PROPOSED.read_text())["contract"]
    for kind in ("metrics", "dimensions", "datasets"):
        strip = lambda items: [{k: v for k, v in i.items() if k != "synonyms"} for i in items]  # noqa: E731
        assert strip(old[kind]) == strip(new[kind])
    for kind in ("fields", "entities", "joins", "filters", "policies"):
        assert old[kind] == new[kind]


def test_the_certified_v1_is_untouched_and_has_no_synonyms():
    raw = json.loads(sp.V1.read_text())
    assert raw["lifecycle"] == "certified" and "synonyms" not in json.dumps(raw)


def test_left_out_words_are_not_in_the_proposal_and_each_has_a_reason():
    used = {w for ws in (*sp.METRIC_SYNONYMS.values(), *sp.DIMENSION_SYNONYMS.values(), *sp.DATASET_SYNONYMS.values()) for w in ws}
    assert not used & set(sp.LEFT_OUT) and all(reason for reason in sp.LEFT_OUT.values())


def test_v2_is_not_served_without_the_coverage_check():
    from app.runtime.ontology_node import has_synonyms

    assert has_synonyms(sp.build().contract) and not has_synonyms(SemanticRegistryDocumentV1().contract)


def SemanticRegistryDocumentV1():  # noqa: N802
    from packages.platform_contracts.semantic import SemanticRegistryDocument

    return SemanticRegistryDocument.model_validate(json.loads(sp.V1.read_text()))


def test_no_risky_business_word_is_proposed():
    words = " ".join(w for ws in sp.METRIC_SYNONYMS.values() for w in ws)
    assert not {"revenue", "sales", "spend", "profit", "gmv"} & set(words.split())


def test_the_measured_effect_is_pinned_and_small(monkeypatch):
    monkeypatch.setattr(ev, "_document", sp.build)
    assert ev.holdout() == {"n": 20, "wrong_answered": 0, "flagged_valid": 1, "refused_valid": 8}  # fixture set: 9; the proposal recovers one
    r = ev.run()
    assert r["dropped"][1] == (0, 12) and r["omitted"][1] == (0, 5) and r["near_miss"][1] == (0, 8)  # the holes stay closed


def test_the_synthetic_draft_is_uncertified_and_keyed_on_declared_keys():
    drafts = list((SERVICE / "semantic_registry_drafts/food-delivery-synthetic").glob("*/contract.draft.json"))
    assert len(drafts) == 1
    doc = json.loads(drafts[0].read_text())
    assert doc["lifecycle"] == "draft"
    keys = {m["id"]: m["grain"]["key_field_ids"] for m in doc["contract"]["metrics"]}
    assert keys["order_count"] == ["orders.row_id"] and keys["quantity_sum"] == ["order_items.order_id", "order_items.line_no"]
    assert "stand-in" in json.dumps(doc["contract"]["metadata"])  # the model that drafted it is named as a stand-in


def test_each_registry_root_holds_exactly_one_certified_contract_so_v1_stays_active_until_a_deployment_points_at_v2():
    for folder, name in (("semantic_registry_sandbox/contracts", "sandbox-databricks-v1.json"), ("semantic_registry_sandbox_v2/contracts", "sandbox-databricks-v2.json")):
        assert [p.name for p in (SERVICE / folder).glob("*.json")] == [name]  # the Connect resolver refuses a tenant folder with two
    assert not (SERVICE / "semantic_registry_sandbox/proposed").exists()


@pytest.mark.parametrize("name", ["order volume", "order quantity", "order size"])
def test_ambiguous_words_stay_out(name):
    assert name in sp.LEFT_OUT
