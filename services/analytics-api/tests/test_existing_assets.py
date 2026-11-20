"""E6: start from a customer's existing metric views and semantic views. Fixtures follow the vendors' public references; no workspace is used."""

from __future__ import annotations

import json

import pytest

from app.ontology_draft import existing_assets as ea
from app.ontology_draft.emit import write_draft
from app.ontology_draft.facts import FactColumn, FactTable, SchemaFacts
from app.ontology_draft.validate import DraftContext, validate_and_emit

COLS = (("id", "integer"), ("amount", "decimal"), ("status", "string"), ("created_at", "timestamp"), ("customer_email", "string"))


def facts(key=("id",), restricted=()):
    cols = tuple(FactColumn(name=n, data_type=t, comment="Order total" if n == "amount" else None, restricted=n in restricted) for n, t in COLS)
    return SchemaFacts(tables=(FactTable(name="orders", asset_id="sch.orders", columns=cols, primary_key=key, key_evidence="declared" if key else "none"),))


CTX = DraftContext(tenant_id="t", domain="sales", contract_id="orders-imported", owner_id="team.owners", owner_name="Owners",
                   model_id="imported", prompt_version="import-v1")

# Shaped after the metric view YAML reference (specification 1.1): fields, measures, synonyms, display names, and the kinds this product cannot take.
YAML = """
version: 1.1
source: cat.sch.orders
comment: Orders
fields:
  - name: order_status
    expr: status
    comment: 'Fulfilment status'
    display_name: 'Order Status'
    synonyms: ['status', 'fulfillment status']
  - name: order_date
    expr: source.created_at
  - name: order_month
    expr: DATE_TRUNC('MONTH', created_at)
  - expr: source.*
measures:
  - name: order_count
    expr: COUNT(1)
    display_name: 'Order Count'
  - name: total_revenue
    expr: SUM(amount)
    comment: 'Gross revenue from all orders'
    synonyms: ['revenue', 'total sales']
  - name: unique_customers
    expr: COUNT(DISTINCT customer_id)
  - name: avg_order_value
    expr: SUM(amount) / COUNT(DISTINCT id)
  - name: open_order_revenue
    expr: SUM(amount) FILTER (WHERE status = 'O')
  - name: rolling_7day
    expr: COUNT(DISTINCT id)
    window:
      - order: order_date
        range: trailing 7 day
  - name: sales_with_param
    expr: SUM((1 - discount) * amount)
parameters:
  - name: discount
    data_type: double
    default: 0
"""


def reasons(result):
    return {n.item: n.reason for n in result.not_imported}


# ---- Databricks metric view -------------------------------------------------------------------------------------------------------


def test_a_metric_view_keeps_what_fits_and_lists_the_rest_with_a_reason():
    r = ea.from_metric_view_yaml(YAML, facts(), view_name="orders_metrics")
    assert [(m.id, m.aggregation, m.measure_column) for m in r.proposal.metrics] == [("order_count", "count", None), ("total_revenue", "sum", "amount")]
    assert [(d.id, d.column, d.dimension_type) for d in r.proposal.dimensions] == [("order_status", "status", "categorical"), ("order_date", "created_at", "temporal")]
    assert reasons(r) == {"unique_customers": "unknown_column", "avg_order_value": "ratio", "open_order_revenue": "filtered_measure", "rolling_7day": "window",
                          "sales_with_param": "parameter", "order_month": "expression_field", "source.*": "wildcard"}
    assert r.found_metrics == 7 and r.found_dimensions == 4 and "#" in r.sources[0]


def test_comments_synonyms_and_display_names_come_through_as_candidate_text():
    r = ea.from_metric_view_yaml(YAML, facts(), view_name="v")
    rev = next(m for m in r.proposal.metrics if m.id == "total_revenue")
    assert rev.synonyms == ["revenue", "total sales"] and rev.description == "Gross revenue from all orders"


def test_the_dimensions_keyword_of_the_older_specification_is_the_same_list():
    old = "version: 0.1\nsource: cat.sch.orders\ndimensions:\n  - name: s\n    expr: status\nmeasures:\n  - name: n\n    expr: COUNT(*)\n"
    r = ea.from_metric_view_yaml(old, facts(), view_name="v")
    assert [d.id for d in r.proposal.dimensions] == ["s"] and [m.id for m in r.proposal.metrics] == ["n"]


