"""Auto-ontology drafter, milestone 4a: introspection, profiling and EXPLAIN on a real PostgreSQL.

These tests need a PostgreSQL that already holds the pilot's `infra/terraform/azure-analytics-pilot/bootstrap.sql` tables and its
`analytics_readonly` role. They skip unless both URLs are set, so CI without PostgreSQL is unaffected:

    ONTOLOGY_TEST_PG_ADMIN_URL=postgresql+psycopg2://postgres@127.0.0.1:54329/analytics_warehouse
    ONTOLOGY_TEST_PG_READONLY_URL=postgresql+psycopg2://analytics_readonly:<password>@127.0.0.1:54329/analytics_warehouse

The tests create a `shop` schema and a `ro_narrow` role, and drop both afterwards. They never touch `public` data.
"""

import os
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from app.compiler.postgres import CompiledQuery
from app.execution.postgres_gateway import PostgresGateway
from app.ontology_draft import (
    PROMPT_VERSION,
    DraftContext,
    ProfileLimits,
    draft_ontology,
    introspect_engine,
    profile_engine,
)
from app.ontology_draft.explain import explain_probe
from app.ontology_draft.profile import session_guards

ADMIN_URL = os.environ.get("ONTOLOGY_TEST_PG_ADMIN_URL")
READONLY_URL = os.environ.get("ONTOLOGY_TEST_PG_READONLY_URL")
pytestmark = pytest.mark.skipif(not (ADMIN_URL and READONLY_URL), reason="needs a local PostgreSQL with the pilot tables")

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

SHOP = """
DROP SCHEMA IF EXISTS shop CASCADE;
CREATE SCHEMA shop;
CREATE TYPE shop.order_kind AS ENUM ('retail', 'wholesale');
CREATE TABLE shop.customers (id text PRIMARY KEY, region text NOT NULL, email text NOT NULL);
COMMENT ON TABLE shop.customers IS 'One row per customer';
COMMENT ON COLUMN shop.customers.region IS 'Sales region';
CREATE TABLE shop.orders (
  id text PRIMARY KEY,
  customer_id text NOT NULL REFERENCES shop.customers(id),
  total numeric(12, 2) NOT NULL,
  placed_at timestamptz NOT NULL,
  kind shop.order_kind NOT NULL,
  tags text[],
  extra jsonb
);
CREATE VIEW shop.orders_view AS SELECT id, total FROM shop.orders;
INSERT INTO shop.customers SELECT 'c' || i, (ARRAY['north','south','east'])[1 + i % 3], 'p' || i || '@example.com'
  FROM generate_series(1, 30) AS i;
INSERT INTO shop.orders SELECT 'o' || i, 'c' || (1 + i % 30), i, TIMESTAMPTZ '2026-01-01' + make_interval(days => i % 90),
  'retail', ARRAY['a'], '{}' FROM generate_series(1, 90) AS i;
DROP ROLE IF EXISTS ro_narrow;
CREATE ROLE ro_narrow LOGIN PASSWORD 'narrow-throwaway';
GRANT CONNECT ON DATABASE analytics_warehouse TO ro_narrow;
GRANT USAGE ON SCHEMA public TO ro_narrow;
GRANT SELECT ON public.demo_sales_orders TO ro_narrow;
GRANT USAGE ON SCHEMA shop TO analytics_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA shop TO analytics_readonly;
"""


@pytest.fixture(scope="module")
def admin():
    engine = sa.create_engine(ADMIN_URL)
    with engine.begin() as db:
        for statement in [s for s in SHOP.split(";\n") if s.strip()]:
            db.execute(sa.text(statement))
    yield engine
    with engine.begin() as db:
        db.execute(sa.text("DROP SCHEMA IF EXISTS shop CASCADE"))
        db.execute(sa.text("REVOKE ALL ON DATABASE analytics_warehouse FROM ro_narrow"))
        db.execute(sa.text("REVOKE ALL ON SCHEMA public FROM ro_narrow"))
        db.execute(sa.text("REVOKE ALL ON public.demo_sales_orders FROM ro_narrow"))
        db.execute(sa.text("DROP ROLE IF EXISTS ro_narrow"))
    engine.dispose()


@pytest.fixture(scope="module")
def readonly(admin):
    engine = sa.create_engine(READONLY_URL)
    yield engine
    engine.dispose()


