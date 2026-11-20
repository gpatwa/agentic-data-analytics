"""The drafter on the synthetic food-delivery schema, with the keys the live read-back found (L1, 2026-11-20).

No model is called: a fixed, recorded reply stands in for one, so this pins the validator's key rule on this schema, not draft quality. The
keys below are exactly what `information_schema` returned for the loaded tables on the validation workspace."""

from __future__ import annotations

import duckdb
import pytest
from reference_stack.synthetic.config import GeneratorConfig
from reference_stack.synthetic.food_delivery import generate

from app.ontology_draft.drafter import PROMPT_VERSION, draft_ontology
from app.ontology_draft.facts import FactColumn, FactTable, SchemaFacts, normalize_type
from app.ontology_draft.validate import DraftContext

PREFIX = "dev.compass_synthetic_food_delivery."
DECLARED = {
    "restaurants": ("restaurant_id",), "couriers": ("courier_id",), "customers": ("customer_id",), "orders": ("row_id",),
    "order_items": ("order_id", "line_no"), "payments": ("payment_id",), "orders_flat": ("row_id",),
}

REPLY = {
    "datasets": [{"table": "orders_flat", "display_name": "Orders", "description": "One row per recorded order event."}],
    "metrics": [
        {"id": "order_rows", "table": "orders_flat", "aggregation": "count", "grain_key_columns": ["row_id"], "description": "Number of order rows."},
        {"id": "total_amount_sum", "table": "orders_flat", "aggregation": "sum", "measure_column": "total_amount", "grain_key_columns": ["row_id"], "description": "Sum of total amount."},
        {"id": "delivery_minutes_avg", "table": "orders_flat", "aggregation": "average", "measure_column": "delivery_minutes", "grain_key_columns": ["row_id"], "description": "Average delivery minutes."},
    ],
    "dimensions": [
        {"id": "city", "table": "orders_flat", "column": "city", "dimension_type": "categorical", "description": "City of the order."},
        {"id": "status", "table": "orders_flat", "column": "status", "dimension_type": "categorical", "description": "Order status."},
        {"id": "order_ts", "table": "orders_flat", "column": "order_ts", "dimension_type": "temporal", "description": "Order time."},
    ],
}


class Recorded:
    def complete_json(self, *, prompt, schema, max_tokens):  # noqa: ARG002
        return REPLY


def ctx() -> DraftContext:
    return DraftContext(tenant_id="t", domain="food_delivery", contract_id="fd-draft", owner_id="team.demo", owner_name="Demo",
                        model_id="recorded-reply-not-a-model", prompt_version=PROMPT_VERSION, dialect="databricks", physical_prefix=PREFIX)


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("fd_keys")
    generate(GeneratorConfig(tier="T"), out)
    return out


def facts(data, *, keys: bool) -> SchemaFacts:
    con = duckdb.connect()
    tables = []
    for name, key in DECLARED.items():
        columns = []
        for col, typ, null, *_ in con.execute(f"DESCRIBE SELECT * FROM '{data}/{name}.parquet'").fetchall():
            semantic = normalize_type(typ.split("(")[0] if typ.startswith("DECIMAL") else typ)
            if semantic is not None:
                columns.append(FactColumn(name=col, data_type=semantic, nullable=null == "YES"))
        tables.append(FactTable(name=name, asset_id=f"{PREFIX}{name}", columns=tuple(columns),
                                primary_key=key if keys else (), key_evidence="declared" if keys else "none"))
    return SchemaFacts(tables=tuple(tables))


def test_every_declared_key_names_columns_the_generator_writes(data):
    f = facts(data, keys=True)
    assert {t.name: t.primary_key for t in f.tables} == DECLARED


def test_with_declared_keys_every_proposed_item_is_kept(data):
    result = draft_ontology(facts(data, keys=True), Recorded(), ctx())
    contract = result.document.contract
    assert {m.id for m in contract.metrics} == {"order_rows", "total_amount_sum", "delivery_minutes_avg"}
    assert {d.id for d in contract.dimensions} == {"city", "status", "order_ts"}
    assert result.dropped == ()
    assert result.provenance["keys"] == {"orders_flat": {"columns": ["row_id"], "evidence": "declared"}}


def test_without_keys_no_metric_survives_and_dimensions_follow(data):
    result = draft_ontology(facts(data, keys=False), Recorded(), ctx())
    codes = {(d.item, d.code) for d in result.dropped}
    assert {("order_rows", "no_key_evidence"), ("total_amount_sum", "no_key_evidence"), ("delivery_minutes_avg", "no_key_evidence")} <= codes
    assert {("city", "no_metric_for_dimension"), ("status", "no_metric_for_dimension")} <= codes
    assert result.document is None or not result.document.contract.metrics
    assert result.provenance["keys"] == {}


def test_a_key_the_data_does_not_have_is_refused_before_the_drafter_runs(data):
    cols = facts(data, keys=True).table("orders_flat").columns
    with pytest.raises(ValueError):
        FactTable(name="orders_flat", asset_id=f"{PREFIX}orders_flat", columns=cols, primary_key=("no_such_column",), key_evidence="declared")
