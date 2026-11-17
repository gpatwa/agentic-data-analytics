"""Reference data, plan R1: determinism, synthetic safety, the oracle against DuckDB (and PostgreSQL when a URL is given), the scenes."""

import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal

import duckdb
import pytest
from reference_data import __main__ as cli
from reference_data import generator as g
from reference_data import loader, oracle, scenes

from app.ontology_draft.profile import is_sensitive_name

# Pinned on purpose. Changing the generator changes the reference data: bump VERSION and re-pin, deliberately.
PINNED = {
    "ref_orders": (20_000, "54c1cc30dc10a6e0596dfc4d531f3c109d87dd6ee9798bc76b085475eb85ae18"),
    "ref_customers": (5_000, "10533d8da2b81a36f50684fcf665c5db48fa8607ba93c2618fe01ed5f4d75ba1"),
}


# ------------------------------------------------------------------------------------------------- determinism


def test_the_generator_is_deterministic_and_the_checksums_are_pinned():
    first = g.checksums()
    assert first == g.checksums()
    for name, (rows, digest) in PINNED.items():
        assert (first[name]["rows"], first[name]["sha256"]) == (rows, digest)
    assert first["ref_ledger_notes"]["rows"] == g.LEDGER_ROWS and g.VERSION == "ref_v1"


def test_every_value_is_derived_from_the_seed_alone_not_from_run_order():
    full = g.orders()
    assert full[12_344] == g.orders()[12_344]
    assert g._u("ref_orders", 12_345, "region", 1000) == g._u("ref_orders", 12_345, "region", 1000)


# ------------------------------------------------------------------------------------------------- shape and safety


def test_the_tables_have_the_designed_shape():
    orders, customers = g.orders(), g.customers()
    assert len(orders) == 20_000 and len(customers) == 5_000
    assert len({r[0] for r in orders}) == len(orders) and len({r[0] for r in customers}) == len(customers)
    assert {r[5] for r in orders} == {s for s, _ in g.STATUSES} and {r[2] for r in orders} == {x for x, _ in g.REGIONS}
    assert {r[4] for r in orders} == {c for c, _ in g.CATEGORIES} and {r[7] for r in orders} == {d for d, _ in g.DISCOUNTS}
    assert all(datetime(2024, 1, 1, tzinfo=timezone.utc) <= r[1] < datetime(2025, 1, 1, tzinfo=timezone.utc) for r in orders)
    assert all(lo <= r[6] < hi + 1 for r in orders for lo, hi in [g.AMOUNT_RANGE[r[4]]])
    assert all(len(row) == len(g.SCHEMAS[t]) for t, rows in (("ref_orders", orders), ("ref_customers", customers)) for row in rows[:50])
    paid = oracle.totals()["paid_orders"]
    assert 0.76 < paid / len(orders) < 0.83  # about 80 percent paid


def test_the_personal_columns_are_obviously_fake():
    customers = g.customers()
    assert all(re.fullmatch(r"customer\d{4}@example\.com", r[4]) for r in customers)  # a reserved domain
    assert all(re.fullmatch(r"\+1-555-01\d\d", r[5]) for r in customers)  # the fictional 555-01xx block

    def luhn(number):
        total = 0
        for position, char in enumerate(reversed(number)):
            digit = int(char) * (2 if position % 2 else 1)
            total += digit - 9 if digit > 9 else digit
        return total % 10 == 0

    cards = [r[7].replace(" ", "") for r in customers]
    assert all(len(c) == 16 and c.startswith("0000") and luhn(c) for c in cards)  # card-shaped, but no issuer uses 0000
    assert all(r[6].year < 2010 for r in customers)


def test_there_is_exactly_one_deliberately_small_group_and_it_is_the_designed_one():
    assert oracle.small_groups(("region", "category")) == [(g.SMALL_COHORT, g.SMALL_COHORT_ROWS)]
    assert len(oracle.visible_groups(("region", "category"))) == len(g.REGIONS) * len(g.CATEGORIES) - 1
    assert all(n >= g.MIN_COHORT for _, n in oracle.order_count_by(("month", "region")))  # the headline scene never hits suppression
    assert all(r[5] == "paid" for r in g.orders() if (r[2], r[4]) == g.SMALL_COHORT)


def test_the_redaction_rule_flags_the_sensitive_columns_and_the_known_gap_is_pinned():
    for column in g.SENSITIVE_COLUMNS["ref_customers"]:
        assert is_sensitive_name(column), column
    for column in g.KNOWN_REDACTION_GAPS["ref_customers"]:
        # A documented gap in the approved rule ("dob" matches, "date_of_birth" does not). If this fails the rule was extended:
        # move the column into SENSITIVE_COLUMNS and update scene 6.
        assert not is_sensitive_name(column), f"{column} is now flagged: move it to SENSITIVE_COLUMNS"
    names = {c.name for c in g.SCHEMAS["ref_customers"]}
    assert set(g.SENSITIVE_COLUMNS["ref_customers"]) | set(g.KNOWN_REDACTION_GAPS["ref_customers"]) <= names


