"""S4 preparation: the proposed sandbox synonyms are a validated DRAFT beside the certified v1, measured once, and certified by nobody."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from reference_stack import coverage_eval as ev
from reference_stack import synonym_proposal as sp

from packages.platform_contracts.semantic import SemanticRegistryDocument

SERVICE = Path(__file__).resolve().parent.parent


def test_the_proposal_is_a_valid_draft_and_the_file_equals_the_builder():
    doc = sp.build()
    assert doc.lifecycle == "draft" and doc.contract.version != json.loads(sp.V1.read_text())["contract"]["version"]
    assert sp.PROPOSED.read_text() == sp.render()
    assert sp.PROPOSED.parent.name == "proposed" and sp.PROPOSED.parent != sp.V1.parent  # never inside the certified contracts folder


def test_the_certified_v1_is_untouched_and_has_no_synonyms():
    raw = json.loads(sp.V1.read_text())
    assert raw["lifecycle"] == "certified" and "synonyms" not in json.dumps(raw)


def test_left_out_words_are_not_in_the_proposal_and_each_has_a_reason():
    used = {w for ws in (*sp.METRIC_SYNONYMS.values(), *sp.DIMENSION_SYNONYMS.values(), *sp.DATASET_SYNONYMS.values()) for w in ws}
    assert not used & set(sp.LEFT_OUT) and all(reason for reason in sp.LEFT_OUT.values())


def test_no_risky_business_word_is_proposed():
    words = " ".join(w for ws in sp.METRIC_SYNONYMS.values() for w in ws)
    assert not {"revenue", "sales", "spend", "profit", "gmv"} & set(words.split())


def test_the_measured_effect_is_pinned_and_small(monkeypatch):
    def certified_copy() -> SemanticRegistryDocument:  # an in-memory copy for the measurement only; nothing is saved or certified
        raw = sp.build().model_dump(mode="json")
        raw["lifecycle"] = "certified"
        return SemanticRegistryDocument.model_validate(raw)

    monkeypatch.setattr(ev, "_document", certified_copy)
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


def test_the_certified_contracts_folder_holds_only_the_certified_v1():
    assert [p.name for p in (SERVICE / "semantic_registry_sandbox/contracts").glob("*.json")] == ["sandbox-databricks-v1.json"]


@pytest.mark.parametrize("name", ["order volume", "order quantity", "order size"])
def test_ambiguous_words_stay_out(name):
    assert name in sp.LEFT_OUT
