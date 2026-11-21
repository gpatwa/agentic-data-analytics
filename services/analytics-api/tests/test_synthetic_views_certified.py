"""The certified combined contract for the synthetic tenant: the union of the two certified contracts, no new definition, and the compiler serves both views from one contract."""

from __future__ import annotations

import json
from decimal import Decimal

import duckdb
import pytest
from reference_stack import orders_current_contract as counts
from reference_stack import orders_usd_gross_contract as usd
from reference_stack import synthetic_views_contract as sv
from reference_stack.synthetic.config import GeneratorConfig
from reference_stack.synthetic.food_delivery import generate

from app.compiler import DuckDBCompilerAdapter
from app.compiler.service import CertifiedIntentCompiler
from app.context.registry_context import context_from_contract
from app.runtime.ontology_node import has_synonyms, resolve_certified_intent
from packages.platform_contracts.analytics_intent import AnalyticalIntent

REGISTRY = sv.CERTIFIED.parents[1]


def intent(metric, dataset, **kw):
    return AnalyticalIntent.model_validate({"query_id": "q", "tenant_id": "synthetic", "dataset_id": dataset, "metrics": [{"metric_id": metric}],
                                            "semantic_contract": {"contract_id": sv.CONTRACT_ID, "contract_version": sv.VERSION}, **kw})


def test_the_file_equals_the_builder_and_is_certified_with_a_recorded_approval_and_the_source_digests():
    doc = sv.build()
    assert sv.CERTIFIED.read_text() == sv.render() and doc.lifecycle == "certified" and doc.contract.id == sv.CONTRACT_ID
    cert = doc.contract.metadata["certification"]
    assert "synthetic_views_combined_contract" in cert["status"] and "no new definition" in cert["scope"] and "tip_total is NOT certified" in cert["scope"]
    assert set(cert["sources"]) == {"food-delivery-orders-current", "food-delivery-orders-usd-gross"}


def test_no_definition_is_new_each_item_equals_its_certified_source_apart_from_its_name():
    combined = sv.build().contract
    sources = {"orders_current": counts.build().contract, "orders_usd_gross": usd.build().contract}
    by_id = {m.id: m for m in combined.metrics}
    strip = lambda m: {k: v for k, v in m.model_dump(mode="json").items() if k not in {"id", "display_name"}}  # noqa: E731
    for dataset, src in sources.items():
        for m in src.metrics:
            new_id = sv.RENAMED_METRICS.get(m.id, m.id) if dataset == "orders_usd_gross" else m.id
            assert strip(by_id[new_id]) == strip(m)
    dims = {d.id: d for d in combined.dimensions}
    for dataset, src in sources.items():
        for d in src.dimensions:
            new_id = (sv.USD_DIMENSION_PREFIX + d.id) if dataset == "orders_usd_gross" else d.id
            assert strip(dims[new_id]) == strip(d)


def test_the_tip_sum_is_not_in_it_no_synonym_is_certified_and_no_restricted_field_is_present():
    contract = sv.build().contract
    assert "tip_total" not in {m.id for m in contract.metrics} and not has_synonyms(contract)
    assert all(f.classification != "restricted" for f in contract.fields)
    assert len({m.id for m in contract.metrics}) == 7 and len({d.id for d in contract.dimensions}) == 15


def test_one_certified_contract_in_its_registry_root_and_the_earlier_two_are_unchanged():
    assert [p.name for p in (REGISTRY / "contracts").glob("*.json")] == ["food-delivery-synthetic-views-v1.json"]
    assert counts.CERTIFIED.read_text() == counts.render() and usd.CERTIFIED.read_text() == usd.render()


def test_a_left_out_metric_cannot_be_asked_for_and_a_certified_one_resolves():
    doc = sv.build()
    ontology = context_from_contract(doc)[1]
    resolve_certified_intent(intent("gross_order_value", "orders_usd_gross", group_by=[{"dimension_id": "usd_city"}]), doc.contract, ontology)
    with pytest.raises(Exception):  # noqa: B017 - any refusal
        resolve_certified_intent(intent("tip_total", "orders_usd_gross"), doc.contract, ontology)


def test_one_contract_serves_both_views_and_reproduces_the_answer_key_on_duckdb(tmp_path):
    out = tmp_path / "t"
    generate(GeneratorConfig(tier="T"), out)
    truth = json.loads((out / "truth.json").read_text())["values"]
    con = duckdb.connect()
    con.execute(f"CREATE VIEW f AS SELECT * FROM read_parquet('{(out / 'orders_flat.parquet').as_posix()}')")
    con.execute("""CREATE VIEW cur AS SELECT * EXCLUDE (rn, is_reversal) FROM (SELECT f.*, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY updated_at DESC, row_id DESC) rn FROM f WHERE NOT is_reversal AND restaurant_id <> 'R000000') WHERE rn = 1""")
    con.execute('CREATE VIEW "dev.compass_synthetic_food_delivery.orders_current" AS SELECT * FROM cur')
    con.execute("""CREATE VIEW "dev.compass_synthetic_food_delivery.orders_usd_gross" AS SELECT * FROM cur WHERE currency = 'USD' AND status <> 'cancelled'""")
    compiler = CertifiedIntentCompiler(REGISTRY, DuckDBCompilerAdapter())

    def value(metric, dataset):
        plan = compiler.compile(intent(metric, dataset))
        return con.execute(plan.sql, plan.parameters or {}).fetchone()[0]

    assert value("order_count", "orders_current") == truth["orders_distinct"]
    assert Decimal(str(value("gross_order_value", "orders_usd_gross"))) == Decimal(truth["gross_value_usd_cents"]) / 100
    assert value("usd_order_count", "orders_usd_gross") == con.execute('SELECT COUNT(*) FROM "dev.compass_synthetic_food_delivery.orders_usd_gross"').fetchone()[0]
