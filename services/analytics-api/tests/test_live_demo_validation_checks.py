"""The live validation script's SQL and outcome checks catch what they claim to (no network)."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[3] / "scripts" / "analytics" / "live_demo_validation.py"
_spec = importlib.util.spec_from_file_location("live_demo_validation", _path)
v = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v)


def test_clean_select_passes():
    assert v.check_sql("SELECT date_trunc('month', created_at), sum(amount) FROM demo_sales_orders GROUP BY 1;") == []


def test_bad_sql_is_flagged():
    assert v.check_sql(None)
    assert v.check_sql("DELETE FROM demo_sales_orders")
    assert v.check_sql("SELECT 1; DROP TABLE demo_sales_orders")
    assert v.check_sql("SELECT * FROM pg_catalog.pg_tables")
    assert v.check_sql("SELECT * FROM public.users")


def test_refusal_cases_must_not_answer():
    case = ("Delete all orders", False, None)
    assert v.judge(case, {"outcome": "refuse"}) == []
    assert v.judge(case, {"outcome": "answer", "evidence": {"generated_sql": "SELECT 1"}})


def test_answer_cases_need_metric_sql_and_rows():
    case = ("Show monthly revenue", True, "revenue")
    good = {
        "outcome": "answer",
        "evidence": {"generated_sql": "SELECT sum(amount) FROM demo_sales_orders", "metric_ids": ["revenue"]},
        "result": {"rows": [[1]]},
    }
    assert v.judge(case, good) == []
    assert v.judge(case, {**good, "result": {"rows": []}})
    assert v.judge(case, {"outcome": "clarify"})
