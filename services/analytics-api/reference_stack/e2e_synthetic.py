"""The synthetic food-delivery data as a third end-to-end dataset on DuckDB: the planted defects, the builder's 30 questions, and contracts over its curated views.

Two datasets share the 30 questions and the same keys: `build` (a DRAFT contract over the new `orders_clean` view: cuisine from `restaurants`, the order time in each city's local time) and
`build_certified` (the owner's CERTIFIED `food-delivery-orders-current-v1`, run over `orders_current`, to show what the certified contract does with the planted defects).

The data is invented (`reference_stack/synthetic/`), with ten defects and two independent answer keys (a ledger from the clean orders and a DuckDB recomputation);
the keys used here are the generator's `truth.json`. `orders_current` is the curated view of `docs/design/SYNTHETIC_DATA_GENERATOR_SPEC.md`: one row per real order,
the latest recorded version, no reversal rows, no test restaurant. It answers "what does `order_count` mean" (9,995 distinct orders, not the 11,126 raw rows).

The contract is an AUTHORED DRAFT FOR THE OWNER'S REVIEW, not certified: one metric per question the view can answer, each with the filter that gives it its meaning. Three things a contract
over this view cannot do today, and the questions that need them are expected to be refused as named gaps: refunds and net value (reversal rows are not in the view), months in each city's
local time (the compiler does not convert time zones), and ratios or joins (not supported by the compiler). Some answers will differ from the key by design: the data carries planted defects
(spelling drift in cuisine, mixed currencies, placeholder minutes) that a contract can hide or expose.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from packages.platform_contracts.semantic import SemanticRegistryDocument
from reference_stack.e2e_duckdb import Case, Dataset
from reference_stack.loaders.engines import dialect
from reference_stack.loaders.synthetic import CLEAN_VIEW, VIEW, clean_view_sql, curated_view_sql

SERVICE = Path(__file__).resolve().parent.parent
QUESTIONS = SERVICE / "reference_stack/synthetic/food_delivery_questions.json"
OWNER = {"id": "team.semantic-owners", "display_name": "Semantic owners", "owner_type": "team"}
RESTRICTED = {"customer_id", "customer_email", "courier_phone", "delivery_address"}  # invented, but shaped like the real thing: never offered to a model
TYPES = {"BIGINT": "integer", "INTEGER": "integer", "VARCHAR": "string", "TIMESTAMP": "timestamp", "BOOLEAN": "boolean", "DATE": "date", "DOUBLE": "decimal"}
GAPS = {  # truth key -> what a contract over the curated views cannot do yet
    "refunds_usd_cents": "reversal rows are not in the curated views", "net_value_usd_cents": "needs refunds, and reversal rows are not in the curated views",
    "reversal_rows": "reversal rows are not in the curated views",
}
NOT_CERTIFIED = {  # extra gaps for the certified orders_current contract: it holds counts and dimensions only
    "gross_value_usd_cents": "amount sums are not certified on orders_current", "tip_card_usd_cents": "amount sums are not certified on orders_current",
    "delivery_minutes_sum": "averages and minute sums are not certified on orders_current", "delivery_minutes_count": "a timed-delivery count is not certified on orders_current",
}
CERTIFIED = SERVICE / "semantic_registry_synthetic/contracts/food-delivery-orders-current-v1.json"


def prepare(data_dir: Path, out_dir: Path) -> dict[str, Path]:
    """Build `orders_current.parquet` and `orders_clean.parquet` by running the views' own SQL in DuckDB over the generated tables."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((data_dir / "manifest.json").read_text())
    columns = manifest["tables"]["orders_flat"]["columns"]
    d = dialect("duckdb")
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS cat")
    con.execute("CREATE SCHEMA cat.sch")
    for name in ("orders_flat", "restaurants"):
        con.execute(f"CREATE VIEW cat.sch.{name} AS SELECT * FROM read_parquet('{(data_dir / (name + '.parquet')).as_posix()}')")
    for sql in (curated_view_sql(d, "cat", "sch", columns), clean_view_sql(d, "cat", "sch", columns)):
        con.execute(sql[: sql.index(" COMMENT")] + sql[sql.index(" AS SELECT"):])  # DuckDB has no view comment clause
    out = {}
    for view in (VIEW, CLEAN_VIEW):
        out[view] = out_dir / f"{view}.parquet"
        con.execute(f"COPY (SELECT * FROM cat.sch.{view}) TO '{out[view].as_posix()}' (FORMAT PARQUET)")
    return out


