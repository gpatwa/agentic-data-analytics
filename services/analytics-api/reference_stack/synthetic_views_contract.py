"""The certified combined contract for the synthetic tenant: the two contracts the owner already certified, merged so a tenant serves one (program file `synthetic_views_combined_contract`).

No definition is new. The counts contract (`food-delivery-orders-current`, over `orders_current`) and the USD amount contract (`food-delivery-orders-usd-gross`, over
`orders_usd_gross`) are read as certified; where an id would clash inside one contract, the USD dataset's item is prefixed `usd_`. `tip_total` stays uncertified and no synonym is certified.
The currency and cancelled-order rules live in the views: the views and this contract are versioned together."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from packages.platform_contracts.semantic import SemanticRegistryDocument
from reference_stack import orders_current_contract as counts
from reference_stack import orders_usd_gross_contract as usd

SERVICE = Path(__file__).resolve().parent.parent
CERTIFIED = SERVICE / "semantic_registry_synthetic_combined/contracts/food-delivery-synthetic-views-v1.json"
VERSION = "2026-11-21"
CONTRACT_ID = "food-delivery-synthetic-views"
RENAMED_METRICS = {"order_count": "usd_order_count"}
USD_DIMENSION_PREFIX = "usd_"


def _digest(doc: SemanticRegistryDocument) -> str:
    return hashlib.sha256(json.dumps(doc.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


NO_DISPLAY_NAME = {"order_count", "usd_order_count"}


def _usd(item: dict, new_id: str) -> dict:
    """The USD dataset's item under a unique id."""
    return {**item, "id": new_id}


def _plain(item: dict) -> dict:
    """Display names are terms, and the rule is that no term may contain another as a whole phrase ("City" sits inside "usd city"). Two datasets that both
    offer a city, a country and a count cannot all carry display names, so the combined contract carries none for the dimensions and the two counts: the ids say what
    they are. Names only; no definition changes."""
    return {**item, "display_name": None}


def build() -> SemanticRegistryDocument:
    a, b = counts.build(), usd.build()
    raw = json.loads(a.model_dump_json())
    contract, other = raw["contract"], json.loads(b.model_dump_json())["contract"]
    contract["id"], contract["version"] = CONTRACT_ID, VERSION
    contract["datasets"] += other["datasets"]
    contract["fields"] += other["fields"]
    contract["metrics"] += [_usd(m, RENAMED_METRICS.get(m["id"], m["id"])) for m in other["metrics"]]
    contract["dimensions"] += [_usd(d, USD_DIMENSION_PREFIX + d["id"]) for d in other["dimensions"]]
    contract["metrics"] = [_plain(m) if m["id"] in NO_DISPLAY_NAME else m for m in contract["metrics"]]
    contract["dimensions"] = [_plain(d) for d in contract["dimensions"]]
    contract["metadata"] = {"certification": {
        "status": "certified by the owner on 2026-11-21 (approval recorded in docs/execution/enterprise-analytics/agentic-data-stack-program.yaml, synthetic_views_combined_contract)",
        "scope": "the union of two contracts the owner certified earlier, with no new definition; tip_total is NOT certified; no synonyms",
        "sources": {"food-delivery-orders-current": {"version": counts.VERSION, "digest": _digest(a)}, "food-delivery-orders-usd-gross": {"version": usd.VERSION, "digest": _digest(b)}},
        "renamed": "ids and display names only, to stay valid in one contract: usd_order_count and the usd_ dimensions; the dimensions and the two counts carry no display name (the term rule forbids a name that contains another)",
        "view_note": "the rules live in the views orders_current and orders_usd_gross; if a view changes, the meaning changes: version them together"}}
    return SemanticRegistryDocument.model_validate(raw)


def render() -> str:
    return json.dumps(build().model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    CERTIFIED.parent.mkdir(parents=True, exist_ok=True)
    CERTIFIED.write_text(render())
