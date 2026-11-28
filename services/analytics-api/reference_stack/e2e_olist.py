"""Olist (real Brazilian e-commerce, about 100,000 orders) as the second end-to-end dataset on DuckDB.

Licence: reported as CC BY-NC-SA 4.0 (non-commercial, share-alike); `docs/design/REALISTIC_DATA_SOURCE.md` keeps it out of public use until checked. This
module and its report are for INTERNAL testing only: do not show the numbers or the data publicly or in a demo before the licence is checked.

The contract is the repository's certified `olist-commerce-v1` (one metric, `delivered_revenue`, on a prepared `olist_orders` that carries `payment_total`);
only its tenant id is rewritten for the harness. The raw Olist `orders` table has no `payment_total`: payments are summed per order first (the rule in
`tests/fixtures/olist/README.md`), and that preparation is a step the contract assumes but does not itself perform. That is a finding, recorded in the report.

The questions are the owner's draft golden set (`docs/evaluation/OLIST_GOLDEN_QUESTIONS_DRAFT.md`). Where the certified contract cannot answer a question
(no metric for it), the correct behaviour today is to REFUSE, and the report counts that as a gap that needs a certified definition, not as a failure.
The answer keys for the covered questions are computed in plain Python straight from the CSVs, never through the product or DuckDB.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from packages.platform_contracts.semantic import SemanticRegistryDocument
from reference_stack.e2e_duckdb import Case, Dataset

SERVICE = Path(__file__).resolve().parent.parent
CONTRACT = SERVICE / "semantic_registry/contracts/olist-commerce-v1.json"


def prepare(csv_dir: Path, out_dir: Path) -> dict[str, Path]:
    """The step the contract assumes: see `reference_stack/olist_prepare.py` (payments summed per order first, then checked against plain Python)."""
    from reference_stack import olist_prepare

    return olist_prepare.prepare(csv_dir, out_dir)


def keys(csv_dir: Path) -> dict[str, object]:
    """Plain Python over the CSVs: delivered revenue is the sum of payments per delivered order, by the month the order was purchased."""
    pay: dict[str, Decimal] = defaultdict(Decimal)
    with open(csv_dir / "olist_order_payments_dataset.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pay[row["order_id"]] += Decimal(row["payment_value"])
    by_month: dict[str, Decimal] = defaultdict(Decimal)
    with open(csv_dir / "olist_orders_dataset.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["order_status"] == "delivered" and row["order_id"] in pay:
                by_month[row["order_purchase_timestamp"][:7] + "-01"] += pay[row["order_id"]]
    top = max(by_month, key=lambda m: by_month[m])
    return {"by_month": dict(by_month), "y2017": sum((v for m, v in by_month.items() if m.startswith("2017")), Decimal(0)), "top": {top: by_month[top]}}


GAPS = {
    "Q02": "an order-count metric by status", "Q05": "an order-count metric with a delivered filter", "Q06": "average order value at order grain",
    "Q07": "item-level GMV by category (a category dimension and a join)", "Q08": "item GMV by category with ranking", "Q09": "a customer state dimension and a join",
    "Q10": "a delivery-days metric by state", "Q11": "a late-rate metric", "Q12": "review score by category", "Q13": "an order-count metric with a status filter",
    "Q14": "a period comparison", "Q15": "a payment-type dimension", "Q16": "an instalments metric with a payment-type filter", "Q17": "a ratio metric",
    "Q20": "a seller dimension and join",
}


def cases(k: dict[str, object], csv_dir: Path) -> tuple[Case, ...]:
    questions = {}
    for line in (SERVICE.parent.parent / "docs/evaluation/OLIST_GOLDEN_QUESTIONS_DRAFT.md").read_text().splitlines():
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) >= 5 and parts[0].startswith("Q") and parts[0][1:].isdigit():
            questions[parts[0]] = (parts[1], parts[2])
    by_month, y2017, top = k["by_month"], k["y2017"], k["top"]
    keys_by_id = {"Q01": by_month, "Q03": y2017, "Q04": top}
    out = []
    for qid in sorted(questions):
        text, outcome = questions[qid]
        if qid in keys_by_id:
            out.append(Case(qid, text, "answer", keys_by_id[qid]))
        else:
            out.append(Case(qid, text, "refuse", None, ("gap: needs " + GAPS[qid]) if qid in GAPS else f"golden outcome: {outcome}"))
    return tuple(out)


def build(csv_dir: Path, work_dir: Path) -> Dataset:
    sources = prepare(Path(csv_dir), Path(work_dir))
    document = SemanticRegistryDocument.model_validate_json(CONTRACT.read_text())
    return Dataset(name="Olist (real Brazilian e-commerce; CC BY-NC-SA 4.0 reported: INTERNAL use only)", document=document, sources=sources,
                   cases=cases(keys(Path(csv_dir)), Path(csv_dir)), certified_by_owner=True,
                   note="The repository's certified olist-commerce-v1 (tenant id rewritten in memory for the harness). Questions not covered by a certified metric are expected to be refused today and are counted as gaps.")