def _types(parquet: Path) -> list[tuple[str, str]]:
    rows = duckdb.connect().execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet.as_posix()}')").fetchall()
    return [(name, "decimal" if str(t).startswith("DECIMAL") else TYPES[str(t)]) for name, t, *_ in rows]


def contract(parquet: Path) -> dict[str, Any]:
    fields = [{"id": f"orders.{n}", "dataset_id": "orders", "physical_name": n, "data_type": t, "classification": "restricted" if n in RESTRICTED else "internal"} for n, t in _types(parquet)]
    grain = {"kind": "order", "key_field_ids": ["orders.row_id"]}

    def flt(fid: str, field: str, op: str, value: Any) -> dict[str, Any]:
        return {"id": fid, "dataset_id": "orders", "field_id": f"orders.{field}", "operator": op, "value_source": "literal", "literal_value": value}

    filters = [flt("delivered", "status", "equals", "delivered"), flt("cancelled", "status", "equals", "cancelled"), flt("refunded", "status", "equals", "refunded"),
               flt("partially_refunded", "status", "equals", "partially_refunded"), flt("not_cancelled", "status", "not_equals", "cancelled"), flt("usd", "currency", "equals", "USD"),
               flt("card", "payment_method", "equals", "card"), flt("timed", "delivery_minutes", "greater_than", -1)]

    def metric(mid: str, agg: str, measure: str | None, fl: list[str], name: str, desc: str) -> dict[str, Any]:
        return {"id": mid, "display_name": name, "dataset_id": "orders", "aggregation": agg, "measure_field_id": measure, "grain": grain, "required_filter_ids": fl,
                "certification": "certified", "owner_ids": [OWNER["id"]], "description": desc}

    metrics = [
        metric("order_count", "count", None, [], "Order count", "One row per real order: the latest recorded version, no reversal rows, no test orders."),
        metric("delivered_orders", "count", None, ["delivered"], "Delivered orders", "Real orders whose latest status is delivered."),
        metric("cancelled_orders", "count", None, ["cancelled"], "Cancelled orders", "Real orders whose latest status is cancelled."),
        metric("refunded_orders", "count", None, ["refunded"], "Refunded orders", "Real orders whose latest status is refunded."),
        metric("partial_refunds", "count", None, ["partially_refunded"], "Partial refunds", "Real orders whose latest status is partially refunded."),
        metric("gross_value_usd", "sum", "orders.total_amount", ["not_cancelled", "usd"], "Gross value in US dollars", "Total amount of orders paid in US dollars; cancelled orders and other currencies are excluded."),
        metric("card_tips_usd", "sum", "orders.tip_amount", ["not_cancelled", "usd", "card"], "Card tips in US dollars", "Tips recorded on card orders paid in US dollars; cash tips are not recorded in this field."),
        metric("delivery_minutes_total", "sum", "orders.delivery_minutes", ["timed"], "Delivery minutes total", "Minutes from dispatch to delivery over orders with a recorded time; the placeholder -1 and missing times are excluded."),
        metric("timed_deliveries", "count", None, ["timed"], "Timed deliveries", "Orders with a recorded delivery time."),
    ]
    dims = [("city", "City", "categorical", "city"), ("country", "Country", "categorical", "country"), ("platform", "Platform", "categorical", "platform"),
            ("status", "Status", "categorical", "status"), ("payment_method", "Payment method", "categorical", "payment_method"), ("cuisine", "Cuisine", "categorical", "cuisine"),
            ("order_date", "Order date in local time", "temporal", "order_local_ts")]  # the UTC time is a field but not a dimension: one way to ask for a month, and it is the local one
    dimensions = [{"id": i, "display_name": n, "dataset_id": "orders", "field_id": f"orders.{f}", "entity_id": "order", "dimension_type": t, "owner_ids": [OWNER["id"]]} for i, n, t, f in dims]
    return {"lifecycle": "draft", "contract": {
        "id": "food-delivery-orders-clean", "tenant_id": "tenant-a", "domain": "food_delivery", "version": "draft-2", "owners": [OWNER],
        "datasets": [{"id": "orders", "display_name": "Orders", "source_asset_id": "synthetic.orders_clean", "physical_name": CLEAN_VIEW,
                      "description": "SYNTHETIC DATA: one row per real order, the latest recorded version, with the restaurant's own cuisine and the order time in the city's local time.", "owner_ids": [OWNER["id"]]}],
        "fields": fields, "entities": [{"id": "order", "dataset_id": "orders", "grain": grain, "owner_ids": [OWNER["id"]]}], "dimensions": dimensions, "filters": filters,
        "metrics": metrics, "joins": [],
        "policies": [{"id": f"policy_{m['id']}", "target_ids": [m["id"]], "classification": "internal", "allowed_purposes": ["analytics"], "required_filter_ids": m["required_filter_ids"],
                      "owner_ids": [OWNER["id"]]} for m in metrics]}}