def test_a_view_filter_a_query_source_a_foreign_table_or_a_join_is_never_imported():
    flt = ea.from_metric_view_yaml("version: 1.1\nsource: c.s.orders\nfilter: status = 'F'\nmeasures:\n  - name: n\n    expr: COUNT(1)\n", facts(), view_name="v")
    assert reasons(flt) == {"n": "view_filter"}
    q = ea.from_metric_view_yaml("version: 1.1\nsource: SELECT * FROM a.b.orders o JOIN a.b.c ON 1=1\nmeasures:\n  - name: n\n    expr: COUNT(1)\n", facts(), view_name="v")
    assert reasons(q) == {"n": "source_is_a_query"} and not q.proposal.metrics
    other = ea.from_metric_view_yaml("version: 1.1\nsource: c.s.payroll\nmeasures:\n  - name: n\n    expr: COUNT(1)\n", facts(), view_name="v")
    assert reasons(other) == {"n": "source_not_in_the_connections_tables"}  # the connection's ceiling is never widened by an import
    joined = ea.from_metric_view_yaml("version: 1.1\nsource: c.s.orders\njoins:\n  - name: customer\n    source: c.s.customer\n    on: source.cid = customer.id\n"
                                      "fields:\n  - name: nation\n    expr: customer.n_name\nmeasures:\n  - name: n\n    expr: SUM(customer.x)\n", facts(), view_name="v")
    assert reasons(joined) == {"nation": "join", "n": "join"}


@pytest.mark.parametrize("bad", ["", "not: [valid", "- a\n- b", "42"])
def test_unreadable_yaml_imports_nothing_and_says_so(bad):
    r = ea.from_metric_view_yaml(bad, facts(), view_name="v")
    assert not r.proposal.metrics and reasons(r) == {"v": "unreadable_yaml"}


# ---- Snowflake semantic view ------------------------------------------------------------------------------------------------------

# Shaped after the DESCRIBE SEMANTIC VIEW reference: (object_kind, object_name, parent_entity, property, property_value).
ROWS = [
    ("TABLE", "ORDERS", None, "BASE_TABLE_NAME", "orders"),
    ("TABLE", "ORDERS", None, "PRIMARY_KEY", '["ID"]'),
    ("TABLE", "CUSTOMERS", None, "BASE_TABLE_NAME", "customers"),
    ("RELATIONSHIP", "ORDERS_TO_CUSTOMERS", "ORDERS", "REF_TABLE", "CUSTOMERS"),
    ("DIMENSION", "ORDER_STATUS", "ORDERS", "EXPRESSION", "orders.status"),
    ("DIMENSION", "ORDER_STATUS", "ORDERS", "COMMENT", "Fulfilment status"),
    ("DIMENSION", "ORDER_STATUS", "ORDERS", "SYNONYMS", '["status", "order state"]'),
    ("DIMENSION", "ORDER_YEAR", "ORDERS", "EXPRESSION", "YEAR(created_at)"),
    ("DIMENSION", "CUSTOMER_NAME", "CUSTOMERS", "EXPRESSION", "customers.c_name"),
    ("FACT", "LINE_ID", "ORDERS", "EXPRESSION", "CONCAT(id, '-', id)"),
    ("METRIC", "ORDER_COUNT", "ORDERS", "EXPRESSION", "COUNT(orders.id)"),
    ("METRIC", "ORDER_COUNT", "ORDERS", "SYNONYMS", '["number of orders"]'),
    ("METRIC", "TOTAL_AMOUNT", "ORDERS", "EXPRESSION", "SUM(orders.amount)"),
    ("METRIC", "TOTAL_AMOUNT", "ORDERS", "COMMENT", "Sum of order amounts"),
    ("METRIC", "AVG_VALUE", "ORDERS", "EXPRESSION", "AVG(orders.amount)"),
    ("METRIC", "AVG_VALUE", "ORDERS", "ACCESS_MODIFIER", "PRIVATE"),
    ("METRIC", "BY_CUSTOMER", "ORDERS", "EXPRESSION", "COUNT(customers.c_custkey)"),
    ("DERIVED_METRIC", "BLENDED", None, "EXPRESSION", "SUM(orders.amount) / 2"),
    (None, None, None, "COMMENT", "Semantic view for orders"),
]


def test_a_semantic_view_keeps_what_fits_synonyms_become_proposals_and_the_rest_is_listed():
    r = ea.from_semantic_view_rows(ROWS, facts(), view_name="orders_sv")
    assert [(m.id, m.aggregation, m.synonyms) for m in r.proposal.metrics] == [("order_count", "count", ["number of orders"]), ("total_amount", "sum", [])]
    assert [(d.id, d.column, d.synonyms) for d in r.proposal.dimensions] == [("order_status", "status", ["status", "order state"])]
    assert reasons(r) == {"ORDER_YEAR": "expression_field", "CUSTOMER_NAME": "source_not_in_the_connections_tables", "LINE_ID": "fact_not_a_metric",
                          "AVG_VALUE": "private", "BY_CUSTOMER": "join", "BLENDED": "derived_metric", "ORDERS_TO_CUSTOMERS": "relationship_not_imported"}


def test_count_of_a_key_is_the_row_count_but_count_of_another_column_is_not():
    rows = [("TABLE", "ORDERS", None, "BASE_TABLE_NAME", "orders"), ("METRIC", "N", "ORDERS", "EXPRESSION", "COUNT(amount)")]
    assert reasons(ea.from_semantic_view_rows(rows, facts(), view_name="v")) == {"N": "count_of_column"}
    assert not ea.from_semantic_view_rows(rows, facts(), view_name="v").proposal.metrics