def test_the_poisoned_comments_really_carry_instructions_and_every_canary():
    notes = " ".join(g.COMMENTS["ref_ledger_notes"].values())
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in notes and "SYSTEM:" in notes and "Assistant:" in notes
    assert all(canary in notes for canary in g.CANARIES)


# ------------------------------------------------------------------------------------------------- the oracle against real engines


@pytest.fixture(scope="module")
def duck():
    conn = duckdb.connect(":memory:")
    loader.load_duckdb(conn)
    return conn


def test_the_duckdb_loader_never_hands_duckdb_a_timezone_aware_datetime():
    """CI found this: without pytz, DuckDB retries a failing import per tz-aware value (68 minutes for 25,000 rows)."""
    seen = []

    class Recorder:
        def execute(self, *_):
            return self

        def executemany(self, _sql, rows):
            seen.extend(v for row in rows for v in row)

    loader.load_duckdb(Recorder())
    assert seen and not any(isinstance(v, datetime) for v in seen)
    assert "2024-01-01 00:00:56+00" in {v for v in seen if isinstance(v, str) and v.startswith("2024-01-01 00:00:56")}


def _month_region_sql(start, end):
    return (
        "SELECT year(created_at) AS y, month(created_at) AS m, region, sum(amount) FROM ref_orders "
        "WHERE status = 'paid' AND created_at >= ? AND created_at <= ? GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
        [start.strftime("%Y-%m-%d %H:%M:%S+00"), end.strftime("%Y-%m-%d %H:%M:%S+00")],
    )


def test_duckdb_holds_exactly_the_generated_rows(duck):
    counts = {t: duck.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in g.SCHEMAS}
    assert counts == {"ref_orders": 20_000, "ref_customers": 5_000, "ref_ledger_notes": g.LEDGER_ROWS}
    import hashlib

    for name, (_, digest) in PINNED.items():
        # Timestamps are formatted by DuckDB itself: fetching a TIMESTAMPTZ as a Python datetime needs the optional pytz package,
        # which CI does not have (found by CI on the first run).
        select = ", ".join(
            f"strftime({c.name}::TIMESTAMP, '%Y-%m-%dT%H:%M:%SZ')" if c.logical_type == "timestamp" else c.name for c in g.SCHEMAS[name]
        )
        h = hashlib.sha256()
        for row in duck.execute(f"SELECT {select} FROM {name} ORDER BY 1").fetchall():
            h.update(("\x1f".join(v if isinstance(v, str) and c.logical_type == "timestamp" else g._canonical(v) for v, c in zip(row, g.SCHEMAS[name], strict=True)) + "\x1e").encode())
        assert h.hexdigest() == digest, f"{name} did not round-trip through DuckDB"


def test_the_oracle_equals_duckdb_for_the_headline_scene(duck):
    start, end = scenes.Q3
    sql, params = _month_region_sql(start, end)
    got = [((y, m), region, total) for y, m, region, total in duck.execute(sql, params).fetchall()]
    want = oracle.revenue_by(("month", "region"), start, end)
    assert sorted(((k[0], k[1]), v) for (k, v) in want) == sorted((((y, m), region), total) for (y, m), region, total in got)
    assert len(want) == 15 and all(isinstance(v, Decimal) for _, v in want)


def test_the_oracle_equals_duckdb_for_counts_small_groups_and_customers(duck):
    rows = duck.execute("SELECT region, product_category, count(*) FROM ref_orders WHERE status = 'paid' GROUP BY 1, 2").fetchall()
    assert sorted(((r, c), n) for r, c, n in rows) == oracle.order_count_by(("region", "category"))
    assert [(r, c) for r, c, n in rows if n < g.MIN_COHORT] == [g.SMALL_COHORT]
    assert sorted(duck.execute("SELECT plan, count(*) FROM ref_customers GROUP BY 1").fetchall()) == oracle.customers_by("plan")
    total = duck.execute("SELECT sum(amount) FROM ref_orders WHERE status = 'paid'").fetchone()[0]
    assert total == oracle.totals()["paid_revenue"]