def cases(truth: dict[str, Any], *, certified: bool = False) -> tuple[Case, ...]:
    questions = json.loads(QUESTIONS.read_text())
    gaps = {**GAPS, **(NOT_CERTIFIED if certified else {})}
    out: list[Case] = []
    for q in questions["answerable"]:
        t = q["truth"]
        name = t.get("value") or t["map"]
        if name in gaps:
            out.append(Case(q["id"], q["question"], "refuse", None, f"gap: {gaps[name]}"))
        elif "value" in t:
            v = truth["values"][name]
            out.append(Case(q["id"], q["question"], "answer", v / 100 if name.endswith("_cents") else v))
        else:
            out.append(Case(q["id"], q["question"], "answer", {t["entry"]: truth["maps"][name][t["entry"]]}))
    out += [Case(r["id"], r["question"], "refuse", None, f"builder refusal: {r['reason']}") for r in questions["refusals"]]
    return tuple(out)


def build(data_dir: Path, work_dir: Path) -> Dataset:
    sources = prepare(Path(data_dir), Path(work_dir))
    truth = json.loads((Path(data_dir) / "truth.json").read_text())
    return Dataset(name="Synthetic food delivery, tier S (invented data, planted defects, answer keys from the generator), draft contract over orders_clean",
                   document=SemanticRegistryDocument.model_validate(contract(sources[CLEAN_VIEW])), sources={CLEAN_VIEW: sources[CLEAN_VIEW]}, cases=cases(truth), certified_by_owner=False,
                   note="The contract is an authored draft over the new `orders_clean` view, for the owner's review. Questions that need reversal rows are expected to be refused as named gaps.")


def build_certified(data_dir: Path, work_dir: Path) -> Dataset:
    """The owner's certified `food-delivery-orders-current-v1` (counts and eight dimensions, no synonyms) over `orders_current`. Only the table's physical name is rewritten, from the
    Databricks name to the local one, in memory."""
    sources = prepare(Path(data_dir), Path(work_dir))
    truth = json.loads((Path(data_dir) / "truth.json").read_text())
    raw = json.loads(CERTIFIED.read_text())
    for ds in raw["contract"]["datasets"]:
        ds["physical_name"] = VIEW
    return Dataset(name="Synthetic food delivery, tier S, the owner's CERTIFIED orders_current contract", document=SemanticRegistryDocument.model_validate(raw), sources={VIEW: sources[VIEW]},
                   cases=cases(truth, certified=True), certified_by_owner=True,
                   note="`food-delivery-orders-current-v1` as certified (counts and eight dimensions; amount sums are not certified); the tenant id and the table's physical name are rewritten in memory for the harness.")
