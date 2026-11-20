"""G1 to G4: the synthetic food-delivery generator is deterministic, its two answer keys agree, every defect shows up (and can fail), and nothing real or sensitive appears."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import duckdb
import pytest
from reference_stack.synthetic import reference as ref
from reference_stack.synthetic import truth as T
from reference_stack.synthetic.config import DEFECTS, EVIDENCE, GeneratorConfig, Rates
from reference_stack.synthetic.food_delivery import TABLES, generate
from reference_stack.synthetic.report import REPORT, render_report

QUESTIONS = json.loads((Path(__file__).resolve().parent.parent / "reference_stack/synthetic/food_delivery_questions.json").read_text())


def build(tmp_path_factory, **kw):
    out = tmp_path_factory.mktemp("synthetic")
    manifest = generate(GeneratorConfig(**kw), out)
    return out, manifest


@pytest.fixture(scope="module")
def s_run(tmp_path_factory):
    return build(tmp_path_factory, tier="S")


def q(out, sql):
    return duckdb.connect().execute(sql.replace("FLAT", f"'{out / 'orders_flat.parquet'}'")).fetchall()


# ---- G1: determinism and configuration ---------------------------------------------------------------------------------------------


def test_same_version_config_and_seed_give_the_same_logical_content_twice_and_anywhere(tmp_path_factory, s_run):
    out2, again = build(tmp_path_factory, tier="S")
    out, first = s_run
    assert first.logical_digest == again.logical_digest and (out / "truth.json").read_text() == (out2 / "truth.json").read_text()


def test_a_different_seed_tier_or_rate_gives_different_content_and_a_different_config_digest(tmp_path_factory, s_run):
    _, base = s_run
    for kw in ({"seed": 7}, {"tier": "T"}, {"rates": Rates(cancelled=0.07)}):
        _, other = build(tmp_path_factory, **{"tier": "S", **kw})
        assert other.logical_digest != base.logical_digest and other.body["config_digest"] != base.body["config_digest"], kw


def test_a_row_does_not_depend_on_how_many_rows_there_are(tmp_path_factory):
    """The first orders of a small run equal those of a larger one for the draws that do not depend on table sizes (time, status, payment method):
    values come from a keyed hash, not a running generator. Which restaurant an order uses depends on how many restaurants exist, so it is not compared."""
    small, _ = build(tmp_path_factory, tier="T")
    big, _ = build(tmp_path_factory, tier="S")
    base_small = q(small, "SELECT order_id, order_ts, status, payment_method FROM FLAT WHERE row_id % 4 = 0 AND order_id < 'O0000000100' ORDER BY 1")
    base_big = q(big, "SELECT order_id, order_ts, status, payment_method FROM FLAT WHERE row_id % 4 = 0 AND order_id < 'O0000000100' ORDER BY 1")
    assert base_small == base_big


def test_the_configuration_rejects_unknown_tiers_unknown_defects_and_dropping_the_storage_convention():
    with pytest.raises(ValueError):
        GeneratorConfig(tier="huge").n_orders
    with pytest.raises(ValueError):
        GeneratorConfig(defects=("D1", "D99", "D5"))
    with pytest.raises(ValueError):
        GeneratorConfig(defects=tuple(d for d in DEFECTS if d != "D5"))
    assert GeneratorConfig(defects=("D5",)).on("D1") is False


def test_there_are_forty_real_city_names_with_zones_and_no_coordinates():
    assert len(ref.CITIES) == 40 and len({c[0] for c in ref.CITIES}) == 40
    assert all(re.fullmatch(r"[A-Z][A-Za-z]+/[A-Za-z_]+", c[2]) for c in ref.CITIES) and {c[3] for c in ref.CITIES} == {"USD", "GBP", "EUR"}
    assert "lat" not in " ".join(ref.CITIES[0]).lower()


# ---- G3: two answer keys that agree -------------------------------------------------------------------------------------------------


def test_the_ledger_and_the_recomputation_agree_on_every_value_and_breakdown(s_run):
    out, _ = s_run
    truth = json.loads((out / "truth.json").read_text())
    assert T.disagreements(truth, T.recompute(out)) == []
    assert set(truth["values"]) == set(T.DEFINITIONS) and set(truth["maps"]) == set(T.MAPS)


@pytest.mark.parametrize("off", [("D1",), ("D2",), ("D3",), ("D4",), ("D6",), ("D7",), ("D8",), ("D9",), ("D10",), DEFECTS[:0] + ("D1", "D2", "D3", "D4", "D6", "D7", "D8", "D9", "D10")])
def test_the_two_keys_still_agree_when_defects_are_switched_off(tmp_path_factory, off):
    out, _ = build(tmp_path_factory, tier="T", defects=tuple(d for d in DEFECTS if d not in off))
    assert T.disagreements(json.loads((out / "truth.json").read_text()), T.recompute(out)) == []


def test_the_naive_values_are_wrong_in_the_ways_the_defects_predict(s_run):
    v = json.loads((s_run[0] / "truth.json").read_text())["values"]
    assert v["naive_row_count"] > v["orders_distinct"] and v["naive_sum_total_cents"] > v["gross_value_usd_cents"]
    assert v["net_value_usd_cents"] == v["gross_value_usd_cents"] - v["refunds_usd_cents"] and v["refunds_usd_cents"] > 0
    assert v["delivered_orders"] + v["cancelled_orders"] + v["refunded_orders"] + v["partially_refunded_orders"] == v["orders_distinct"]
    assert v["delivery_minutes_count"] < v["orders_distinct"]  # cancelled orders and placeholder values are left out


def test_a_tip_hidden_by_d2_is_known_to_the_generator_and_invisible_in_the_data(s_run):
    out, _ = s_run
    truth = json.loads((out / "truth.json").read_text())
    assert truth["hidden"]["tip_true_all_methods_usd_cents"] > truth["values"]["tip_card_usd_cents"]
    assert q(out, "SELECT count(*) FROM FLAT WHERE payment_method = 'cash' AND tip_amount > 0") == [(0,)]


def test_the_builders_question_set_points_only_at_real_truth_entries_and_the_two_halves_are_separate(s_run):
    truth = json.loads((s_run[0] / "truth.json").read_text())
    assert len(QUESTIONS["answerable"]) == 20 and len(QUESTIONS["refusals"]) == 10 and "not the owner's" in QUESTIONS["note"]
    for item in QUESTIONS["answerable"]:
        t = item["truth"]
        assert (t["value"] in truth["values"]) if "value" in t else (t["entry"] in truth["maps"][t["map"]]), item["id"]
    assert {r["reason"] for r in QUESTIONS["refusals"]} <= set(QUESTIONS["reason_classes"])
    assert len({i["id"] for i in QUESTIONS["answerable"] + QUESTIONS["refusals"]}) == 30


# ---- defects appear at their rates, and can fail ------------------------------------------------------------------------------------


def test_every_defect_is_visible_in_the_default_output(s_run):
    assert all(n > 0 for n in T.observed_defects(s_run[0]).values())


@pytest.mark.parametrize("defect", [d for d in DEFECTS if d != "D5"])
def test_a_defect_switched_off_reads_zero_so_the_check_can_fail(tmp_path_factory, defect):
    out, _ = build(tmp_path_factory, tier="T", defects=tuple(d for d in DEFECTS if d != defect))
    assert T.observed_defects(out)[defect] == 0, defect


def within(observed, p, n, slack=2):
    return abs(observed - p * n) <= 5 * math.sqrt(n * p * (1 - p)) + slack


def test_rates_land_within_five_standard_deviations_of_their_parameters(s_run):
    out, _ = s_run
    r, n = Rates(), 10_000
    v = json.loads((out / "truth.json").read_text())["values"]
    assert within(v["cancelled_orders"], r.cancelled, n, 3) and within(v["refunded_orders"], r.refunded, n, 3)
    assert within(v["partially_refunded_orders"], r.partially_refunded, n, 3)
    obs = T.observed_defects(out)
    assert within(obs["D1"], r.refunded + r.partially_refunded, n, 5)
    assert within(obs["D10"], r.orphan, n * 1.11, 5) and within(obs["D9"], r.test_order, n * 1.11, 5)
    assert 0.0005 < obs["D4"] / 11_126 < 0.008


def test_evidence_groups_are_stated_for_every_defect():
    assert set(EVIDENCE) == set(DEFECTS) and set(EVIDENCE.values()) == {"reviewed", "partly_reviewed", "general"}
    assert {d for d, e in EVIDENCE.items() if e == "reviewed"} == {"D1", "D2"}


# ---- the tables ------------------------------------------------------------------------------------------------------------------------


def test_orders_flat_has_a_unique_row_id_and_exact_money(s_run):
    out, _ = s_run
    assert q(out, "SELECT count(*), count(DISTINCT row_id), count(row_id) FROM FLAT")[0][0] == q(out, "SELECT count(DISTINCT row_id) FROM FLAT")[0][0]
    types = dict(q(out, "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM FLAT)"))
    assert all(types[c] == "DECIMAL(12,2)" for c in ("subtotal", "delivery_fee", "service_fee", "discount_amount", "tax", "tip_amount", "total_amount"))
    assert q(out, "SELECT count(*) FROM FLAT WHERE NOT is_reversal AND subtotal + delivery_fee + service_fee + tax - discount_amount + tip_amount <> total_amount") == [(0,)]


def test_the_normalised_tables_agree_with_each_other(s_run):
    out, _ = s_run
    con = duckdb.connect()
    for t in TABLES:
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM '{out / (t + '.parquet')}'")
    assert con.execute("SELECT count(*) FROM orders WHERE NOT is_reversal AND row_id % 4 = 0 AND items_count <> (SELECT count(*) FROM order_items i WHERE i.order_id = orders.order_id)").fetchone() == (0,)
    assert con.execute("SELECT (SELECT count(*) FROM orders_flat), (SELECT count(*) FROM orders)").fetchone()[0] == con.execute("SELECT count(*) FROM orders").fetchone()[0]
    assert con.execute("SELECT (SELECT count(*) FROM payments WHERE kind = 'refund'), (SELECT count(DISTINCT order_id) FROM orders WHERE is_reversal)").fetchone() == tuple([con.execute("SELECT count(DISTINCT order_id) FROM orders WHERE is_reversal").fetchone()[0]] * 2)
    assert con.execute("SELECT count(*) FROM orders_flat f JOIN restaurants r USING (restaurant_id) WHERE r.restaurant_name <> f.restaurant_name").fetchone() == (0,)


def test_sensitive_looking_columns_are_obviously_fake(s_run):
    out, _ = s_run
    assert q(out, "SELECT count(*) FROM FLAT WHERE customer_email NOT SIMILAR TO 'user_[0-9]{7}@example.invalid'") == [(0,)]
    assert q(out, "SELECT count(*) FROM FLAT WHERE courier_phone IS NOT NULL AND courier_phone NOT SIMILAR TO '\\+1-555-01[0-9]{2}'") == [(0,)]
    streets = "|".join(ref.STREETS)
    assert q(out, f"SELECT count(*) FROM FLAT WHERE NOT regexp_matches(delivery_address, '^[0-9]{{1,3}} ({streets}) Street$')") == [(0,)]
    cols = [r[0] for r in q(out, "SELECT column_name FROM (DESCRIBE SELECT * FROM FLAT)")]
    assert not {"name", "customer_name", "lat", "lng", "latitude", "longitude", "postal_code", "zip"} & set(cols)


def test_everything_is_labelled_synthetic(s_run):
    _, m = s_run
    b = m.body
    assert b["synthetic"] is True and b["notice"].startswith("SYNTHETIC DATA") and b["schema_name"] == "compass_synthetic_food_delivery"
    assert set(b["table_comments"]) == set(TABLES) and all(c.startswith("SYNTHETIC DATA") for c in b["table_comments"].values())
    assert "not measured from any real platform" in b["rates_note"] and all(d["evidence"] in {"reviewed", "partly_reviewed", "general"} for d in b["defects"].values())


def test_the_generator_uses_no_network_model_or_warehouse():
    pkg = Path(__file__).resolve().parent.parent / "reference_stack/synthetic"
    text = " ".join(p.read_text() for p in pkg.glob("*.py"))
    assert not re.search(r"\b(requests|urllib|httpx|snowflake|databricks|complete_json|openai|anthropic)\b", text)


# ---- G4: the pinned report ----------------------------------------------------------------------------------------------------------


def test_the_pinned_report_equals_the_generator():
    assert REPORT.read_text() == render_report(), "regenerate with: python -m reference_stack.synthetic.report"


@pytest.mark.skipif(not __import__("os").environ.get("SYNTHETIC_SLOW"), reason="tier M takes about half a minute; set SYNTHETIC_SLOW=1")
def test_tier_m_generates_and_the_two_keys_agree(tmp_path_factory):
    out, m = build(tmp_path_factory, tier="M")
    assert m.body["tables"]["orders_flat"]["rows"] > 1_000_000
    assert T.disagreements(json.loads((out / "truth.json").read_text()), T.recompute(out)) == []
