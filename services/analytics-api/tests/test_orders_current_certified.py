"""The certified orders_current contract: counts and dimensions only, no synonyms, no restricted field, and the compiler counts the view's rows."""

from __future__ import annotations

import json

import duckdb
import pytest
from reference_stack import orders_current_contract as oc
from reference_stack.synthetic.config import GeneratorConfig
from reference_stack.synthetic.food_delivery import generate

from app.compiler import DuckDBCompilerAdapter
from app.compiler.service import CertifiedIntentCompiler
from app.context.registry_context import context_from_contract
from app.runtime.ontology_node import has_synonyms, resolve_certified_intent
from packages.platform_contracts.analytics_intent import AnalyticalIntent

REGISTRY = oc.CERTIFIED.parents[1]


def intent(**kw):
    return AnalyticalIntent.model_validate({"query_id": "q", "tenant_id": "synthetic", "dataset_id": "orders_current", "metrics": [{"metric_id": "order_count"}],
                                            "semantic_contract": {"contract_id": "food-delivery-orders-current", "contract_version": oc.VERSION}, **kw})


def test_the_file_equals_the_builder_and_is_certified_with_a_recorded_approval():
    doc = oc.build()
    assert oc.CERTIFIED.read_text() == oc.render() and doc.lifecycle == "certified" and doc.contract.version == oc.VERSION
    cert = doc.contract.metadata["certification"]
    assert "orders_current_certify_counts" in cert["status"] and cert["scope_digest"] == oc.scope_digest() and "NOT certified" in cert["scope"]


def test_only_the_reviewed_metric_and_dimensions_are_in_it():
    contract = oc.build().contract
    assert [m.id for m in contract.metrics] == ["order_count"] and all(m.certification == "certified" for m in contract.metrics)
    assert {d.id for d in contract.dimensions} == set(oc.DIMENSIONS)
    text = json.dumps(contract.model_dump(mode="json"))
    for left_out in ("total_order_amount", "total_tip_amount", "total_discount_amount", "average_delivery_minutes", "average_prep_minutes"):
        assert left_out not in text


def test_no_synonym_is_certified_and_no_restricted_field_is_in_the_contract():
    contract = oc.build().contract
    assert not has_synonyms(contract)  # the unreviewed agent words are dropped, so it is not subject to the coverage guard
    assert all(f.classification != "restricted" for f in contract.fields)
    assert not {"customer_email", "courier_phone", "delivery_address", "tax", "restaurant_name"} & {f.id.split(".")[1] for f in contract.fields}


def test_a_certified_intent_resolves_and_cannot_name_a_left_out_metric():
    doc = oc.build()
    ontology = context_from_contract(doc)[1]
    resolve_certified_intent(intent(group_by=[{"dimension_id": "status"}]), doc.contract, ontology)
    with pytest.raises(Exception):  # noqa: B017 - any refusal: the metric is not certified in this contract
        resolve_certified_intent(intent(metrics=[{"metric_id": "total_order_amount"}]), doc.contract, ontology)


def test_the_compiler_counts_the_views_rows_and_matches_the_answer_key_on_duckdb(tmp_path):
    out = tmp_path / "t"
    generate(GeneratorConfig(tier="T"), out)
    truth = json.loads((out / "truth.json").read_text())["values"]["orders_distinct"]
    con = duckdb.connect()
    con.execute(f"CREATE VIEW f AS SELECT * FROM read_parquet('{(out / 'orders_flat.parquet').as_posix()}')")
    con.execute("""CREATE VIEW "dev.compass_synthetic_food_delivery.orders_current" AS SELECT * EXCLUDE rn FROM (SELECT f.*, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY updated_at DESC, row_id DESC) rn FROM f WHERE NOT is_reversal AND restaurant_id <> 'R000000') WHERE rn = 1""")  # the DuckDB adapter quotes the physical name as one identifier
    plan = CertifiedIntentCompiler(REGISTRY, DuckDBCompilerAdapter()).compile(intent())
    assert "orders_current" in plan.sql and "COUNT" in plan.sql.upper()
    assert con.execute(plan.sql, plan.parameters or {}).fetchone()[0] == truth
