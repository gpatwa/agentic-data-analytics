"""The certified `orders_usd_gross` contract: the USD amount metrics, built from the reviewed draft, certified on the owner's recorded choice.

The owner chose (program file `orders_usd_gross_certify_amounts`, 2026-11-21): `gross_order_value`, a USD `order_count` and the fee and discount sums, and
NOT `tip_total` (cash tips are not recorded, so its name overstates what it holds). Every agent-proposed synonym is dropped: none was reviewed. Fields the kept
items do not use (tip amount, currency) are dropped. The currency rule lives in the view, not in the contract: the view and the contract are versioned together."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from packages.platform_contracts.semantic import SemanticRegistryDocument

SERVICE = Path(__file__).resolve().parent.parent
DRAFT_GLOB = "semantic_registry_drafts/food-delivery-orders-usd-gross/*/contract.draft.json"
CERTIFIED = SERVICE / "semantic_registry_synthetic_usd/contracts/food-delivery-orders-usd-gross-v1.json"
VERSION = "2026-11-21"
METRICS = ("order_count", "gross_order_value", "gross_subtotal", "delivery_fee_total", "service_fee_total", "discount_total")
LEFT_OUT = ("tip_total",)
DIMENSIONS = ("order_time", "city", "country", "cuisine", "platform", "order_status", "payment_method")


def _draft() -> dict:
    (path,) = list(SERVICE.glob(DRAFT_GLOB))
    return json.loads(path.read_text())


def scope_digest() -> str:
    return hashlib.sha256(json.dumps({"m": METRICS, "d": DIMENSIONS}, separators=(",", ":")).encode()).hexdigest()


def build() -> SemanticRegistryDocument:
    raw = _draft()
    contract = raw["contract"]
    raw["lifecycle"] = "certified"
    contract["version"] = VERSION
    contract["metrics"] = [{**m, "certification": "certified", "synonyms": []} for m in contract["metrics"] if m["id"] in METRICS]
    contract["dimensions"] = [{**d, "synonyms": []} for d in contract["dimensions"] if d["id"] in DIMENSIONS]
    contract["datasets"] = [{**d, "synonyms": []} for d in contract["datasets"]]
    used = {k for m in contract["metrics"] for k in m["grain"]["key_field_ids"]} | {m["measure_field_id"] for m in contract["metrics"] if m.get("measure_field_id")}
    used |= {d["field_id"] for d in contract["dimensions"]}
    contract["fields"] = [f for f in contract["fields"] if f["id"] in used]
    contract["metadata"] = {"certification": {
        "status": "certified by the owner on 2026-11-21 (approval recorded in docs/execution/enterprise-analytics/agentic-data-stack-program.yaml, orders_usd_gross_certify_amounts)",
        "scope": "USD amount metrics (gross; refunds not netted) and a USD order count; tip_total is NOT certified; no synonyms",
        "scope_digest": scope_digest(),
        "drafted_by": "a Claude agent standing in for the drafting model, validated by the product's validator; the product's own model was not called for this draft",
        "view_note": "the currency and cancelled-order rules live in the view dev.compass_synthetic_food_delivery.orders_usd_gross (on orders_current); if the view changes, this meaning changes: version them together"}}
    return SemanticRegistryDocument.model_validate(raw)


def render() -> str:
    return json.dumps(build().model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    CERTIFIED.parent.mkdir(parents=True, exist_ok=True)
    CERTIFIED.write_text(render())
