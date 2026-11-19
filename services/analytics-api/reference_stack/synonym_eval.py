"""S3 of the synonyms design (`docs/design/SEMANTIC_SYNONYMS_DESIGN.md`): what certified synonyms change at the acceptance check, measured offline.

Fakes only: no model, no warehouse. Each case is a question and the intent a model is assumed to have returned (a certified metric id),
run through the same two steps the graph's ontology node runs: resolve the intent against the certified ontology, then the deterministic
intent-echo check. The sandbox contract is the one the owner certified for C5; "with synonyms" is a REVIEWED SET defined here, applied to
a copy in memory. **Nothing here certifies anything and the published contract is untouched.** Thresholds are PROPOSED, not approved.

What this does not show: how a real model behaves, or whether a real reviewer would certify these words. The cases are written by the
author of the feature; a different author would write different questions.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.context.registry_context import context_from_contract
from app.ontology_draft.proposal import DraftMetric
from app.ontology_draft.validate import Drop, _apply_words, _check_words
from app.runtime.intent_echo import echo_problems
from app.runtime.ontology_node import IntentResolutionError, resolve_certified_intent
from packages.platform_contracts.analytics_intent import AnalyticalIntent
from packages.platform_contracts.semantic import SemanticRegistryDocument

SERVICE = Path(__file__).resolve().parent.parent
CONTRACT = SERVICE / "semantic_registry_sandbox/contracts/sandbox-databricks-v1.json"
SUITE_VERSION = "synonym-phrasing-v1"
COUNT, SUM = "order_count", "order_amount_sum"

# What a reviewer might certify. A fixture of this evaluation, not a certification.
REVIEWED = {COUNT: ["how many orders", "number of orders", "orders placed"], SUM: ["total order amount", "order total"]}

# (id, group, question, the certified metric the model returned)
#   group: paraphrase (should be answered) | near_miss (must be refused) | dropped_constraint | omitted_metric (measured, known gaps)
CASES: list[tuple[str, str, str, str]] = [
    ("P01", "paraphrase", "How many orders are there?", COUNT),
    ("P02", "paraphrase", "What is the number of orders?", COUNT),
    ("P04", "paraphrase", "Show me the order count", COUNT),
    ("P05", "paraphrase", "orders placed", COUNT),
    ("P06", "paraphrase", "Count of orders", COUNT),
    ("P07", "paraphrase", "How many purchases do we have?", COUNT),
    ("P08", "paraphrase", "What is the total order amount?", SUM),
    ("P09", "paraphrase", "What's the order total?", SUM),
    ("P10", "paraphrase", "Give me the order amount sum", SUM),
    ("P11", "paraphrase", "How much did customers spend in total?", SUM),
    ("P12", "paraphrase", "Total sales", SUM),
    ("N01", "near_miss", "What is the total profit?", SUM),
    ("N02", "near_miss", "How many customers are there?", COUNT),
    ("N03", "near_miss", "What is the average order amount?", SUM),
    ("N04", "near_miss", "What is the largest order amount?", SUM),
    ("N05", "near_miss", "How many products do we sell?", COUNT),
    ("N06", "near_miss", "What is the total order amount?", COUNT),  # the model picks the wrong certified metric for a question that has a certified word
    ("N07", "near_miss", "How many orders are there?", SUM),
    ("N08", "near_miss", "Show total revenue by customer region", SUM),
    ("D01", "dropped_constraint", "How many orders were refunded?", COUNT),
    ("D02", "dropped_constraint", "What is the total order amount last month?", SUM),
    ("D03", "dropped_constraint", "How many orders were placed in EMEA?", COUNT),
    ("D04", "dropped_constraint", "How many orders were placed last year?", COUNT),
    ("O01", "omitted_metric", "How many orders are there and what is the total order amount?", COUNT),
    ("O02", "omitted_metric", "Show the order count and the order amount sum", COUNT),
]

# (metric id, proposed word, whether a reviewer is assumed to want it). `wanted` is the author's label.
PROPOSALS: list[tuple[str, str, bool]] = [
    (COUNT, "number of orders", True), (COUNT, "how many orders", True), (COUNT, "orders placed", True), (COUNT, "order tally", True),
    (COUNT, "order", False),  # one generic word inside another metric's name
    (COUNT, "orders id", False),  # the id of a field
    (COUNT, "orders by region", False),  # a grouping phrase
    (COUNT, "monthly", False),  # a bare time word
    (COUNT, "ORD 20240117", False),  # identifier-like
    (SUM, "total order amount", True), (SUM, "order total", True), (SUM, "sum of orders", True),
    (SUM, "order tally", False),  # a word the other metric already holds
    (SUM, "amount", False),  # one generic word inside the metric's own name and another metric's
    (SUM, "total; DROP TABLE orders", False),  # not a business word
    (SUM, "revenue per order", False),  # a grouping word
]

PROPOSED_THRESHOLDS = {
    "wrong_metric_acceptance_max": 0,
    "bad_words_kept_max": 0,
    "false_abstention_with_synonyms_max": 0.25,
}


def _document(words: dict[str, list[str]] | None) -> SemanticRegistryDocument:
    raw = json.loads(CONTRACT.read_text())
    for metric in raw["contract"]["metrics"]:
        if words and metric["id"] in words:
            metric["synonyms"] = list(words[metric["id"]])
    return SemanticRegistryDocument.model_validate(raw)


def _verdict(document: SemanticRegistryDocument, question: str, metric_id: str) -> str:
    intent = AnalyticalIntent.model_validate({
        "query_id": "q", "tenant_id": document.contract.tenant_id, "dataset_id": "orders", "metrics": [{"metric_id": metric_id}],
        "semantic_contract": {"contract_id": document.contract.id, "contract_version": document.contract.version}})
    ontology = context_from_contract(document)[1]
    try:
        resolved = resolve_certified_intent(intent, document.contract, ontology)
    except IntentResolutionError as exc:
        return f"refused:{exc.code}"
    problems = echo_problems(question, resolved, ontology)
    return "refused:echo" if problems else "answered"


def phrasing(words: dict[str, list[str]] | None) -> dict[str, Any]:
    document = _document(words)
    rows = [(cid, group, question, metric, _verdict(document, question, metric)) for cid, group, question, metric in CASES]
    def rate(group: str, want: str) -> tuple[int, int]:
        sel = [r for r in rows if r[1] == group]
        return sum(1 for r in sel if r[4] == want), len(sel)
    return {"rows": rows, "answered_paraphrases": rate("paraphrase", "answered"), "near_miss_accepted": rate("near_miss", "answered"),
            "dropped_constraint_accepted": rate("dropped_constraint", "answered"), "omitted_metric_accepted": rate("omitted_metric", "answered")}


def proposals() -> dict[str, Any]:
    """Run the per-word and cross-object rules on a fixed set of proposed words, one at a time in order, as the drafter's validator would:
    each word meets the per-word rules and the words already kept."""
    raw = json.loads(CONTRACT.read_text())
    contract = SemanticRegistryDocument.model_validate(raw).contract
    drops: list[Drop] = []
    base = {"table": "orders", "grain_key_columns": ["id"], "description": "x"}
    shapes = {COUNT: {"aggregation": "count"}, SUM: {"aggregation": "sum", "measure_column": "amount"}}
    kept: dict[str, list[str]] = {COUNT: [], SUM: []}
    rows = []
    for metric, word, wanted in PROPOSALS:
        item = DraftMetric(id=metric, synonyms=[word], **base, **shapes[metric])
        words = _check_words([item], [], None, drops)
        if words:
            earlier = {m: (None, v) for m, v in kept.items() if m != metric and v}  # words already kept come first, so a new word is the one that yields
            trial = _apply_words(contract, earlier | {metric: (None, kept[metric] + words[metric][1])}, drops)
            if word in next(m for m in trial.metrics if m.id == metric).synonyms:
                kept[metric].append(word)
        rows.append((metric, word, wanted, word in kept[metric]))
    return {"rows": rows, "drops": Counter(d.code for d in drops),
            "wanted_kept": (sum(1 for r in rows if r[2] and r[3]), sum(1 for r in rows if r[2])),
            "bad_kept": (sum(1 for r in rows if not r[2] and r[3]), sum(1 for r in rows if not r[2]))}


def run() -> dict[str, Any]:
    without, with_words = phrasing(None), phrasing(REVIEWED)
    return {"without": without, "with": with_words, "proposals": proposals()}


def _pct(pair: tuple[int, int]) -> str:
    return f"{pair[0]}/{pair[1]} ({100 * pair[0] // pair[1]}%)" if pair[1] else "n/a"


def render_markdown() -> str:
    r = run()
    w0, w1, pr = r["without"], r["with"], r["proposals"]
    ans0, ans1 = w0["answered_paraphrases"], w1["answered_paraphrases"]
    lines = [
        "# Synonyms S3: phrasing and proposal evaluation (generated)", "",
        f"Suite `{SUITE_VERSION}`. Generated by `services/analytics-api/reference_stack/synonym_eval.py`; a test keeps this file equal to the generator. "
        "Fakes only: no model, no warehouse. The sandbox contract is the one the owner certified for C5; the reviewed synonym set is a fixture of this "
        "evaluation, **not a certification**, and the published contract is untouched. Thresholds are **proposed, not approved**. The cases were written by "
        "the feature's author; a different author would write different questions.", "",
        "## Phrasing: each question with the certified metric a model is assumed to have returned", "",
        "| Measure | Without synonyms | With the reviewed set | Proposed limit |", "|---|---|---|---|",
        f"| Paraphrases answered (higher is better) | {_pct(ans0)} | {_pct(ans1)} | false abstention with synonyms at most {int(PROPOSED_THRESHOLDS['false_abstention_with_synonyms_max'] * 100)}%: **{'met' if (ans1[1] - ans1[0]) / ans1[1] <= PROPOSED_THRESHOLDS['false_abstention_with_synonyms_max'] else 'not met'}** |",
        f"| Near-misses wrongly accepted (must be 0) | {_pct(w0['near_miss_accepted'])} | {_pct(w1['near_miss_accepted'])} | {PROPOSED_THRESHOLDS['wrong_metric_acceptance_max']}: **{'met' if w1['near_miss_accepted'][0] <= PROPOSED_THRESHOLDS['wrong_metric_acceptance_max'] else 'not met'}** |",
        f"| Dropped constraint accepted (known gap) | {_pct(w0['dropped_constraint_accepted'])} | {_pct(w1['dropped_constraint_accepted'])} | none proposed |",
        f"| Named metric omitted from the intent, accepted (known gap) | {_pct(w0['omitted_metric_accepted'])} | {_pct(w1['omitted_metric_accepted'])} | none proposed |", "",
        "| Case | Group | Question | Metric returned | Without | With |", "|---|---|---|---|---|---|",
    ]
    for a, b in zip(w0["rows"], w1["rows"], strict=True):
        lines.append(f"| {a[0]} | {a[1]} | {a[2]} | `{a[3]}` | {a[4]} | {b[4]} |")
    lines += ["", "## Proposed words through the validator's rules", "",
              f"Words a reviewer is assumed to want, kept: {_pct(pr['wanted_kept'])}. Words assumed unwanted, kept (must be 0): {_pct(pr['bad_kept'])}. "
              "Drop reasons (a word may be dropped by more than one rule; the first is recorded): " + ", ".join(f"{c} {n}" for c, n in sorted(pr["drops"].items())) + ".", "",
              "| Metric | Proposed word | Assumed wanted | Kept |", "|---|---|---|---|"]
    lines += [f"| `{m}` | {w} | {'yes' if wanted else 'no'} | {'yes' if kept else 'no'} |" for m, w, wanted, kept in pr["rows"]]
    lines += ["", "## What this shows, and what it does not", "",
              "- Synonyms raise the answered share of natural paraphrases without letting a near-miss through; the words nobody certified are still refused.",
              "- **Two gaps the echo check has, now measured:** it does not notice a constraint the question states and the intent omits (D01 to D03), and it does not notice a second metric the question names (O01, O02). Both matter more once synonyms let more questions pass; each is a separate small change to the echo check.",
              "- It does not show a real model's choices, a real reviewer's choices or any other contract."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = render_markdown()
    if "--write" in sys.argv:
        (SERVICE.parent.parent / "docs/evaluation/semantic-synonyms-s3-report.md").write_text(text)
    else:
        sys.stdout.write(text)
