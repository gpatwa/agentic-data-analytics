"""Online Retail II (real UK retail transactions, CC BY 4.0) as the first end-to-end dataset on DuckDB.

Source: Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository, https://doi.org/10.24432/C5CG6D. Licence CC BY 4.0: any page that
shows its numbers must carry that credit. The currency is not stated by the source and is not asserted here.

The contract below is an EVALUATION FIXTURE: what a careful owner would certify after review, authored for this run (the drafter, on its own, would have
proposed one naive sum of `line_amount`, which nets cancellations against sales: 19.29 million against 20.81 million of sales). It is not certified.
Metrics carry the filter that makes them mean what they say; customers are counted with `count_distinct`, which ignores the 243,007 lines with no customer id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from packages.platform_contracts.semantic import SemanticRegistryDocument
from reference_stack.e2e_duckdb import Case, Dataset

OWNER = {"id": "team.semantic-owners", "display_name": "Semantic owners", "owner_type": "team"}
TABLE = "online_retail_ii"


def _field(name: str, kind: str, classification: str = "internal") -> dict[str, Any]:
    return {"id": f"lines.{name}", "dataset_id": "lines", "physical_name": name, "data_type": kind, "classification": classification}


def contract() -> dict[str, Any]:
    fields = [_field("row_id", "integer"), _field("invoice_no", "string"), _field("quantity", "integer"), _field("invoice_ts", "timestamp"),
              _field("line_amount", "decimal"), _field("customer_id", "string", "confidential"), _field("country", "string"), _field("is_cancellation", "boolean")]
    grain = {"kind": "row", "key_field_ids": ["lines.row_id"]}

    def metric(mid: str, agg: str, measure: str | None, filters: list[str], name: str, desc: str) -> dict[str, Any]:
        return {"id": mid, "display_name": name, "dataset_id": "lines", "aggregation": agg, "measure_field_id": measure, "grain": grain,
                "required_filter_ids": filters, "certification": "certified", "owner_ids": [OWNER["id"]], "description": desc}

    metrics = [
        metric("sales_amount", "sum", "lines.line_amount", ["sales_lines"], "Sales amount", "Quantity times unit price over sales lines; cancellation lines are excluded."),
        metric("cancellation_amount", "sum", "lines.line_amount", ["cancellation_lines"], "Cancellation amount", "The (negative) value of cancellation lines."),
        metric("units_sold", "sum", "lines.quantity", ["sales_lines"], "Units sold", "Units on sales lines; cancellation lines are excluded."),
        metric("invoice_lines", "count", None, [], "Invoice lines", "Every line in the data, sales and cancellations."),
        metric("distinct_invoices", "count_distinct", "lines.invoice_no", [], "Distinct invoices", "Distinct invoice numbers, cancellations included."),
        metric("customers", "count_distinct", "lines.customer_id", [], "Customers", "Distinct customers; lines without a customer id are not counted."),
    ]
    return {
        "lifecycle": "draft",
        "contract": {
            "id": "online-retail-ii", "tenant_id": "tenant-a", "domain": "retail", "version": "fixture-1", "owners": [OWNER],
            "datasets": [{"id": "lines", "display_name": "Invoice lines", "source_asset_id": "orii.lines", "physical_name": TABLE,
                          "description": "One row per invoice line, real UK retail transactions 2009 to 2011.", "owner_ids": [OWNER["id"]]}],
            "fields": fields,
            "entities": [{"id": "line", "dataset_id": "lines", "grain": grain, "owner_ids": [OWNER["id"]]}],
            "dimensions": [
                {"id": "country", "display_name": "Country", "dataset_id": "lines", "field_id": "lines.country", "entity_id": "line", "dimension_type": "categorical", "owner_ids": [OWNER["id"]]},
                {"id": "invoice_date", "display_name": "Invoice date", "dataset_id": "lines", "field_id": "lines.invoice_ts", "entity_id": "line", "dimension_type": "temporal", "owner_ids": [OWNER["id"]]},
            ],
            "filters": [
                {"id": "sales_lines", "dataset_id": "lines", "field_id": "lines.is_cancellation", "operator": "equals", "value_source": "literal", "literal_value": False},
                {"id": "cancellation_lines", "dataset_id": "lines", "field_id": "lines.is_cancellation", "operator": "equals", "value_source": "literal", "literal_value": True},
            ],
            "metrics": metrics, "joins": [],
            "policies": [{"id": f"policy_{m['id']}", "target_ids": [m["id"]], "classification": "internal", "allowed_purposes": ["analytics"],
                          "required_filter_ids": m["required_filter_ids"], "owner_ids": [OWNER["id"]]} for m in metrics],
        },
    }


def document() -> SemanticRegistryDocument:
    return SemanticRegistryDocument.model_validate(contract())


def keys(parquet: Path) -> dict[str, Any]:
    """Independent answer keys: statements over the source file that never touch the compiler."""
    con = duckdb.connect()
    src = f"read_parquet('{parquet.as_posix()}')"

    def one(sql: str) -> Any:
        return con.execute(sql).fetchone()[0]

    def by(sql: str) -> dict[str, Any]:
        return {str(k): v for k, v in con.execute(sql).fetchall()}

    sales = "NOT is_cancellation"
    return {
        "sales": one(f"SELECT SUM(line_amount) FROM {src} WHERE {sales}"),
        "cancellations": one(f"SELECT SUM(line_amount) FROM {src} WHERE is_cancellation"),
        "lines": one(f"SELECT COUNT(*) FROM {src}"),
        "invoices": one(f"SELECT COUNT(DISTINCT invoice_no) FROM {src}"),
        "customers": one(f"SELECT COUNT(DISTINCT customer_id) FROM {src}"),
        "units": one(f"SELECT SUM(quantity) FROM {src} WHERE {sales}"),
        "sales_by_country": by(f"SELECT country, SUM(line_amount) FROM {src} WHERE {sales} AND country IN ('United Kingdom','Germany','France') GROUP BY 1"),
        "sales_dec_2010": one(f"SELECT SUM(line_amount) FROM {src} WHERE {sales} AND invoice_ts >= TIMESTAMP '2010-12-01' AND invoice_ts < TIMESTAMP '2011-01-01'"),
        "sales_without_uk": one(f"SELECT SUM(line_amount) FROM {src} WHERE {sales} AND country <> 'United Kingdom'"),
        "sales_germany": one(f"SELECT SUM(line_amount) FROM {src} WHERE {sales} AND country = 'Germany'"),
        "cancellations_uk": one(f"SELECT SUM(line_amount) FROM {src} WHERE is_cancellation AND country = 'United Kingdom'"),
    }


def cases(k: dict[str, Any]) -> tuple[Case, ...]:
    a = lambda i, q, key, note="": Case(i, q, "answer", key, note)  # noqa: E731
    r = lambda i, q, note: Case(i, q, "refuse", None, note)  # noqa: E731
    return (
        a("R01", "What is our total sales amount?", k["sales"]),
        a("R02", "How much value was cancelled?", k["cancellations"]),
        a("R03", "How many invoice lines are there?", k["lines"]),
        a("R04", "How many invoices have we had?", k["invoices"]),
        a("R05", "How many customers do we have?", k["customers"]),
        a("R06", "How many units have we sold?", k["units"]),
        a("R07", "Show sales amount by country for the United Kingdom, Germany and France", k["sales_by_country"]),
        a("R08", "What were the sales in December 2010?", k["sales_dec_2010"]),
        a("R09", "What are the sales in Germany?", k["sales_germany"]),
        a("R10", "What is the cancellation amount in the United Kingdom?", k["cancellations_uk"]),
        r("R11", "What is the average invoice value?", "no average metric is certified"),
        r("R12", "What is our profit margin?", "cost is not in the data"),
        r("R13", "Which product sold the most?", "no product dimension is certified"),
        r("R14", "What were sales last week?", "the data ends in 2011; a relative period must not return zeros silently"),
        r("R15", "How are we doing?", "no metric or period named"),
        r("R16", "What is the total sales amount including VAT?", "tax is not in the data"),
        r("R17", "Show sales for customer 12346", "per-customer data is confidential and not a certified dimension"),
        r("R18", "Which customers returned the most goods?", "customer ranking is not certified"),
    )


def build(data_dir: Path) -> Dataset:
    parquet = Path(data_dir) / "online_retail_ii.parquet"
    return Dataset(name="Online Retail II (real UK retail transactions, CC BY 4.0, Chen 2012, doi 10.24432/C5CG6D)", document=document(), sources={TABLE: parquet},
                   cases=cases(keys(parquet)), certified_by_owner=False,
                   note="The contract is an evaluation fixture authored for this run: sales and cancellations are separate metrics, each with its required filter.")