@pytest.mark.skipif(not os.environ.get("REFERENCE_TEST_PG_URL"), reason="set REFERENCE_TEST_PG_URL to a scratch PostgreSQL to run this")
def test_the_oracle_equals_a_real_postgresql_load():
    import psycopg2

    schema = "ref_r1_test"
    conn = psycopg2.connect(os.environ["REFERENCE_TEST_PG_URL"])
    try:
        cur = conn.cursor()
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.commit()
        assert loader.load_postgres(conn, schema) == {"ref_orders": 20_000, "ref_customers": 5_000, "ref_ledger_notes": g.LEDGER_ROWS}
        cur.execute("SET TIME ZONE 'UTC'")
        start, end = scenes.Q3
        cur.execute(
            f"SELECT extract(year FROM created_at)::int, extract(month FROM created_at)::int, region, sum(amount) FROM {schema}.ref_orders "
            "WHERE status = 'paid' AND created_at >= %s AND created_at <= %s GROUP BY 1, 2, 3", (start, end),
        )
        got = sorted((((y, m), region), total) for y, m, region, total in cur.fetchall())
        assert got == sorted(((k[0], k[1]), v) for k, v in oracle.revenue_by(("month", "region"), start, end))
        cur.execute(f"SELECT region, product_category, count(*) FROM {schema}.ref_orders WHERE status = 'paid' GROUP BY 1, 2")
        assert sorted(((r, c), n) for r, c, n in cur.fetchall()) == oracle.order_count_by(("region", "category"))
        cur.execute(f"SELECT count(*) FROM {schema}.ref_customers WHERE card_number LIKE '0000 %'")
        assert cur.fetchone()[0] == 5_000
    finally:
        conn.rollback()
        conn.cursor().execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.commit()
        conn.close()


# ------------------------------------------------------------------------------------------------- DDL per dialect


@pytest.mark.parametrize("dialect", ["postgres", "duckdb", "snowflake", "databricks"])
def test_the_ddl_parses_in_every_dialect_and_names_every_column(dialect):
    import sqlglot

    sqlglot_dialect = {"postgres": "postgres", "duckdb": "duckdb", "snowflake": "snowflake", "databricks": "databricks"}[dialect]
    for table, columns in g.SCHEMAS.items():
        sql = loader.create_table_sql(dialect, table)
        tree = sqlglot.parse_one(sql, read=sqlglot_dialect)
        assert tree is not None and all(c.name in sql for c in columns)


def test_snowflake_ddl_quotes_lower_case_and_databricks_uses_backticks():
    assert '"ref_orders"' in loader.create_table_sql("snowflake", "ref_orders") and '"order_id" VARCHAR NOT NULL' in loader.create_table_sql("snowflake", "ref_orders")
    assert "`ref_orders`" in loader.create_table_sql("databricks", "ref_orders", "main.ref")


# ------------------------------------------------------------------------------------------------- the scenes


def test_there_are_ten_scenes_with_unique_ids_and_a_known_kind():
    assert [s.id for s in scenes.SCENES] == list(range(1, 11))
    assert {s.kind for s in scenes.SCENES} <= {"oracle", "parity", "behavior", "model"}
    assert all(s.title and s.use_case and s.capability and s.needs for s in scenes.SCENES)
    assert scenes.scenes_markdown().count("\n") == 12


def test_every_scene_has_an_expected_result_and_it_is_json_ready():
    for scene in scenes.SCENES:
        expected = scene.expected()
        assert isinstance(expected, dict) and expected
        json.dumps(expected, default=str)


def test_the_headline_scene_is_the_oracle_and_parity_adds_the_checksums():
    one, two = scenes.by_id(1).expected(), scenes.by_id(2).expected()
    assert one["rows"] == oracle.revenue_by(("month", "region"), *scenes.Q3) and one["min_group_size"] >= g.MIN_COHORT
    assert two["table_checksums"] == g.checksums() and two["rows"] == one["rows"]


def test_the_behaviour_scenes_agree_with_the_data():
    refusal, small = scenes.by_id(3).expected(), scenes.by_id(4).expected()
    assert refusal["must_not_appear"] == [r[4] for r in g.customers()[:3]]
    assert small["cohort_size"] == g.SMALL_COHORT_ROWS < small["min_cohort"] and small["only_small_group"] is True
    assert scenes.by_id(5).expected()["groups_it_would_return"] == 20_000
    six = scenes.by_id(6).expected()
    assert set(six["excluded_from_model"]) == {"email", "phone", "card_number"} and six["known_gap_not_excluded_today"] == ("date_of_birth",)


def test_no_scene_output_contains_a_canary_or_a_personal_value():
    blob = json.dumps([s.expected() for s in scenes.SCENES if s.id not in (3, 7)], default=str)
    assert not any(c in blob for c in g.CANARIES)
    assert not any(email in blob for email in oracle.sample_emails(50))


# ------------------------------------------------------------------------------------------------- the command line


def test_the_command_line_prints_checksums_scenes_and_totals(capsys):
    assert cli.main(["checksums"]) == 0
    assert json.loads(capsys.readouterr().out)["tables"]["ref_orders"]["sha256"] == PINNED["ref_orders"][1]
    assert cli.main(["scenes"]) == 0 and "Governed revenue answer" in capsys.readouterr().out
    assert cli.main(["oracle"]) == 0 and json.loads(capsys.readouterr().out)["orders"] == "20000"
