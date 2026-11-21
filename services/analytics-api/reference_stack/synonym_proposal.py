"""S4: the sandbox contract's synonym set, version 2026-11-20, CERTIFIED on the owner's recorded approval (program file `s6_s4_k3_decisions`).

The words were proposed on 2026-11-20 by a fresh agent that saw only the contract's metric and dimension descriptions and the validator's rules: not
the corpus, the hold-out or the neutral list. They were then filtered by the preparer for words that could mean something the contract does not define
(see `LEFT_OUT`). The owner approved certifying it on 2026-11-20 ("go ahead with 1-4"); the words are listed in the approval record and the digest below pins them. v1 stays as it was. A contract with synonyms is only served for a tenant with the coverage check on (`synonyms_need_coverage`), so deploying v2 without K3 fails closed.
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.platform_contracts.semantic import SemanticRegistryDocument

SERVICE = Path(__file__).resolve().parent.parent
V1 = SERVICE / "semantic_registry_sandbox/contracts/sandbox-databricks-v1.json"
PROPOSED = SERVICE / "semantic_registry_sandbox_v2/contracts/sandbox-databricks-v2.json"  # certified on 2026-11-20; a registry root of its own (the registry reads every folder under a root, and a tenant holds exactly one certified contract), so v1 stays the active one until a deployment points at this folder

METRIC_SYNONYMS = {
    "order_count": ["number of orders", "how many orders", "orders placed", "count of orders", "total orders placed", "order tally", "orders received",
                    "number of orders placed", "how many orders placed", "orders made"],
    "order_amount_sum": ["total order amount", "sum of order amounts", "total of order amounts", "combined order amount", "total order value", "order value total",
                         "sum of orders", "total amount of orders", "overall order amount", "order amount total"],
}
DIMENSION_SYNONYMS = {"amount": ["order value"]}
DATASET_SYNONYMS = {"orders": ["customer orders", "order records"]}

# Proposed by the agent and left out by the preparer, each for a stated reason.
LEFT_OUT = {
    "order volume": "volume can mean the amount or the count",
    "order quantity": "quantity can mean units, not orders",
    "order size": "size can mean the number of items",
    "amount per order": "reads as an average, which the contract does not define",
    "aggregate order amount": "over the limit of 10 per item; the least common phrasing",
    "total order amounts": "over the limit of 10 per item; a plural of a phrase already kept",
    "order amount": "equals the id of the field orders.amount once normalised, which the validator rejects",
    "order table": "names the table, not a business term",
    "order data": "generic",
}


def words_digest() -> str:
    import hashlib

    canonical = json.dumps({"m": METRIC_SYNONYMS, "d": DIMENSION_SYNONYMS, "s": DATASET_SYNONYMS}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def build() -> SemanticRegistryDocument:
    raw = json.loads(V1.read_text())
    raw["lifecycle"] = "certified"
    contract = raw["contract"]
    contract["version"] = "2026-11-20"
    contract["metadata"] = {**contract.get("metadata", {}), "certification": {
        "status": "certified by the owner on 2026-11-20 (approval recorded in docs/execution/enterprise-analytics/agentic-data-stack-program.yaml, s6_s4_k3_decisions)",
        "change": "synonyms only; no definition, grain, filter or policy changed", "source": "words proposed by a fresh agent, filtered by the preparer",
        "previous_version": "2026-11-18", "words_digest": words_digest()}}
    for metric in contract["metrics"]:
        metric["synonyms"] = METRIC_SYNONYMS.get(metric["id"], [])
    for dim in contract["dimensions"]:
        dim["synonyms"] = DIMENSION_SYNONYMS.get(dim["id"], [])
    for ds in contract["datasets"]:
        ds["synonyms"] = DATASET_SYNONYMS.get(ds["id"], [])
    return SemanticRegistryDocument.model_validate(raw)


def render() -> str:
    return json.dumps(build().model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    PROPOSED.write_text(render())
