"""A boolean must stay a boolean through a JSON round trip. With the union written `str | int | float | bool`, pydantic wrote False and True as 0.0 and 1.0,
which a boolean filter on a real warehouse would compare as a number (found by clicking through the local DuckDB stack: 'is cancellation equals 0.0')."""

from __future__ import annotations

import json

import pytest

from packages.platform_contracts.analytics_intent import AnalyticalIntent
from packages.platform_contracts.semantic import SemanticFilter


def intent(values):
    return AnalyticalIntent.model_validate({"query_id": "q", "tenant_id": "t", "dataset_id": "d", "metrics": [{"metric_id": "m"}],
                                            "semantic_contract": {"contract_id": "c", "contract_version": "v"},
                                            "filters": [{"field_id": "d.flag", "operator": "in", "values": values}]})


def test_boolean_filter_values_in_an_intent_survive_json():
    out = json.loads(intent([False, True, 3, "x", 2.5]).model_dump_json())["filters"][0]["values"]
    assert out == [False, True, 3, "x", 2.5] and [type(v) for v in out] == [bool, bool, int, str, float]
    assert intent([True]).model_dump(mode="json")["filters"][0]["values"] == [True]


@pytest.mark.parametrize("value", [False, True, "delivered", 7, 0.5, [True, "a", 3]])
def test_a_contract_filter_literal_keeps_its_type_through_json(value):
    op = "in" if isinstance(value, list) else "equals"
    f = SemanticFilter(id="f", dataset_id="d", field_id="d.x", operator=op, value_source="literal", literal_value=value)
    back = SemanticFilter.model_validate_json(f.model_dump_json())
    assert back.literal_value == value and (not isinstance(value, bool) or back.literal_value is value)
    assert json.loads(f.model_dump_json())["literal_value"] == value


def test_a_zero_or_one_that_was_an_int_stays_an_int():
    f = SemanticFilter(id="f", dataset_id="d", field_id="d.x", operator="equals", value_source="literal", literal_value=1)
    assert json.loads(f.model_dump_json())["literal_value"] == 1 and isinstance(json.loads(f.model_dump_json())["literal_value"], int)
    assert isinstance(json.loads(f.model_dump_json())["literal_value"], bool) is False