def ctx():
    return DraftContext(
        tenant_id="t", domain="d", contract_id="m4a-draft", owner_id="team.eval", owner_name="Eval",
        model_id="scripted", prompt_version=PROMPT_VERSION,
    )


class Scripted:
    def __init__(self, reply):
        self.reply = reply

    def complete_json(self, *, prompt, schema, max_tokens):
        return self.reply


def rowcount(engine, table):
    with engine.connect() as db:
        return db.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar()


# ------------------------------------------------------------------------------------------------------------ introspection


def test_pilot_tables_introspect_with_keys_and_types(readonly):
    facts = introspect_engine(readonly, schema="public", tables=["sales_orders", "demo_sales_orders"]).facts
    demo = facts.table("demo_sales_orders")
    assert demo.primary_key == ("id",)
    assert {c.name: c.data_type for c in demo.columns} == {
        "id": "string", "amount": "decimal", "status": "string", "created_at": "timestamp"
    }
    assert [t.name for t in facts.tables] == ["demo_sales_orders", "sales_orders"]


def test_comments_foreign_keys_and_unusual_types_on_a_real_schema(readonly):
    result = introspect_engine(readonly, schema="shop")
    orders, customers = result.facts.table("orders"), result.facts.table("customers")
    assert customers.comment == "One row per customer" and customers.column("region").comment == "Sales region"
    assert [(fk.columns, fk.ref_table, fk.ref_columns) for fk in orders.foreign_keys] == [(("customer_id",), "customers", ("id",))]
    assert orders.asset_id == "shop.orders"
    # An array and jsonb are not recognised types: skipped and reported, never guessed. An enum reflects as VARCHAR(n), so it is a
    # string (categorical) column. A view is not a table.
    assert set(result.skipped) == {"orders.tags", "orders.extra"}
    assert orders.column("kind").data_type == "string"
    assert result.facts.table("orders_view") is None


# ---------------------------------------------------------------------------------------------------------------- profiling


def test_profiling_the_pilot_tables_as_the_read_only_role(readonly):
    facts = introspect_engine(readonly, schema="public", tables=["demo_sales_orders"]).facts
    profile = profile_engine(readonly, facts, schema="public")
    cols = {c.name: c for c in profile.table("demo_sales_orders").columns}
    assert cols["id"].rows == 20_000 and cols["id"].distinct == 20_000 and cols["id"].sample_status == "high_cardinality"
    assert cols["status"].distinct == 3 and set(cols["status"].samples) == {"paid", "pending", "refunded"}
    assert cols["amount"].min_value is not None and cols["amount"].samples == ()
    # Ranges are in UTC whatever the server's zone, so the prompt and the fingerprint do not depend on it.
    assert cols["created_at"].min_value.startswith("2024-01-01") and cols["created_at"].min_value.endswith("+00:00")


def test_sensitive_and_small_cohort_rules_hold_on_postgres(readonly):
    facts = introspect_engine(readonly, schema="shop", tables=["customers"]).facts
    cols = {c.name: c for c in profile_engine(readonly, facts, schema="shop").table("customers").columns}
    assert cols["email"].sample_status == "sensitive_name" and cols["email"].samples == ()
    assert set(cols["region"].samples) == {"north", "south", "east"}  # 10 rows each, exactly the cohort floor
    floor = profile_engine(readonly, facts, schema="shop", limits=ProfileLimits(min_cohort=11))
    assert {c.name: c for c in floor.table("customers").columns}["region"].sample_status == "small_cohort"


def test_the_guard_statements_make_the_transaction_read_only_on_a_real_server(admin):
    """Even an admin connection cannot write once the profiler's guards are on."""
    before = rowcount(admin, "public.sales_orders")
    with admin.connect() as db:
        for statement in session_guards("postgresql", 5_000):
            db.execute(sa.text(statement))
        assert db.execute(sa.text("SHOW transaction_read_only")).scalar() == "on"
        assert db.execute(sa.text("SHOW statement_timeout")).scalar() == "5s"
        with pytest.raises(sa.exc.DBAPIError):
            db.execute(sa.text("INSERT INTO public.sales_orders VALUES ('x', 1, 'paid', now())"))
        db.rollback()
    assert rowcount(admin, "public.sales_orders") == before


def test_the_read_only_role_cannot_write_at_all(readonly):
    with readonly.connect() as db:
        with pytest.raises(sa.exc.DBAPIError):
            db.execute(sa.text("DELETE FROM public.demo_sales_orders"))
        db.rollback()


