"""Two independent answer keys (docs/design/SYNTHETIC_DATA_GENERATOR_SPEC.md section 7).

`ledger` reads the clean order table the generator built (one row per order, before any defect, amounts in integer cents) and applies the
named definitions. `recompute` reads only the Parquet the generator wrote and applies the same definitions with ordinary SQL and the cleaning
rules a certifier would have to choose (latest version per order, drop the test restaurant, USD only, and so on). The build fails if they
disagree. Neither uses the product.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from reference_stack.synthetic import reference as ref
from reference_stack.synthetic.config import GeneratorConfig
from reference_stack.synthetic.engine import connect, read_parquet

TEST_ID = ref.TEST_RESTAURANT[0]
_BASE = "regexp_replace(trim(lower(cuisine)), ' place$', '')"
CLEAN_CUISINE = f"(upper(substr({_BASE}, 1, 1)) || substr({_BASE}, 2))"  # every cuisine is one word

# name -> (unit, definition in words). The definitions are what a certified metric would have to say.
DEFINITIONS: dict[str, tuple[str, str]] = {
    "orders_distinct": ("count", "distinct orders, excluding the test restaurant"),
    "delivered_orders": ("count", "orders whose latest version has status delivered"),
    "cancelled_orders": ("count", "orders whose latest version has status cancelled"),
    "refunded_orders": ("count", "orders whose latest version has status refunded"),
    "partially_refunded_orders": ("count", "orders whose latest version has status partially_refunded"),
    "gross_value_usd_cents": ("cents", "sum of total_amount of each order's latest non-reversal version, USD only, excluding cancelled orders and test orders"),
    "refunds_usd_cents": ("cents", "minus the sum of total_amount over reversal rows, USD only"),
    "net_value_usd_cents": ("cents", "gross value minus refunds"),
    "tip_card_usd_cents": ("cents", "sum of tip_amount of the latest version, card payments only, USD, excluding cancelled orders (cash tips are not recorded)"),
    "reversal_rows": ("count", "rows with is_reversal true"),
    "naive_row_count": ("count", "count of every row, as a certification that ignored versions, reversals, duplicates and test orders would give"),
    "naive_sum_total_cents": ("cents", "sum of total_amount over every row and currency, as a naive certification would give"),
    "delivery_minutes_sum": ("minutes", "sum of delivery_minutes over each order's latest version, excluding null and the -1 placeholder"),
    "delivery_minutes_count": ("count", "number of orders counted in delivery_minutes_sum"),
    "day_boundary_orders": ("count", "orders whose UTC calendar day differs from their local calendar day"),
}
MAPS = {"orders_by_city": "distinct orders per city", "orders_by_month_local": "distinct orders per local month (YYYY-MM)",
        "orders_by_cuisine": "distinct orders per cleaned cuisine, excluding orphan restaurants"}
# Values the data itself can never show (the generator knows them, the output hides them). Ledger only.
HIDDEN = {"tip_true_all_methods_usd_cents": "tips really paid in USD on every payment method, including cash tips the data does not record"}


def ledger(con: duckdb.DuckDBPyConnection, cfg: GeneratorConfig) -> dict[str, Any]:
    """From the clean order table `ob` (built by `generate`). Amounts are integer cents."""
    d1 = cfg.on("D1")
    ft = "total" if d1 else "total - refund"  # what a row records as its total once the refund is in place or left to a reversal row
    one = lambda sql: int(con.execute(sql).fetchone()[0] or 0)  # noqa: E731
    live = "NOT is_test"
    values = {
        "orders_distinct": one(f"SELECT count(*) FROM ob WHERE {live}"),
        "delivered_orders": one(f"SELECT count(*) FROM ob WHERE {live} AND status = 'delivered'"),
        "cancelled_orders": one(f"SELECT count(*) FROM ob WHERE {live} AND status = 'cancelled'"),
        "refunded_orders": one(f"SELECT count(*) FROM ob WHERE {live} AND status = 'refunded'"),
        "partially_refunded_orders": one(f"SELECT count(*) FROM ob WHERE {live} AND status = 'partially_refunded'"),
        "gross_value_usd_cents": one(f"SELECT sum({ft}) FROM ob WHERE {live} AND status <> 'cancelled' AND currency = 'USD'"),
        "refunds_usd_cents": one(f"SELECT sum(refund) FROM ob WHERE {live} AND has_reversal AND currency = 'USD'"),
        "tip_card_usd_cents": one(f"SELECT sum(tip_rec) FROM ob WHERE {live} AND status <> 'cancelled' AND currency = 'USD' AND payment_method = 'card'"),
        "reversal_rows": one("SELECT count(*) FROM ob WHERE has_reversal"),
        "naive_row_count": one("SELECT count(*) + sum(CAST(is_late AS INT)) + sum(CAST(has_reversal AS INT)) + sum(CAST(is_dup AS INT)) FROM ob"),
        "naive_sum_total_cents": one(
            f"SELECT sum((CASE WHEN is_late THEN total ELSE {ft} END) + (CASE WHEN is_late THEN {ft} ELSE 0 END)"
            f" + (CASE WHEN has_reversal THEN -refund ELSE 0 END) + (CASE WHEN is_dup THEN {ft} ELSE 0 END)) FROM ob"),
        "delivery_minutes_sum": one(f"SELECT sum(delivery_true) FROM ob WHERE {live} AND delivery_true IS NOT NULL AND NOT delivery_sent"),
        "delivery_minutes_count": one(f"SELECT count(*) FROM ob WHERE {live} AND delivery_true IS NOT NULL AND NOT delivery_sent"),
        "day_boundary_orders": one(f"SELECT count(*) FROM ob WHERE {live} AND CAST(order_ts AS DATE) <> CAST(local_ts AS DATE)"),
    }
    values["net_value_usd_cents"] = values["gross_value_usd_cents"] - values["refunds_usd_cents"]
    maps = {
        "orders_by_city": dict(con.execute(f"SELECT city, count(*) FROM ob WHERE {live} GROUP BY 1 ORDER BY 1").fetchall()),
        "orders_by_month_local": dict(con.execute(f"SELECT strftime(local_ts, '%Y-%m'), count(*) FROM ob WHERE {live} GROUP BY 1 ORDER BY 1").fetchall()),
        "orders_by_cuisine": dict(con.execute(
            f"SELECT r.cuisine, count(*) FROM ob JOIN restaurants r ON r.restaurant_id = ob.true_restaurant_id WHERE {live} AND NOT ob.is_orphan GROUP BY 1 ORDER BY 1").fetchall()),
    }
    hidden = {"tip_true_all_methods_usd_cents": one(f"SELECT sum(tip_true) FROM ob WHERE {live} AND currency = 'USD'")}
    return {"values": values, "maps": {k: {a: int(b) for a, b in v.items()} for k, v in maps.items()}, "hidden": hidden}


def _scratch(parquet: Path) -> duckdb.DuckDBPyConnection:
    con = connect()
    con.execute("CREATE TABLE city_tz (city VARCHAR, tz VARCHAR)")
    con.executemany("INSERT INTO city_tz VALUES (?, ?)", [(c, tz) for c, _, tz, _ in ref.CITIES])
    con.execute(f"CREATE VIEW flat AS SELECT * FROM {read_parquet(parquet)}")
    return con


def recompute(out_dir: Path) -> dict[str, Any]:
    """From orders_flat.parquet only, with ordinary SQL and the cleaning rules written out."""
    con = _scratch(out_dir / "orders_flat.parquet")
    con.execute(f"""
        CREATE VIEW latest AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY order_id ORDER BY updated_at DESC, row_id DESC) AS rn
            FROM flat WHERE NOT is_reversal AND restaurant_id <> '{TEST_ID}'
        ) WHERE rn = 1
    """)
    cents = lambda expr: f"CAST(round(coalesce({expr}, 0) * 100) AS BIGINT)"  # noqa: E731
    one = lambda sql: int(con.execute(sql).fetchone()[0] or 0)  # noqa: E731
    local = "timezone(t.tz, l.order_ts AT TIME ZONE 'UTC')"
    values = {
        "orders_distinct": one("SELECT count(*) FROM latest"),
        "delivered_orders": one("SELECT count(*) FROM latest WHERE status = 'delivered'"),
        "cancelled_orders": one("SELECT count(*) FROM latest WHERE status = 'cancelled'"),
        "refunded_orders": one("SELECT count(*) FROM latest WHERE status = 'refunded'"),
        "partially_refunded_orders": one("SELECT count(*) FROM latest WHERE status = 'partially_refunded'"),
        "gross_value_usd_cents": one(f"SELECT {cents('sum(total_amount)')} FROM latest WHERE status <> 'cancelled' AND currency = 'USD'"),
        "refunds_usd_cents": one(f"SELECT -{cents('sum(total_amount)')} FROM flat WHERE is_reversal AND currency = 'USD' AND restaurant_id <> '{TEST_ID}'"),
        "tip_card_usd_cents": one(f"SELECT {cents('sum(tip_amount)')} FROM latest WHERE payment_method = 'card' AND status <> 'cancelled' AND currency = 'USD'"),
        "reversal_rows": one("SELECT count(*) FROM flat WHERE is_reversal"),
        "naive_row_count": one("SELECT count(*) FROM flat"),
        "naive_sum_total_cents": one(f"SELECT {cents('sum(total_amount)')} FROM flat"),
        "delivery_minutes_sum": one("SELECT sum(delivery_minutes) FROM latest WHERE delivery_minutes >= 0"),
        "delivery_minutes_count": one("SELECT count(*) FROM latest WHERE delivery_minutes >= 0"),
        "day_boundary_orders": one(f"SELECT count(*) FROM latest l JOIN city_tz t USING (city) WHERE CAST(l.order_ts AS DATE) <> CAST({local} AS DATE)"),
    }
    values["net_value_usd_cents"] = values["gross_value_usd_cents"] - values["refunds_usd_cents"]
    clean = CLEAN_CUISINE
    maps = {
        "orders_by_city": dict(con.execute("SELECT city, count(*) FROM latest GROUP BY 1 ORDER BY 1").fetchall()),
        "orders_by_month_local": dict(con.execute(f"SELECT strftime({local}, '%Y-%m'), count(*) FROM latest l JOIN city_tz t USING (city) GROUP BY 1 ORDER BY 1").fetchall()),
        "orders_by_cuisine": dict(con.execute(f"SELECT {clean}, count(*) FROM latest WHERE cuisine IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()),
    }
    return {"values": values, "maps": {k: {a: int(b) for a, b in v.items()} for k, v in maps.items()}}


def disagreements(truth: dict[str, Any], again: dict[str, Any]) -> list[str]:
    out = [f"{k}: ledger {truth['values'][k]} != recomputed {again['values'][k]}" for k in truth["values"] if truth["values"][k] != again["values"].get(k)]
    for name in truth["maps"]:
        if truth["maps"][name] != again["maps"].get(name):
            out.append(f"{name}: maps differ")
    return out


def observed_defects(out_dir: Path) -> dict[str, int]:
    """How many times each defect is visible in the output, from the Parquet alone. A defect switched off reads zero."""
    con = _scratch(out_dir / "orders_flat.parquet")
    one = lambda sql: int(con.execute(sql).fetchone()[0] or 0)  # noqa: E731
    clean = CLEAN_CUISINE
    return {
        "D1": one("SELECT count(*) FROM flat WHERE is_reversal"),
        "D2": one("SELECT count(*) FROM flat WHERE NOT is_reversal AND payment_method = 'cash' AND status <> 'cancelled' AND tip_amount = 0")
        if one("SELECT count(*) FROM flat WHERE payment_method = 'cash' AND tip_amount > 0") == 0 else 0,
        "D3": one("SELECT count(*) FROM (SELECT order_id FROM flat WHERE NOT is_reversal GROUP BY 1 HAVING count(DISTINCT updated_at) > 1)"),
        "D4": one("SELECT count(*) - count(DISTINCT (order_id, updated_at, is_reversal, status, total_amount)) FROM flat"),
        "D5": one("SELECT count(*) FROM flat f JOIN city_tz t USING (city) WHERE CAST(f.order_ts AS DATE) <> CAST(timezone(t.tz, f.order_ts AT TIME ZONE 'UTC') AS DATE)"),
        "D6": one("SELECT count(DISTINCT currency) - 1 FROM flat"),
        "D7": one("SELECT count(*) FROM flat WHERE delivery_minutes = -1 OR prep_minutes = -1"),
        "D8": one(f"SELECT count(DISTINCT cuisine) - count(DISTINCT {clean}) FROM flat WHERE cuisine IS NOT NULL"),
        "D9": one(f"SELECT count(*) FROM flat WHERE restaurant_id = '{TEST_ID}'"),
        "D10": one(f"SELECT count(*) FROM flat WHERE restaurant_name IS NULL AND restaurant_id <> '{TEST_ID}'"),
    }


__all__ = ["DEFINITIONS", "HIDDEN", "MAPS", "disagreements", "ledger", "observed_defects", "recompute"]
