"""The orders_current contract draft: a draft, keyed on a profiled key, named as agent-drafted, and never in a registry folder."""

from __future__ import annotations

import json
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent


def draft():
    files = list((SERVICE / "semantic_registry_drafts/food-delivery-orders-current").glob("*/contract.draft.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text())


def test_it_is_an_uncertified_draft_named_as_agent_drafted():
    doc = draft()
    assert doc["lifecycle"] == "draft" and "stand-in" in json.dumps(doc["contract"]["metadata"])


def test_order_count_is_keyed_on_order_id_the_profiled_key_of_the_view():
    contract = draft()["contract"]
    count = next(m for m in contract["metrics"] if m["id"] == "order_count")
    assert count["grain"]["key_field_ids"] == ["orders_current.order_id"] and count["aggregation"] == "count"
    review = next((SERVICE / "semantic_registry_drafts/food-delivery-orders-current").glob("*/REVIEW.md")).read_text()
    assert "key (order_id), evidence **profiled_full**" in review  # the evidence level is stated for the reviewer


def test_the_review_says_the_amount_sums_are_not_certified():
    review = (SERVICE.parent.parent / "docs/design/ORDERS_CURRENT_CONTRACT_REVIEW.md").read_text()
    assert "are NOT certified" in review and "Do not certify yet" in review and "41% too high" in review


def test_no_registry_folder_holds_the_draft():
    for root in ("semantic_registry_sandbox/contracts", "semantic_registry_sandbox_v2/contracts"):
        assert not list((SERVICE / root).glob("*orders*current*"))


def test_the_usd_amount_draft_is_an_uncertified_stand_in_draft_and_the_review_pins_the_matching_value():
    files = list((SERVICE / "semantic_registry_drafts/food-delivery-orders-usd-gross").glob("*/contract.draft.json"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text())
    assert doc["lifecycle"] == "draft" and "stand-in" in json.dumps(doc["contract"]["metadata"])
    gross = next(m for m in doc["contract"]["metrics"] if m["id"] == "gross_order_value")
    assert gross["aggregation"] == "sum" and gross["measure_field_id"] == "orders_usd_gross.total_amount"
    review = (SERVICE.parent.parent / "docs/design/ORDERS_CURRENT_CONTRACT_REVIEW.md").read_text()
    assert "545,012.03" in review and "Matches the key exactly" in review and "Do not certify under that name" in review
