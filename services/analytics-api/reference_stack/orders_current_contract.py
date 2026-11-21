"""The certified `orders_current` contract: counts and dimensions only, built from the reviewed draft, certified on the owner's recorded choice.

The owner chose "Counts now, USD amounts next" on 2026-11-21 (program file `orders_current_certify_counts`). Kept: the metric `order_count` and the eight
dimensions the review recommended. Left out on purpose, each for a stated reason in `docs/design/ORDERS_CURRENT_CONTRACT_REVIEW.md`: the amount sums (they add
three currencies and count cancelled orders), the tip sum (cash tips are not recorded), the delivery and prep averages (a -1 placeholder). Every
agent-proposed synonym is dropped: none was reviewed, and a contract with synonyms is only served with the coverage check on. Fields the kept items do not
use are dropped, so no restricted column (email, phone, address, tax, restaurant name) is in the contract at all."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from packages.platform_contracts.semantic import SemanticRegistryDocument

SERVICE = Path(__file__).resolve().parent.parent
DRAFT_GLOB = "semantic_registry_drafts/food-delivery-orders-current/*/contract.draft.json"
CERTIFIED = SERVICE / "semantic_registry_synthetic/contracts/food-delivery-orders-current-v1.json"
VERSION = "2026-11-21"
METRICS = ("order_count",)
DIMENSIONS = ("order_ts", "city", "country", "cuisine", "platform", "status", "payment_method", "currency")


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
    used = {m["grain"]["key_field_ids"][0] for m in contract["metrics"]} | {d["field_id"] for d in contract["dimensions"]}
    contract["fields"] = [f for f in contract["fields"] if f["id"] in used]
    contract["metadata"] = {"certification": {
        "status": "certified by the owner on 2026-11-21 (approval recorded in docs/execution/enterprise-analytics/agentic-data-stack-program.yaml, orders_current_certify_counts)",
        "scope": "counts and dimensions only; amount sums, tip sum and averages are NOT certified (see docs/design/ORDERS_CURRENT_CONTRACT_REVIEW.md)",
        "synonyms": "none: the agent-proposed words were not reviewed", "scope_digest": scope_digest(),
        "drafted_by": "a Claude agent standing in for the drafting model, validated by the product's validator; the product's own model was not called",
        "view_note": "the meaning of order_count lives in the view dev.compass_synthetic_food_delivery.orders_current; if the view changes, this meaning changes: version them together"}}
    return SemanticRegistryDocument.model_validate(raw)


def render() -> str:
    return json.dumps(build().model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    CERTIFIED.parent.mkdir(parents=True, exist_ok=True)
    CERTIFIED.write_text(render())
