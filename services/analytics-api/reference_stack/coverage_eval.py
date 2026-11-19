"""K2 of `docs/design/INTENT_COVERAGE_CHECK_PLAN.md`: what the coverage check changes, measured offline.

Fakes only: no model, no warehouse. Each case is a question and the intent a model is assumed to have returned. Verdicts are computed
two ways, with the echo check alone (today) and with the echo check plus the coverage check, over the sandbox contract with a REVIEWED
synonym set that is a fixture of this evaluation (not a certification; the published contract is untouched). Thresholds are PROPOSED, not
approved. **The neutral list was written before these cases and was not changed after seeing their results.** The cases were written by the
author of the check; the owner's hold-out file (`m7/coverage-holdout.owner.json`) is the figure that counts and is empty until the owner fills it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from app.context.registry_context import context_from_contract
from app.runtime.intent_coverage import NEUTRAL_VERSION, NEUTRAL_WORDS, unaccounted_positions
from app.runtime.intent_echo import echo_problems
from app.runtime.ontology_node import IntentResolutionError, resolve_certified_intent
from packages.platform_contracts.analytics_intent import AnalyticalIntent
from packages.platform_contracts.semantic import SemanticRegistryDocument

SERVICE = Path(__file__).resolve().parent.parent
CONTRACT = SERVICE / "semantic_registry_sandbox/contracts/sandbox-databricks-v1.json"
HOLDOUT = Path(__file__).resolve().parent / "m7/coverage-holdout.owner.json"
SUITE_VERSION = "coverage-v1"
COUNT, SUM = "order_count", "order_amount_sum"
REVIEWED = {COUNT: ["how many orders", "number of orders", "orders placed", "orders received", "order tally"], SUM: ["total order amount", "order total", "sum of orders"]}
PROPOSED_THRESHOLDS = {"dropped_constraint_accepted_max": 0, "omitted_metric_accepted_max": 0, "valid_flagged_max_rate": 0.20}
DESC = [{"target_kind": "metric", "target_id": COUNT, "direction": "desc"}]

# (id, group, question, metric, what the model's intent also holds)
#   valid: a plain question the metric answers   faithful: the question has a condition and the model's intent holds it
#   dropped: the question has a condition the intent omits   omitted: a second metric the intent omits   near_miss: not the metric at all
CASES: list[tuple[str, str, str, str, dict]] = [
    ("V01", "valid", "How many orders are there?", COUNT, {}), ("V02", "valid", "How many orders do we have?", COUNT, {}),
    ("V03", "valid", "Could you tell me how many orders there are, please?", COUNT, {}), ("V04", "valid", "What is the number of orders?", COUNT, {}),
    ("V05", "valid", "Show me the order count", COUNT, {}), ("V06", "valid", "I need the order count", COUNT, {}),
    ("V07", "valid", "What's the order count right now?", COUNT, {}), ("V08", "valid", "How many orders have we received in total?", COUNT, {}),
    ("V09", "valid", "Give me the order tally", COUNT, {}), ("V10", "valid", "orders placed", COUNT, {}),
    ("V11", "valid", "What is the total order amount?", SUM, {}), ("V12", "valid", "What's the order total?", SUM, {}),
    ("V13", "valid", "Can you show me the order amount sum please?", SUM, {}), ("V14", "valid", "Tell me the sum of orders", SUM, {}),
    ("V15", "valid", "What is our total order amount overall?", SUM, {}), ("V16", "valid", "I want to see the order total", SUM, {}),
    ("V17", "valid", "Quickly, how many orders are there?", COUNT, {}), ("V18", "valid", "Hi, what is the order count?", COUNT, {}),
    ("V19", "valid", "How many orders did we get?", COUNT, {}), ("V20", "valid", "What was the order total?", SUM, {}),
    ("F01", "faithful", "Top 5 order counts", COUNT, {"limit": 5, "sort": DESC}),
    ("F02", "faithful", "How many orders with order amount 210?", COUNT, {"filters": [{"field_id": "orders.amount", "operator": "equals", "values": ["210"]}]}),
    ("F03", "faithful", "Top 3 order counts", COUNT, {"limit": 3, "sort": DESC}),
    ("D01", "dropped", "How many orders were refunded?", COUNT, {}), ("D02", "dropped", "How many orders were placed last year?", COUNT, {}),
    ("D03", "dropped", "How many orders were placed in EMEA?", COUNT, {}), ("D04", "dropped", "What is the total order amount last month?", SUM, {}),
    ("D05", "dropped", "How many orders per month?", COUNT, {}), ("D06", "dropped", "Top 10 order counts", COUNT, {}),
    ("D07", "dropped", "How many orders from paying customers?", COUNT, {}), ("D08", "dropped", "How many orders were cancelled yesterday?", COUNT, {}),
    ("D09", "dropped", "What is the order total for gold customers?", SUM, {}), ("D10", "dropped", "How many orders above 100?", COUNT, {}),
    ("D11", "dropped", "How many orders this quarter?", COUNT, {}), ("D12", "dropped", "What is the total order amount in 2024?", SUM, {}),
    ("O01", "omitted", "How many orders are there and what is the total order amount?", COUNT, {}),
    ("O02", "omitted", "Show the order count and the order amount sum", COUNT, {}),
    ("O03", "omitted", "Give me the order total and the number of orders", SUM, {}),
    ("O04", "omitted", "What is the total order amount and how many orders?", SUM, {}),
    ("O05", "omitted", "Order count plus order total", COUNT, {}),
    ("N01", "near_miss", "What is the total profit?", SUM, {}), ("N02", "near_miss", "How many customers are there?", COUNT, {}),
    ("N03", "near_miss", "What is the average order amount?", SUM, {}), ("N04", "near_miss", "How many products do we sell?", COUNT, {}),
    ("N05", "near_miss", "What is the total order amount?", COUNT, {}), ("N06", "near_miss", "How many orders are there?", SUM, {}),
    ("N07", "near_miss", "Show total revenue by customer region", SUM, {}), ("N08", "near_miss", "How many visitors came last week?", COUNT, {}),
]


def _document() -> SemanticRegistryDocument:
    raw = json.loads(CONTRACT.read_text())
    for metric in raw["contract"]["metrics"]:
        metric["synonyms"] = REVIEWED.get(metric["id"], [])
    return SemanticRegistryDocument.model_validate(raw)


def verdicts(question: str, metric: str, extra: dict) -> tuple[str, str]:
    """(echo alone, echo plus coverage): `answered` or why it was refused."""
    document = _document()
    ontology = context_from_contract(document)[1]
    intent = AnalyticalIntent.model_validate({
        "query_id": "q", "tenant_id": document.contract.tenant_id, "dataset_id": "orders", "metrics": [{"metric_id": metric}],
        "semantic_contract": {"contract_id": document.contract.id, "contract_version": document.contract.version}, **extra})
    try:
        resolved = resolve_certified_intent(intent, document.contract, ontology)
    except IntentResolutionError as exc:
        return (f"refused:{exc.code}",) * 2
    if echo_problems(question, resolved, ontology):
        return ("refused:echo",) * 2
    return "answered", ("refused:coverage" if unaccounted_positions(question, resolved, ontology) else "answered")


def _count(rows: list[tuple], group: str, index: int, want: str = "answered") -> tuple[int, int]:
    sel = [r for r in rows if r[1] == group]
    return sum(1 for r in sel if r[index] == want), len(sel)


def holdout() -> dict[str, Any]:
    cases = json.loads(HOLDOUT.read_text()).get("cases", []) if HOLDOUT.exists() else []
    rows = []
    for c in cases:
        echo, both = verdicts(c["question"], c["metric"], {})
        rows.append((c["expect"], echo, both))
    wrong_answered = sum(1 for e, _, b in rows if e == "refuse" and b == "answered")
    flagged_valid = sum(1 for e, echo, b in rows if e == "answer" and echo == "answered" and b != "answered")
    refused_valid = sum(1 for e, _, b in rows if e == "answer" and b != "answered")
    return {"n": len(rows), "wrong_answered": wrong_answered, "flagged_valid": flagged_valid, "refused_valid": refused_valid}


def run() -> dict[str, Any]:
    rows = [(cid, group, q, metric, *verdicts(q, metric, extra)) for cid, group, q, metric, extra in CASES]
    return {"rows": rows, "holdout": holdout(),
            "valid": (_count(rows, "valid", 4), _count(rows, "valid", 5)), "faithful": (_count(rows, "faithful", 4), _count(rows, "faithful", 5)),
            "dropped": (_count(rows, "dropped", 4), _count(rows, "dropped", 5)), "omitted": (_count(rows, "omitted", 4), _count(rows, "omitted", 5)),
            "near_miss": (_count(rows, "near_miss", 4), _count(rows, "near_miss", 5))}


def _p(pair: tuple[int, int]) -> str:
    return f"{pair[0]}/{pair[1]} ({100 * pair[0] // pair[1]}%)" if pair[1] else "n/a"


def render_markdown() -> str:
    r = run()
    valid_flagged = (r["valid"][0][0] - r["valid"][1][0], r["valid"][0][1])  # answered by the echo check but refused once coverage is added
    faith_flagged = (r["faithful"][0][0] - r["faithful"][1][0], r["faithful"][0][1])
    v_rate = valid_flagged[0] / valid_flagged[1]
    h = r["holdout"]
    lines = [
        "# Intent coverage K2: corpus evaluation (generated)", "",
        f"Suite `{SUITE_VERSION}`, neutral list `{NEUTRAL_VERSION}` ({len(NEUTRAL_WORDS)} words). Generated by `services/analytics-api/reference_stack/coverage_eval.py`; a test keeps this file equal to the generator. "
        "Fakes only. The sandbox contract with a **reviewed synonym set that is a fixture of this evaluation, not a certification**; the published contract is untouched. "
        "Thresholds are **proposed, not approved**. The neutral list was written before these cases and was **not changed after seeing their results**. "
        "The cases were written by the check's author and the figures are therefore optimistic; the owner's hold-out, not these, decides K3.", "",
        "| Group | Echo check alone: answered | Echo plus coverage: answered | Proposed limit |", "|---|---|---|---|",
        f"| Valid plain questions (answered is good) | {_p(r['valid'][0])} | {_p(r['valid'][1])} | flagged at most {int(PROPOSED_THRESHOLDS['valid_flagged_max_rate'] * 100)}%: **{'met' if v_rate <= PROPOSED_THRESHOLDS['valid_flagged_max_rate'] else 'not met'}** ({_p(valid_flagged)} newly refused) |",
        f"| Faithful intents that hold their condition (answered is good) | {_p(r['faithful'][0])} | {_p(r['faithful'][1])} | none proposed ({_p(faith_flagged)} newly refused) |",
        f"| Dropped constraint (answered is bad) | {_p(r['dropped'][0])} | {_p(r['dropped'][1])} | at most {PROPOSED_THRESHOLDS['dropped_constraint_accepted_max']}: **{'met' if r['dropped'][1][0] <= PROPOSED_THRESHOLDS['dropped_constraint_accepted_max'] else 'not met'}** |",
        f"| Omitted second metric (answered is bad) | {_p(r['omitted'][0])} | {_p(r['omitted'][1])} | at most {PROPOSED_THRESHOLDS['omitted_metric_accepted_max']}: **{'met' if r['omitted'][1][0] <= PROPOSED_THRESHOLDS['omitted_metric_accepted_max'] else 'not met'}** |",
        f"| Near-misses (answered is bad) | {_p(r['near_miss'][0])} | {_p(r['near_miss'][1])} | 0 |", "",
        "## Owner's hold-out", "",
        ("Not provided yet: `reference_stack/m7/coverage-holdout.owner.json` has no cases. **K3 waits for it.**" if not h["n"] else
         f"{h['n']} questions: answered although the owner expected a refusal {h['wrong_answered']}; refused or flagged although the owner expected an answer {h['refused_valid']} ({h['flagged_valid']} of them by the coverage check)."), "",
        "## Cases", "", "| Case | Group | Question | Metric returned | Echo alone | Echo plus coverage |", "|---|---|---|---|---|---|"]
    lines += [f"| {c} | {g} | {q} | `{m}` | {a} | {b} |" for c, g, q, m, a, b in r["rows"]]
    lines += ["", "## What this shows, and what it does not", "",
              "- **The sandbox contract has no temporal dimension**, so no intent can hold a time range or a time grouping here: every time condition (D02, D04, D05, D08, D11, D12) is a dropped constraint by construction, and the time-accounting rules of the check are covered by unit tests, not by this corpus.",
              "- The check closes the two holes synonyms open when the model's intent is as assumed here; it cannot catch what a model puts *inside* a filter or a time range.",
              "- **The valid questions it newly refuses** (V07 \"right now\", V08 \"received in total\" with \"orders received\" not contiguous, V17 \"Quickly,\", V18 \"Hi,\") are filler words and a split phrase; the neutral list was deliberately not extended to fit them. At 20% the proposed limit is met only at its boundary on cases the author wrote.",
              "- Any valid question it newly refuses is the price of being deterministic; the remedy is a certified synonym for the domain word, not a longer neutral list.",
              "- It does not show a real model's intents, a real user's phrasing, other contracts or other languages."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    out = render_markdown()
    if "--write" in sys.argv:
        (SERVICE.parent.parent / "docs/evaluation/intent-coverage-k2-report.md").write_text(out)
    else:
        sys.stdout.write(out)
