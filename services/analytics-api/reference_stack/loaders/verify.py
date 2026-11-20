"""Portable verification queries: the same definitions the generator's answer keys use, written for each engine's SQL.

The logic is checked offline by running the DuckDB flavour against the generator's own Parquet and comparing with `truth.json`; the
Databricks and Snowflake flavours are parsed with sqlglot but are not run anywhere until G5 is approved.
"""

from __future__ import annotations

from reference_stack.loaders.engines import Dialect
from reference_stack.synthetic import reference as ref

TEST_ID = ref.TEST_RESTAURANT[0]


def _cents(expr: str) -> str:
    return f"CAST(ROUND(COALESCE({expr}, 0) * 100) AS BIGINT)"


def _tz_table() -> str:
    rows = ", ".join(f"('{c}', '{tz}')" for c, _, tz, _ in ref.CITIES)
    return f"(VALUES {rows}) AS t(city, tz)"


def _latest(tbl: str) -> str:
    return (f"WITH latest AS (SELECT * FROM (SELECT f.*, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY updated_at DESC, row_id DESC) AS rn "
            f"FROM {tbl} f WHERE NOT is_reversal AND restaurant_id <> '{TEST_ID}') x WHERE rn = 1) ")


def food_delivery_values(d: Dialect, tbl: str) -> dict[str, str]:
    L = _latest(tbl)  # noqa: N806
    local = d.local("l.order_ts", "t.tz")
    q = {
        "orders_distinct": f"{L}SELECT COUNT(*) FROM latest",
        "delivered_orders": f"{L}SELECT COUNT(*) FROM latest WHERE status = 'delivered'",
        "cancelled_orders": f"{L}SELECT COUNT(*) FROM latest WHERE status = 'cancelled'",
        "refunded_orders": f"{L}SELECT COUNT(*) FROM latest WHERE status = 'refunded'",
        "partially_refunded_orders": f"{L}SELECT COUNT(*) FROM latest WHERE status = 'partially_refunded'",
        "gross_value_usd_cents": f"{L}SELECT {_cents('SUM(total_amount)')} FROM latest WHERE status <> 'cancelled' AND currency = 'USD'",
        "refunds_usd_cents": f"SELECT -{_cents('SUM(total_amount)')} FROM {tbl} WHERE is_reversal AND currency = 'USD' AND restaurant_id <> '{TEST_ID}'",
        "tip_card_usd_cents": f"{L}SELECT {_cents('SUM(tip_amount)')} FROM latest WHERE payment_method = 'card' AND status <> 'cancelled' AND currency = 'USD'",
        "reversal_rows": f"SELECT COUNT(*) FROM {tbl} WHERE is_reversal",
        "naive_row_count": f"SELECT COUNT(*) FROM {tbl}",
        "naive_sum_total_cents": f"SELECT {_cents('SUM(total_amount)')} FROM {tbl}",
        "delivery_minutes_sum": f"{L}SELECT SUM(delivery_minutes) FROM latest WHERE delivery_minutes >= 0",
        "delivery_minutes_count": f"{L}SELECT COUNT(*) FROM latest WHERE delivery_minutes >= 0",
        "day_boundary_orders": f"{L}SELECT COUNT(*) FROM latest l JOIN {_tz_table()} ON l.city = t.city WHERE CAST(l.order_ts AS DATE) <> CAST({local} AS DATE)",
    }
    return q  # net value is derived by the caller (gross minus refunds): a query cannot carry two statements with their own clauses


def food_delivery_maps(d: Dialect, tbl: str) -> dict[str, str]:
    L = _latest(tbl)  # noqa: N806
    local = d.local("l.order_ts", "t.tz")
    base = "REGEXP_REPLACE(TRIM(LOWER(cuisine)), ' place$', '')"
    clean = f"(UPPER(SUBSTR({base}, 1, 1)) || SUBSTR({base}, 2))"
    return {
        "orders_by_city": f"{L}SELECT city, COUNT(*) FROM latest GROUP BY city",
        "orders_by_month_local": f"{L}SELECT {d.month(local)}, COUNT(*) FROM latest l JOIN {_tz_table()} ON l.city = t.city GROUP BY 1",
        "orders_by_cuisine": f"{L}SELECT {clean}, COUNT(*) FROM latest WHERE cuisine IS NOT NULL GROUP BY 1",
    }


def retail_values(tbl: str) -> dict[str, str]:
    return {
        "line_count": f"SELECT COUNT(*) FROM {tbl}",
        "invoice_count": f"SELECT COUNT(DISTINCT invoice_no) FROM {tbl}",
        "customer_count": f"SELECT COUNT(DISTINCT customer_id) FROM {tbl} WHERE customer_id IS NOT NULL",
        "lines_without_customer": f"SELECT COUNT(*) FROM {tbl} WHERE customer_id IS NULL",
        "cancellation_lines": f"SELECT COUNT(*) FROM {tbl} WHERE is_cancellation",
        "sales_lines_value_cents": f"SELECT {_cents('SUM(line_amount)')} FROM {tbl} WHERE NOT is_cancellation",
        "cancellation_value_cents": f"SELECT {_cents('SUM(line_amount)')} FROM {tbl} WHERE is_cancellation",
        "naive_net_value_cents": f"SELECT {_cents('SUM(line_amount)')} FROM {tbl}",
        "quantity_total": f"SELECT COALESCE(SUM(quantity), 0) FROM {tbl}",
    }


def retail_maps(tbl: str) -> dict[str, str]:
    return {"lines_by_country": f"SELECT country, COUNT(*) FROM {tbl} GROUP BY country"}
