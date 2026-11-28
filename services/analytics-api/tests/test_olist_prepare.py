"""The Olist `payment_total` step: payments summed per order first, checked against plain Python, and the same step as warehouse SQL."""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest
import sqlglot
from reference_stack import olist_prepare as op

ORDERS = ("order_id,customer_id,order_status,order_purchase_timestamp,order_approved_at,order_delivered_carrier_date,order_delivered_customer_date,order_estimated_delivery_date\n"
          "o1,c1,delivered,2017-11-05 10:00:00,,,,\no2,c2,delivered,2017-11-20 09:00:00,,,,\no3,c3,canceled,2017-12-01 09:00:00,,,,\no4,c4,delivered,2018-01-03 09:00:00,,,,\n")
PAYMENTS = ("order_id,payment_sequential,payment_type,payment_installments,payment_value\no1,1,credit_card,1,60.00\no1,2,voucher,1,40.00\no2,1,boleto,1,25.50\n"
            "o3,1,boleto,1,10.00\nghost,1,boleto,1,99.00\n")  # o4 has no payment; "ghost" is not an order
ITEMS = "order_id,order_item_id,product_id,seller_id,shipping_limit_date,price,freight_value\no1,1,p1,s1,2017-11-06 10:00:00,70.00,5.00\no1,2,p2,s1,2017-11-06 10:00:00,30.00,5.00\n"


@pytest.fixture()
def csvs(tmp_path):
    d = tmp_path / "csv"
    d.mkdir()
    (d / op.ORDERS_CSV).write_text(ORDERS)
    (d / op.PAYMENTS_CSV).write_text(PAYMENTS)
    (d / op.ITEMS_CSV).write_text(ITEMS)
    return d


def test_an_order_with_two_payments_and_two_items_is_summed_once_not_repeated(csvs, tmp_path):
    src = op.prepare(csvs, tmp_path / "out")
    rows = dict(duckdb.connect().execute(f"SELECT order_id, payment_total FROM read_parquet('{src['olist_orders'].as_posix()}')").fetchall())
    assert rows["o1"] == Decimal("100.00")  # two payments of 60 and 40, two items: 100, not 200
    assert rows["o2"] == Decimal("25.50") and rows["o4"] is None  # an order with no payment stays in with no total


def test_every_check_agrees_with_plain_python_and_the_odd_rows_are_counted_not_hidden(csvs, tmp_path):
    checks = op.check(csvs, op.prepare(csvs, tmp_path / "out"))
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    facts = op._python_facts(csvs)
    assert facts["without_payments"] == 1 and facts["payment_orders_not_in_orders"] == 1  # o4, and the "ghost" payment
    assert facts["total_paid"] == Decimal("135.50")  # the ghost payment is excluded: 100 + 25.5 + 10


def test_a_wrong_total_is_caught(csvs, tmp_path):
    src = op.prepare(csvs, tmp_path / "out")
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{src['olist_orders'].as_posix()}')")
    con.execute("UPDATE t SET payment_total = payment_total * 2 WHERE order_id = 'o1'")
    con.execute(f"COPY t TO '{src['olist_orders'].as_posix()}' (FORMAT PARQUET)")
    assert not all(c.ok for c in op.check(csvs, src))


@pytest.mark.parametrize("engine", ["databricks", "snowflake"])
def test_the_warehouse_view_parses_and_is_text_only(engine):
    sql = op.warehouse_sql(engine, "cat", "sch")
    assert sql.startswith("CREATE VIEW") and sqlglot.parse_one(sql, read=engine) is not None and "SUM(payment_value)" in sql and "GROUP BY order_id" in sql


def test_the_warehouse_view_gives_the_same_totals_as_the_duckdb_step(csvs, tmp_path):
    src = op.prepare(csvs, tmp_path / "out")
    sql = sqlglot.transpile(op.warehouse_sql("databricks", "dev", "sch"), read="databricks", write="duckdb")[0]
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS dev")
    con.execute("CREATE SCHEMA dev.sch")
    con.execute(f"CREATE TABLE dev.sch.olist_orders_raw AS SELECT * FROM read_csv_auto('{(csvs / op.ORDERS_CSV).as_posix()}', header=true)")
    con.execute(f"CREATE TABLE dev.sch.olist_order_payments AS SELECT * FROM read_csv_auto('{(csvs / op.PAYMENTS_CSV).as_posix()}', header=true)")
    con.execute(sql)
    view = dict(con.execute("SELECT order_id, payment_total FROM dev.sch.olist_orders").fetchall())
    step = dict(con.execute(f"SELECT order_id, payment_total FROM read_parquet('{src['olist_orders'].as_posix()}')").fetchall())
    assert view == step


def test_bad_names_and_unknown_engines_are_refused():
    with pytest.raises(Exception):  # noqa: B017 - the loaders' identifier check
        op.warehouse_sql("databricks", "cat; DROP", "sch")
    with pytest.raises(ValueError):
        op.warehouse_sql("duckdb", "cat", "sch")