def test_profiling_leaves_the_data_untouched(readonly, admin):
    before = rowcount(admin, "public.demo_sales_orders")
    facts = introspect_engine(readonly, schema="public", tables=["demo_sales_orders"]).facts
    profile_engine(readonly, facts, schema="public")
    assert rowcount(admin, "public.demo_sales_orders") == before


# ----------------------------------------------------------------------------------------------------------------- explain


def test_a_valid_compiled_query_plans_under_the_read_only_role(readonly):
    probe = explain_probe(readonly, allowed_tables=["demo_sales_orders"])
    probe('SELECT COUNT(*) AS "m" FROM "demo_sales_orders" LIMIT 1', {})  # raises on failure


def test_the_probe_refuses_a_write_and_a_foreign_table_without_touching_data(readonly, admin):
    before = rowcount(admin, "public.demo_sales_orders")
    probe = explain_probe(readonly, allowed_tables=["demo_sales_orders"])
    with pytest.raises(Exception) as write:
        probe('DELETE FROM "demo_sales_orders"', {})
    with pytest.raises(Exception) as other:
        probe('SELECT 1 FROM "sales_orders"', {})
    assert type(write.value).__name__ == type(other.value).__name__ == "QueryRejected"
    assert rowcount(admin, "public.demo_sales_orders") == before


def test_the_gateway_rejects_a_write_even_with_no_allowlist_help(readonly):
    gateway = PostgresGateway(readonly, allowed_tables=["demo_sales_orders"])
    with pytest.raises(Exception):
        gateway.estimate(CompiledQuery(sql='UPDATE "demo_sales_orders" SET amount = 0', parameters={}))


GOOD = {
    "datasets": [{"table": "demo_sales_orders", "display_name": "Orders", "description": "One row per order."}],
    "metrics": [
        {"id": "revenue", "table": "demo_sales_orders", "aggregation": "sum", "measure_column": "amount",
         "grain_key_columns": ["id"], "description": "Total order value."},
        {"id": "order_count", "table": "demo_sales_orders", "aggregation": "count", "grain_key_columns": ["id"],
         "description": "Number of orders."},
    ],
    "dimensions": [
        {"id": "status", "table": "demo_sales_orders", "column": "status", "dimension_type": "categorical", "description": "State."},
        {"id": "created_at", "table": "demo_sales_orders", "column": "created_at", "dimension_type": "temporal", "description": "When."},
    ],
}


def test_a_draft_survives_a_real_explain_dry_run(readonly):
    facts = introspect_engine(readonly, schema="public", tables=["demo_sales_orders"]).facts
    profile = profile_engine(readonly, facts, schema="public")
    probe = explain_probe(readonly, allowed_tables=[t.name for t in facts.tables])
    result = draft_ontology(facts, Scripted(GOOD), ctx(), explain=probe, profile=profile, now=NOW)
    assert not result.dropped
    assert {m.id for m in result.document.contract.metrics} == {"revenue", "order_count"}
    assert {d.id for d in result.document.contract.dimensions} == {"status", "created_at"}
    assert result.document.lifecycle == "draft"


def test_a_role_without_the_grants_makes_every_candidate_a_coded_drop_not_a_crash(readonly):
    """The plan's risk: EXPLAIN needs the query's privileges. Introspect with one role, dry-run with a narrower one."""
    facts = introspect_engine(readonly, schema="public", tables=["sales_orders", "demo_sales_orders"]).facts
    narrow = sa.create_engine(sa.engine.make_url(READONLY_URL).set(username="ro_narrow", password="narrow-throwaway"))
    try:
        probe = explain_probe(narrow, allowed_tables=["sales_orders", "demo_sales_orders"])
        reply = {
            "metrics": [
                {"id": "ok", "table": "demo_sales_orders", "aggregation": "count", "grain_key_columns": ["id"], "description": "n"},
                {"id": "blocked", "table": "sales_orders", "aggregation": "count", "grain_key_columns": ["id"], "description": "n"},
            ]
        }
        result = draft_ontology(facts, Scripted(reply), ctx(), explain=probe, now=NOW)
    finally:
        narrow.dispose()
    assert [m.id for m in result.document.contract.metrics] == ["ok"]
    assert [(d.item, d.code) for d in result.dropped] == [("blocked", "explain_failed:ExecutionError")]