def test_short_or_empty_rows_are_ignored():
    r = ea.from_semantic_view_rows([("TABLE",), (), ("x", "y")], facts(), view_name="v")
    assert not r.proposal.metrics and not r.not_imported


# ---- through the same validator as any draft --------------------------------------------------------------------------------------


def drafted(result, f=None):
    return validate_and_emit(result.proposal, f or facts(), CTX)


def test_an_import_becomes_a_draft_contract_that_is_not_certified(tmp_path):
    out = drafted(ea.from_metric_view_yaml(YAML, facts(), view_name="orders_metrics"))
    assert out.document is not None and out.document.lifecycle == "draft" and not out.dropped
    assert sorted(m.id for m in out.document.contract.metrics) == ["order_count", "total_revenue"]
    note = (write_draft(out, tmp_path, report=None, skipped_columns=(), source_note="imported") / "REVIEW.md").read_text()
    assert "Not certified" in note and "revenue" in note


def test_a_table_with_no_key_evidence_still_gets_no_metric_from_an_import():
    out = drafted(ea.from_metric_view_yaml(YAML, facts(key=()), view_name="v"), facts(key=()))
    assert {d.code for d in out.dropped if d.kind == "metric"} == {"no_key_evidence"} and (out.document is None or not out.document.contract.metrics)


def test_a_vendor_comment_with_a_link_or_markup_is_dropped_like_a_models_would_be():
    bad = YAML.replace("Gross revenue from all orders", "See https://example.com/x for details")
    out = drafted(ea.from_metric_view_yaml(bad, facts(), view_name="v"))
    assert any(d.item == "total_revenue" and d.code == "bad_description" for d in out.dropped)


def test_an_imported_dimension_on_a_sensitive_column_is_dropped():
    y = "version: 1.1\nsource: c.s.orders\nfields:\n  - name: who\n    expr: customer_email\nmeasures:\n  - name: n\n    expr: COUNT(*)\n"
    out = drafted(ea.from_metric_view_yaml(y, facts(), view_name="v"))
    assert any(d.item == "who" and d.code == "sensitive_column" for d in out.dropped)


# ---- tags only tighten ------------------------------------------------------------------------------------------------------------


def test_an_upstream_sensitivity_tag_restricts_a_column_and_the_draft_drops_it():
    tagged = ea.restricted_columns({("orders", "status"): ["classification=PII"], ("orders", "amount"): ["owner=finance"]})
    assert tagged == {("orders", "status")}
    f = ea.apply_restrictions(facts(), tagged)
    assert f.table("orders").column("status").restricted and not f.table("orders").column("amount").restricted
    out = drafted(ea.from_metric_view_yaml(YAML, f, view_name="v"), f)
    assert any(d.item == "order_status" and d.code == "sensitive_column" for d in out.dropped)


def test_a_restriction_is_never_cleared_by_applying_more_hints_or_none():
    f = ea.apply_restrictions(facts(), {("orders", "status")})
    again = ea.apply_restrictions(f, set())
    assert again.table("orders").column("status").restricted
    plain = SchemaFacts.model_validate_json(facts().model_dump_json())
    assert facts().fingerprint() == plain.fingerprint() != f.fingerprint()  # a restriction is a change in the facts


def test_a_tag_cannot_unrestrict_a_name_based_sensitive_column():
    f = ea.apply_restrictions(facts(), {("orders", "id")})
    assert not f.table("orders").column("customer_email").restricted  # unmarked, yet still refused by the name rule in the validator
    y = "version: 1.1\nsource: c.s.orders\nfields:\n  - name: who\n    expr: customer_email\nmeasures:\n  - name: n\n    expr: COUNT(*)\n"
    assert any(d.code == "sensitive_column" for d in drafted(ea.from_metric_view_yaml(y, f, view_name="v"), f).dropped)


# ---- what we found ----------------------------------------------------------------------------------------------------------------


def test_the_found_summary_counts_what_existed_what_became_candidates_and_why_the_rest_did_not():
    f = ea.apply_restrictions(facts(), {("orders", "status")})
    r = ea.from_metric_view_yaml(YAML, f, view_name="orders_metrics")
    out = drafted(r, f)
    text = ea.render_found(f, [r], kept_metrics=len(out.document.contract.metrics), kept_dimensions=len(out.document.contract.dimensions))
    assert "Metrics found: 7, dimensions found: 4" in text and "2 metric(s), 1 dimension(s)" in text
    assert "ratio: 1" in text and "window: 1" in text and "Columns with comments: 1; tables with a declared key: 1; columns restricted by an upstream tag: 1" in text
    assert "nothing here is certified" in text
    assert "nothing was left out" in ea.render_found(f, [], kept_metrics=0, kept_dimensions=0)


def test_the_module_opens_no_connection_and_calls_no_model():
    from pathlib import Path

    text = Path(ea.__file__).read_text()
    assert "connect(" not in text and "complete_json" not in text and "execute(" not in text
    assert json.dumps(ROWS)  # the fixtures are plain data
