"""The step the certified `olist-commerce-v1` contract assumes but nothing performed: an `olist_orders` table that carries `payment_total`.

The raw Olist `orders` table has no `payment_total`. The contract's metric `delivered_revenue` is the sum of `orders.payment_total` over delivered
orders, and `tests/fixtures/olist/README.md` fixes the rule: payments are summed **per order first**, never joined to a table with more rows
per order (an order with two payments and two items would count twice, BRL 200 for a BRL 100 order).

Two forms of the same step:

- `prepare(csv_dir, out_dir)` writes `olist_orders.parquet` and `olist_order_items.parquet` for DuckDB, and `check(...)` verifies the result against numbers
  computed in plain Python straight from the CSVs (never through DuckDB or the product).
- `warehouse_sql(...)` returns the `CREATE VIEW` for Databricks or Snowflake over the raw tables a loader put there. It is a dry-run text: nothing here
  connects to a warehouse, and creating it live is a step that needs its own approval.

What the check reports, not hides: orders with no payment row get `payment_total` NULL (they add nothing to a sum; they are counted); payment rows whose
order is not in the orders table are counted and excluded; an order's `payment_total` is the exact sum of its `payment_value` rows.

Licence: Olist is reported as CC BY-NC-SA 4.0 (non-commercial). Internal use only until the licence is checked.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb

from reference_stack.loaders.engines import dialect, ident

ORDERS_CSV, PAYMENTS_CSV, ITEMS_CSV = "olist_orders_dataset.csv", "olist_order_payments_dataset.csv", "olist_order_items_dataset.csv"


@dataclass(frozen=True)
class Check:
    name: str
    expected: object
    got: object

    @property
    def ok(self) -> bool:
        return self.expected == self.got


def prepare(csv_dir: Path, out_dir: Path) -> dict[str, Path]:
    """Write the two tables the contract names. `payment_total` is the sum of an order's payments."""
    csv_dir, out_dir = Path(csv_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    orders, items = out_dir / "olist_orders.parquet", out_dir / "olist_order_items.parquet"
    src = lambda name: f"read_csv_auto('{(csv_dir / name).as_posix()}', header=true, sample_size=-1)"  # noqa: E731
    con.execute(f"""COPY (SELECT o.order_id, o.customer_id, o.order_status, CAST(o.order_purchase_timestamp AS TIMESTAMP) AS order_purchase_timestamp,
        CAST(p.payment_total AS DECIMAL(14,2)) AS payment_total
        FROM {src(ORDERS_CSV)} o
        LEFT JOIN (SELECT order_id, SUM(payment_value) AS payment_total FROM {src(PAYMENTS_CSV)} GROUP BY 1) p USING (order_id)) TO '{orders.as_posix()}' (FORMAT PARQUET)""")
    con.execute(f"""COPY (SELECT order_id, order_item_id, product_id, seller_id, CAST(price AS DECIMAL(12,2)) AS price, CAST(freight_value AS DECIMAL(12,2)) AS freight_value
        FROM {src(ITEMS_CSV)}) TO '{items.as_posix()}' (FORMAT PARQUET)""")
    return {"olist_orders": orders, "olist_order_items": items}


def _python_facts(csv_dir: Path) -> dict[str, object]:
    pay: dict[str, Decimal] = defaultdict(Decimal)
    with open(csv_dir / PAYMENTS_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pay[row["order_id"]] += Decimal(row["payment_value"])
    order_ids: list[str] = []
    with open(csv_dir / ORDERS_CSV, newline="", encoding="utf-8") as fh:
        order_ids = [row["order_id"] for row in csv.DictReader(fh)]
    known = set(order_ids)
    return {
        "orders": len(order_ids), "distinct_orders": len(known), "with_payments": sum(1 for o in known if o in pay), "without_payments": sum(1 for o in known if o not in pay),
        "payment_orders_not_in_orders": sum(1 for o in pay if o not in known), "total_paid": sum((v for o, v in pay.items() if o in known), Decimal(0)),
    }


def check(csv_dir: Path, sources: dict[str, Path]) -> list[Check]:
    """The prepared table against plain-Python numbers from the CSVs."""
    facts = _python_facts(Path(csv_dir))
    con = duckdb.connect()
    q = lambda sql: con.execute(sql).fetchone()  # noqa: E731
    src = f"read_parquet('{sources['olist_orders'].as_posix()}')"
    rows, distinct, with_pay, total = (
        q(f"SELECT COUNT(*) FROM {src}")[0], q(f"SELECT COUNT(DISTINCT order_id) FROM {src}")[0], q(f"SELECT COUNT(payment_total) FROM {src}")[0],
        Decimal(str(q(f"SELECT COALESCE(SUM(payment_total), 0) FROM {src}")[0])),
    )
    return [
        Check("rows equal the orders in the CSV", facts["orders"], rows),
        Check("one row per order", facts["distinct_orders"], distinct),
        Check("orders with a payment", facts["with_payments"], with_pay),
        Check("total of payment_total equals the total paid on known orders", facts["total_paid"], total),
        Check("orders without a payment stay in, with no total", facts["without_payments"], rows - with_pay),
    ]


def warehouse_sql(engine: str, catalog: str, schema: str, *, raw_orders: str = "olist_orders_raw", raw_payments: str = "olist_order_payments", target: str = "olist_orders") -> str:
    """A `CREATE VIEW` for Databricks or Snowflake over the raw tables a loader put there. Text only: nothing is executed."""
    for name in (catalog, schema, raw_orders, raw_payments, target):
        ident(name)
    d = dialect(engine)
    if engine not in ("databricks", "snowflake"):
        raise ValueError("engine must be databricks or snowflake")
    return (
        f"CREATE VIEW {d.qn(catalog, schema, target)} AS SELECT o.order_id, o.customer_id, o.order_status, CAST(o.order_purchase_timestamp AS TIMESTAMP) AS order_purchase_timestamp, "
        f"CAST(p.payment_total AS DECIMAL(14,2)) AS payment_total FROM {d.qn(catalog, schema, raw_orders)} o "
        f"LEFT JOIN (SELECT order_id, SUM(payment_value) AS payment_total FROM {d.qn(catalog, schema, raw_payments)} GROUP BY order_id) p ON o.order_id = p.order_id"
    )
