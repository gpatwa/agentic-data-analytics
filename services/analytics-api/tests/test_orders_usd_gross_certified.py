"""The certified orders_usd_gross contract: the USD amount metrics the owner chose, no tip sum, no synonyms, and the compiler reproduces the answer key's gross value."""

from __future__ import annotations

import json
from decimal import Decimal

import duckdb
import pytest
from reference_stack import orders_usd_gross_contract as uc
from reference_stack.synthetic.config import GeneratorConfig
from reference_stack.synthetic.food_delivery import generate

from app.compiler import DuckDBCompilerAdapter
from app.compiler.service import CertifiedIntentCompiler
from app.context.registry_context import context_from_contract
from app.runtime.ontology_node import has_synonyms, resolve_certified_intent
from packages.platform_contracts.analytics_intent import AnalyticalIntent

REGISTRY = uc.CERTIFIED.parents[1]


def intent(metric="order_count", **kw):
    return AnalyticalIntent.model_validate({"query_id": "q", "tenant_id": "synthetic", "dataset_id": "orders_usd_gross", "metrics": [{"metric_id": metric}],
                                            "semantic_contract": {"contract_id": "food-delivery-orders-usd-gross", "contract_version": uc.VERSION}, **kw})


def test_the_file_equals_the_builder_and_is_certified_with_a_recorded_approval():
    doc = uc.build()
    assert uc.CERTIFIED.read_text() == uc.render() and doc.lifecycle == "certified" and doc.contract.version == uc.VERSION
    cert = doc.contract.metadata["certification"]
    assert "orders_usd_gross_certify_amounts" in cert["status"] and cert["scope_digest"] == uc.scope_digest() and "tip_total is NOT certified" in cert["scope"]


def test_exactly_the_chosen_metrics_are_in_it_and_the_tip_sum_is_not():
    contract = uc.build().contract
    assert [m.id for m in contract.metrics] == list(uc.METRICS) and all(m.certification == "certified" for m in contract.metrics)
    assert {d.id for d in contract.dimensions} == set(uc.DIMENSIONS)
    assert "tip_total" not in {m.id for m in contract.metrics}  # named only in the metadata, as not certified
    assert not {"orders_usd_gross.tip_amount", "orders_usd_gross.currency"} & {f.id for f in contract.fields}


def test_no_synonym_is_certified_and_no_restricted_field_is_in_the_contract():
    contract = uc.build().contract
    assert not has_synonyms(contract) and all(f.classification != "restricted" for f in contract.fields)


def test_the_left_out_metric_cannot_be_asked_for():
    doc = uc.build()
    ontology = context_from_contract(doc)[1]
    resolve_certified_intent(intent("gross_order_value", group_by=[{"dimension_id": "city"}]), doc.contract, ontology)
    with pytest.raises(Exception):  # noqa: B017 - any refusal: tip_total is not certified in this contract
        resolve_certified_intent(intent("tip_total"), doc.contract, ontology)


def test_the_compiler_reproduces_the_answer_keys_gross_usd_value_and_counts_the_views_rows(tmp_path):
    out = tmp_path / "t"
    generate(GeneratorConfig(tier="T"), out)
    truth = json.loads((out / "truth.json").read_text())["values"]
    con = duckdb.connect()
    con.execute(f"CREATE VIEW f AS SELECT * FROM read_parquet('{(out / 'orders_flat.parquet').as_posix()}')")
    con.execute("""CREATE VIEW "dev.compass_synthetic_food_delivery.orders_usd_gross" AS SELECT * EXCLUDE (rn, is_reversal) FROM (SELECT f.*, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY updated_at DESC, row_id DESC) rn FROM f WHERE NOT is_reversal AND restaurant_id <> 'R000000') WHERE rn = 1 AND currency = 'USD' AND status <> 'cancelled'""")  # the DuckDB adapter quotes the physical name as one identifier
    compiler = CertifiedIntentCompiler(REGISTRY, DuckDBCompilerAdapter())

    def value(metric):
        plan = compiler.compile(intent(metric))
        return con.execute(plan.sql, plan.parameters or {}).fetchone()[0]

    assert Decimal(str(value("gross_order_value"))) == Decimal(truth["gross_value_usd_cents"]) / 100
    assert value("order_count") == con.execute('SELECT COUNT(*) FROM "dev.compass_synthetic_food_delivery.orders_usd_gross"').fetchone()[0]
    for metric in ("gross_subtotal", "delivery_fee_total", "service_fee_total", "discount_total"):
        assert value(metric) is not None
