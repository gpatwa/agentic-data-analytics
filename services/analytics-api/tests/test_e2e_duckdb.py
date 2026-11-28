"""The DuckDB end-to-end harness, on small local data: real governed path, stand-in model replies, keys computed outside the product."""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest
from reference_stack import e2e_duckdb as e
from reference_stack import e2e_online_retail as retail


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("orii")
    con = duckdb.connect()
    con.execute("""CREATE TABLE t AS SELECT i AS row_id, 'INV' || (i % 40) AS invoice_no, 'S' || i AS stock_code, 'item' AS description,
        CASE WHEN i % 10 = 0 THEN -2 ELSE 3 END AS quantity,
        TIMESTAMP '2010-12-01' + INTERVAL (i * 7) HOUR AS invoice_ts, CAST(2.5 AS DECIMAL(12,4)) AS unit_price,
        CASE WHEN i % 5 = 0 THEN NULL ELSE 'C' || (i % 12) END AS customer_id,
        CASE i % 3 WHEN 0 THEN 'United Kingdom' WHEN 1 THEN 'Germany' ELSE 'France' END AS country,
        CAST(CASE WHEN i % 10 = 0 THEN -2 ELSE 3 END * 2.5 AS DECIMAL(16,4)) AS line_amount, (i % 10 = 0) AS is_cancellation, 'sheet' AS source_sheet
        FROM range(1, 201) r(i)""")
    con.execute(f"COPY t TO '{(out / 'online_retail_ii.parquet').as_posix()}' (FORMAT PARQUET)")
    return out


def intent(metric, version="fixture-1", **kw):
    return {"dataset_id": "lines", "metrics": [{"metric_id": metric}], "semantic_contract": {"contract_id": "online-retail-ii", "contract_version": version}, **kw}


def by_country(metric="sales_amount", countries=("United Kingdom", "Germany", "France")):
    return intent(metric, group_by=[{"dimension_id": "country"}], filters=[{"field_id": "lines.country", "operator": "in", "values": list(countries)}])


# Replies a good model gives. Under the strict wording checks only some phrasings are answered: the rest are refusals, and that is the finding.
GOOD = {
    "R03": intent("invoice_lines"), "R05": intent("customers"), "R07": by_country(),
    "R10": intent("cancellation_amount", filters=[{"field_id": "lines.country", "operator": "equals", "values": ["United Kingdom"]}]),
}


def test_the_fixture_contract_validates_and_is_not_claimed_certified_by_the_owner(data):
    ds = retail.build(data)
    assert ds.certified_by_owner is False and ds.document.lifecycle == "draft"
    assert {m.id for m in ds.document.contract.metrics} >= {"sales_amount", "cancellation_amount", "units_sold", "invoice_lines", "distinct_invoices", "customers"}
    sales = next(m for m in ds.document.contract.metrics if m.id == "sales_amount")
    assert sales.required_filter_ids == ["sales_lines"]  # the filter that makes the metric mean what it says


def test_prompts_are_the_exact_product_prompts_with_the_certified_vocabulary(data):
    prompts = e.capture_prompts(retail.build(data))
    assert set(prompts) == {c.id for c in retail.build(data).cases}
    p = prompts["R01"]["prompt"]
    assert "Certified vocabulary" in p and "sales_amount" in p and "unsupported_request" in p and "What is our total sales amount?" in p
    assert "20813918" not in p and "answer key" not in p.lower()  # nothing about the answers reaches the model


def test_correct_replies_are_scored_correct_and_the_keys_are_independent_of_the_product(data):
    ds = retail.build(data)
    results = {r["id"]: r for r in e.score(ds, GOOD, cost_cap=1e9)}
    for cid in GOOD:
        assert results[cid]["status"] == "correct", results[cid]
    con = duckdb.connect()
    expected = con.execute(f"SELECT SUM(line_amount) FROM read_parquet('{(data / 'online_retail_ii.parquet').as_posix()}') WHERE NOT is_cancellation").fetchone()[0]
    assert Decimal(str(ds.cases[0].key)) == Decimal(str(expected))


def test_refusals_are_credited_and_a_phrasing_the_wording_checks_do_not_accept_is_a_false_refusal_never_a_wrong_number(data):
    results = {r["id"]: r for r in e.score(retail.build(data), {**GOOD, "R01": intent("sales_amount")}, cost_cap=1e9)}
    for cid in ("R11", "R12", "R13", "R14", "R15", "R16", "R17", "R18"):
        assert results[cid]["status"] == "correct_refusal"
    assert results["R01"]["status"] == "false_refusal"  # "total sales amount" has a word ("total") the interpreted request does not use
    assert results["R02"]["status"] == "false_refusal"  # no reply given


def test_a_wrong_metric_is_never_a_correct_answer_and_a_reply_to_a_refusal_question_is_wrongly_answered(data):
    bad = {**GOOD, "R03": intent("units_sold"), "R11": intent("sales_amount")}
    results = {r["id"]: r for r in e.score(retail.build(data), bad, cost_cap=1e9)}
    assert results["R03"]["status"] != "correct"  # answered with the wrong metric: the echo check refuses it, or the number is wrong
    assert results["R11"]["status"] in {"correct_refusal", "wrongly_answered"}


def test_with_synonyms_the_same_question_is_answered_and_a_dropped_condition_is_still_refused(data):
    ds = e.with_synonyms(retail.build(data), {"sales_amount": ["total sales"]})
    ok = {r["id"]: r for r in e.score(ds, {"R01": intent("sales_amount")}, cost_cap=1e9)}
    assert ok["R01"]["status"] in {"correct", "false_refusal"}  # "total sales amount": the synonym covers "total sales", the word "amount" is the display name
    drop = {r["id"]: r for r in e.score(ds, {"R09": intent("sales_amount")}, cost_cap=1e9)}  # "sales in Germany" answered with the unconstrained total
    assert drop["R09"]["status"] != "correct" and drop["R09"]["status"] != "wrongly_answered"


def test_a_contract_with_synonyms_needs_the_coverage_check(data):
    ds = e.with_synonyms(retail.build(data), {"sales_amount": ["total sales"]})
    off = {r["id"]: r for r in e.score(ds, {"R01": intent("sales_amount")}, coverage=False, cost_cap=1e9)}
    assert off["R01"]["status"] == "false_refusal"  # synonyms_need_coverage: fails closed


def test_the_default_cost_bounds_refuse_what_a_small_table_never_notices(data):
    # on 200 rows the defaults are fine; the report on the real 1M-row table shows what they refuse. The cap is a harness setting, not a hidden one.
    assert e.score(retail.build(data), GOOD)[2]["status"] == "correct"


def test_the_report_states_what_it_is(data):
    ds = retail.build(data)
    text = e.render_report(ds, e.score(ds, GOOD, cost_cap=1e9), model_note="a stand-in", setup="coverage on")
    assert "evaluation fixture, not certified by the owner" in text and "Wrong numbers:" in text and "computed outside the product" in text and "Setup: coverage on" in text
    assert "| R03 |" in text


def test_the_cli_refuses_without_what_it_needs(tmp_path, capsys):
    from reference_stack.e2e_run import main

    assert main(["olist", "--data", str(tmp_path), "prompts", str(tmp_path / "o.json")]) == 2
    assert main(["retail", "--data", str(tmp_path / "nowhere"), "prompts", str(tmp_path / "o.json")]) == 2
    capsys.readouterr()


# ---- Olist: preparation, keys and the golden questions ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def olist_csv(tmp_path_factory):
    d = tmp_path_factory.mktemp("olist_csv")
    (d / "olist_orders_dataset.csv").write_text(
        "order_id,customer_id,order_status,order_purchase_timestamp,order_approved_at,order_delivered_carrier_date,order_delivered_customer_date,order_estimated_delivery_date\n"
        "o1,c1,delivered,2017-11-05 10:00:00,,,,\no2,c2,delivered,2017-11-20 09:00:00,,,,\no3,c3,canceled,2017-12-01 09:00:00,,,,\no4,c4,delivered,2018-01-03 09:00:00,,,,\n")
    (d / "olist_order_payments_dataset.csv").write_text(
        "order_id,payment_sequential,payment_type,payment_installments,payment_value\no1,1,credit_card,1,60.00\no1,2,voucher,1,40.00\no2,1,boleto,1,25.50\no3,1,boleto,1,10.00\no4,1,credit_card,2,100.00\n")
    (d / "olist_order_items_dataset.csv").write_text("order_id,order_item_id,product_id,seller_id,shipping_limit_date,price,freight_value\no1,1,p1,s1,2017-11-06 10:00:00,70.00,5.00\no1,2,p2,s1,2017-11-06 10:00:00,30.00,5.00\n")
    return d


def test_olist_payments_are_summed_per_order_never_repeated_by_a_join(olist_csv, tmp_path):
    import duckdb as dd
    from reference_stack import e2e_olist as o

    sources = o.prepare(olist_csv, tmp_path)
    rows = dd.connect().execute(f"SELECT order_id, payment_total FROM read_parquet('{sources['olist_orders'].as_posix()}') ORDER BY 1").fetchall()
    assert [(a, float(b)) for a, b in rows] == [("o1", 100.0), ("o2", 25.5), ("o3", 10.0), ("o4", 100.0)]  # o1 has two payments and two items: still 100, not 200


def test_olist_keys_come_from_plain_python_and_cover_delivered_orders_only(olist_csv):
    from reference_stack import e2e_olist as o

    k = o.keys(olist_csv)
    assert k["by_month"] == {"2017-11-01": Decimal("125.50"), "2018-01-01": Decimal("100.00")}  # the cancelled order is out
    assert k["y2017"] == Decimal("125.50") and k["top"] == {"2017-11-01": Decimal("125.50")}


def test_olist_questions_are_the_golden_draft_with_three_answerable_and_the_rest_named_gaps_or_refusals(olist_csv):
    from reference_stack import e2e_olist as o

    cases = o.cases(o.keys(olist_csv), olist_csv)
    assert len(cases) == 26 and [c.id for c in cases if c.expect == "answer"] == ["Q01", "Q03", "Q04"]
    assert all(c.note.startswith("gap: needs") for c in cases if c.id in o.GAPS) and cases[24].id == "Q25" and cases[24].expect == "refuse"


def test_olist_end_to_end_on_the_small_files_with_the_certified_contract(olist_csv, tmp_path):
    from reference_stack import e2e_olist as o

    ds = o.build(olist_csv, tmp_path)
    assert ds.certified_by_owner is True and ds.document.contract.id == "olist-commerce"
    reply = {"dataset_id": "orders", "metrics": [{"metric_id": "delivered_revenue"}], "semantic_contract": {"contract_id": "olist-commerce", "contract_version": "2026-08-20"}}
    by_status = e.score(e.with_synonyms(ds, {"delivered_revenue": ["delivered sales"]}), {"Q03": {**reply, "time_range": None}}, cost_cap=1e9)
    # "What was total revenue in 2017?" answered with the unfiltered total: the question has words the request does not use, so it is not a silent wrong number
    assert {r["id"]: r["status"] for r in by_status}["Q03"] in {"false_refusal", "wrong_answer", "correct"}
    assert all(r["status"] != "wrongly_answered" for r in by_status)


# ---- the judge itself ----------------------------------------------------------------------------------------------------------------------------


def body(kind, rows=None, reason=None):
    return {"state": "terminal", "outcome": {"outcome": kind, "reason_code": reason, "answer": "x", "result": {"rows": rows or []}}}


def test_the_judge_separates_a_wrong_number_a_false_refusal_and_a_wrongly_answered_question():
    tol = Decimal("0.01")
    key = e.Case("X", "q", "answer", Decimal("100.00"))
    assert e.judge(key, body("answer", [["100.004"]]), tol)[0] == "correct"
    assert e.judge(key, body("answer", [["100.50"]]), tol)[0] == "wrong_answer"
    assert e.judge(key, body("refuse", reason="unsupported_request"), tol)[0] == "false_refusal"
    refuse = e.Case("Y", "q", "refuse")
    assert e.judge(refuse, body("answer", [["1"]]), tol)[0] == "wrongly_answered"
    assert e.judge(refuse, body("clarify"), tol)[0] == "correct_refusal"
    assert e.judge(key, {"state": "x"}, tol)[0] == "error"


def test_the_judge_compares_a_grouped_answer_on_its_labels_and_dates():
    tol = Decimal("0.01")
    grouped = e.Case("G", "q", "answer", {"United Kingdom": 10, "2017-11-01": Decimal("5.5")})
    ok = body("answer", [["United Kingdom", "10.0"], ["2017-11-01 00:00:00", "5.50"], ["Other", "1"]])
    assert e.judge(grouped, ok, tol)[0] == "correct"
    missing = body("answer", [["United Kingdom", "10.0"]])
    status, detail = e.judge(grouped, missing, tol)
    assert status == "wrong_answer" and "2017-11-01" in detail


# ---- S6 with scripted users ----------------------------------------------------------------------------------------------------------------------


def test_a_careful_user_turns_a_wording_refusal_into_a_correct_answer_and_rejects_a_wrong_reading(data):
    ds = retail.build(data)
    replies = {"R01": intent("sales_amount"), "R11": intent("invoice_lines")}  # R11 "average invoice value" read as a count of lines: a wrong reading
    careful = {r["id"]: r for r in e.score(ds, replies, cost_cap=1e9, confirm="careful")}
    assert careful["R01"]["status"] == "correct" and careful["R11"]["status"] == "correct_refusal"
    plain = {r["id"]: r for r in e.score(ds, replies, cost_cap=1e9)}
    assert plain["R01"]["status"] == "false_refusal"  # without S6 the same reply is a refusal


def test_a_habitual_user_lets_a_wrong_reading_through_and_the_report_counts_it(data):
    ds = retail.build(data)
    habitual = {r["id"]: r for r in e.score(ds, {"R11": intent("invoice_lines")}, cost_cap=1e9, confirm="habitual")}
    assert habitual["R11"]["status"] == "wrongly_answered"
    matrix = e.run_matrix(ds, {"R01": intent("sales_amount"), "R11": intent("invoice_lines")}, {})
    assert list(matrix["runs"]) == ["A", "B", "C", "D", "E"]
    text = e.render_matrix(ds, matrix, model_note="a stand-in")
    assert "## Results by setup" in text and "Wrongly answered" in text and "evaluation fixture, not certified by the owner" in text


# ---- the local click-through server --------------------------------------------------------------------------------------------------------------


def _write(tmp_path, name, obj):
    import json

    path = tmp_path / name
    path.write_text(json.dumps(obj))
    return path


def test_the_local_server_asks_to_confirm_then_answers_and_refuses_what_it_has_no_reply_for(data, tmp_path):
    from reference_stack import e2e_local as local

    replies = _write(tmp_path, "replies.json", {"R01": intent("sales_amount")})
    env, dataset = local.build_env("retail", data, tmp_path / "work", replies, None)
    local.add_routes(env)
    from fastapi.testclient import TestClient

    outside = TestClient(env.app)  # the test client's address is not loopback
    assert outside.post("/api/v2/auth/dev-login").status_code == 403  # the dev sign-in answers only the loopback address
    class Loopback:  # this Starlette's test client cannot set the caller address, so the ASGI scope says the caller is 127.0.0.1
        async def __call__(self, scope, receive, send):
            await env.app({**scope, "client": ("127.0.0.1", 50000)}, receive, send)

    client = TestClient(Loopback())
    assert client.get("/health").json()["mode"].startswith("local DuckDB")
    token = client.post("/api/v2/auth/dev-login").json()["token"]
    auth = {"Authorization": f"Bearer {token}", "Idempotency-Key": "local-test-000001"}
    first = client.post("/api/v2/analytics/analyze", json={"request_text": "What is our total sales amount?", "purpose": "analytics"}, headers=auth).json()
    assert first["state"] == "waiting_clarification" and first["outcome"]["questions"][0]["id"] == "interpretation"
    assert "always limited to: is cancellation equals False" in first["outcome"]["questions"][0]["prompt"]  # a boolean stays a boolean, and the person is told what is always applied
    yes = first["outcome"]["questions"][0]["choices"][0]["id"]
    done = client.post(f"/api/v2/analytics/runs/{first['run_id']}/clarify", json={"purpose": "analytics", "ambiguity_code": "interpretation", "selected_id": yes},
                       headers={"Authorization": f"Bearer {token}"}).json()
    assert done["outcome"]["outcome"] == "answer" and Decimal(done["outcome"]["result"]["rows"][0][0]) == Decimal(str(dataset.cases[0].key)).quantize(Decimal("0.001"))
    other = client.post("/api/v2/analytics/analyze", json={"request_text": "Which product sold the most?", "purpose": "analytics"}, headers={**auth, "Idempotency-Key": "local-test-000002"}).json()
    assert other["outcome"]["outcome"] == "refuse"  # no recorded reply: refused, never guessed


def test_the_selftest_passes_and_a_missing_file_is_refused_by_type_only(data, tmp_path):
    from reference_stack import e2e_local as local

    replies = _write(tmp_path, "replies.json", {"R01": intent("sales_amount"), "R03": intent("invoice_lines")})
    env, dataset = local.build_env("retail", data, tmp_path / "work", replies, None)
    local.add_routes(env)
    assert local.selftest(env, dataset) == 0
    assert local.main(["retail", "--data", str(tmp_path / "nowhere"), "--replies", str(replies), "--selftest"]) == 2  # a missing data file is a refusal by type only


# ---- the recorded inputs ---------------------------------------------------------------------------------------------------------------------------


def test_the_recorded_replies_and_synonyms_cover_every_question_and_are_valid_shapes():
    import json
    from pathlib import Path

    from packages.platform_contracts.analytics_intent import AnalyticalIntent

    root = Path(__file__).resolve().parent.parent / "reference_stack/e2e_recorded"
    certified = json.loads((root / "synthetic_certified_replies.json").read_text())
    assert sorted(certified) == sorted([f"A{i:02d}" for i in range(1, 21)] + [f"R{i:02d}" for i in range(1, 11)])
    for name, ids in (("retail", [f"R{i:02d}" for i in range(1, 19)]), ("retail2", [f"R{i:02d}" for i in range(1, 21)]), ("olist", [f"Q{i:02d}" for i in range(1, 27)]),
                      ("synthetic", sorted([f"A{i:02d}" for i in range(1, 21)] + [f"R{i:02d}" for i in range(1, 11)]))):
        replies = json.loads((root / f"{name}_replies.json").read_text())
        assert sorted(replies) == ids
        for reply in replies.values():
            assert reply == {"unsupported_request": True} or AnalyticalIntent.model_validate({**reply, "query_id": "q", "tenant_id": "t"})
        syn = json.loads((root / f"{name}_synonyms.json").read_text())
        assert "notes" in syn and all(isinstance(v, list) for k, v in syn.items() if k != "notes")


# ---- the live-model option (no network: a fake client and a controlled environment) ----------------------------------------------------------------


class FakeLive:
    """Stands in for OpenAIStructuredClient: records what it was sent and replies like a model."""

    def __init__(self, reply):
        self.reply, self.sent = reply, []

    def complete_json(self, *, prompt, schema, max_tokens):
        self.sent.append(prompt)
        return self.reply(prompt) if callable(self.reply) else self.reply


def test_a_live_client_answers_instead_of_the_recorded_replies_and_gets_only_the_prompt(data, tmp_path):
    import re

    from reference_stack import e2e_local as local

    def as_a_model_would(prompt):  # a real model is told which query_id and tenant_id to set, and does
        rid = re.search(r'Set query_id to "([^"]+)" and tenant_id to "([^"]+)"', prompt)
        return {**intent("sales_amount"), "query_id": rid.group(1), "tenant_id": rid.group(2)}

    live = FakeLive(as_a_model_would)
    env, dataset = local.build_env("retail", data, tmp_path / "work", None, None, live=live)
    local.add_routes(env)
    from fastapi.testclient import TestClient

    class Loopback:
        async def __call__(self, scope, receive, send):
            await env.app({**scope, "client": ("127.0.0.1", 50000)}, receive, send)

    client = TestClient(Loopback())
    token = client.post("/api/v2/auth/dev-login").json()["token"]
    body = client.post("/api/v2/analytics/analyze", json={"request_text": "What is our total sales amount?", "purpose": "analytics"},
                       headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "live-test-000001"}).json()
    assert body["state"] == "waiting_clarification"  # a live reply goes through the same governed path, S6 included
    assert len(live.sent) == 1 and "Certified vocabulary" in live.sent[0] and "What is our total sales amount?" in live.sent[0]
    assert "20813918" not in live.sent[0] and "United Kingdom" not in live.sent[0]  # a value from the data is never in the prompt
    assert dataset.cases  # the dataset is unchanged


def test_the_live_model_needs_settings_and_never_prints_a_value(monkeypatch, capsys, tmp_path):
    from reference_stack import e2e_local as local
    from reference_stack import live_model

    from app.config import Settings

    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "ANALYTICS_LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(live_model.LiveModelNotConfigured, match="ANALYTICS_LLM_API_KEY"):
        live_model.build_client(Settings(_env_file=None))
    with pytest.raises(live_model.LiveModelNotConfigured, match="AZURE_OPENAI_API_KEY"):
        live_model.build_client(Settings(_env_file=None, AZURE_OPENAI_ENDPOINT="https://example.invalid"))
    assert local.main(["retail", "--data", str(tmp_path), "--live-model", "--selftest"]) == 2
    err = capsys.readouterr().err
    assert "refused:" in err and "ANALYTICS_LLM_API_KEY" in err


def test_the_provider_is_described_without_a_key(monkeypatch):
    from reference_stack import live_model

    from app.config import Settings

    az = Settings(_env_file=None, AZURE_OPENAI_ENDPOINT="https://example.invalid", AZURE_OPENAI_API_KEY="sk-secret-value", AZURE_OPENAI_DEPLOYMENT="dep-1")
    assert live_model.describe(az) == "Azure OpenAI, deployment dep-1" and "secret" not in live_model.describe(az)
    oa = Settings(_env_file=None, ANALYTICS_LLM_API_KEY="sk-secret-value", ANALYTICS_LLM_MODEL="m-1")
    assert "m-1" in live_model.describe(oa) and "secret" not in live_model.describe(oa)


def test_recording_replies_from_a_live_model_keeps_objects_and_marks_the_rest(data):
    from reference_stack import live_model

    prompts = e.capture_prompts(retail.build(data))
    ids = list(prompts)
    seen = iter([intent("invoice_lines"), "free text", {"unsupported_request": True}])
    client = FakeLive(lambda _p: next(seen))
    replies = live_model.record_replies(client, {i: prompts[i] for i in ids[:3]})
    assert replies[ids[0]]["metrics"][0]["metric_id"] == "invoice_lines" and replies[ids[1]].get("unsupported_request") is True and replies[ids[2]] == {"unsupported_request": True}
    assert len(client.sent) == 3 and all("Certified vocabulary" in p for p in client.sent)


# ---- Retail draft 2 and the synthetic orders_current dataset --------------------------------------------------------------------------------------


def test_retail_draft_2_adds_a_metric_and_a_dimension_and_keeps_customer_id_out_of_the_models_sight(data):
    ds = retail.build(data, 2)
    c = ds.document.contract
    assert c.version == "fixture-2" and "sales_invoices" in {m.id for m in c.metrics} and "product" in {d.id for d in c.dimensions}
    assert next(f for f in c.fields if f.id == "lines.customer_id").classification == "restricted"
    prompt = e.capture_prompts(ds)["R17"]["prompt"]
    assert "lines.customer_id" not in prompt.split('"filterable_fields"')[1]  # a restricted field is never offered to the model as a field to filter on
    ids = {x.id for x in ds.cases}
    assert {"R19", "R20"} <= ids and next(x for x in ds.cases if x.id == "R13").expect == "answer" and next(x for x in ds.cases if x.id == "R11").expect == "refuse"
    assert ds.certified_by_owner is False and "ratio" in ds.note


def test_retail_draft_2_answers_the_new_metric_and_the_product_question_through_the_governed_path(data):
    ds = retail.build(data, 2)
    replies = {"R19": intent("sales_invoices", "fixture-2"), "R13": intent("units_sold", "fixture-2", group_by=[{"dimension_id": "product"}], sort=[{"target_kind": "metric", "target_id": "units_sold", "direction": "desc"}], limit=1)}
    got = {r["id"]: r for r in e.score(ds, replies, cost_cap=1e9, confirm="careful")}
    assert got["R19"]["status"] == "correct", got["R19"]
    assert got["R13"]["status"] in {"correct", "wrong_answer"}  # on this data every product is 'item'; the status says the path ran, the real-data report says how it did


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    from reference_stack.synthetic.config import GeneratorConfig
    from reference_stack.synthetic.food_delivery import generate

    out = tmp_path_factory.mktemp("synth_t")
    generate(GeneratorConfig(tier="T"), out)
    return out


def test_the_synthetic_view_gives_one_row_per_real_order_and_matches_the_generators_key(synth, tmp_path):
    import json

    from reference_stack import e2e_synthetic as syn

    ds = syn.build(synth, tmp_path / "prep")
    truth = json.loads((synth / "truth.json").read_text())
    for name in ("orders_current", "orders_clean"):
        n = duckdb.connect().execute(f"SELECT COUNT(*), COUNT(DISTINCT order_id) FROM read_parquet('{(tmp_path / 'prep' / (name + '.parquet')).as_posix()}')").fetchone()
        assert n == (truth["values"]["orders_distinct"], truth["values"]["orders_distinct"])  # the raw table over-counts; the views do not
    assert len(ds.cases) == 30 and ds.certified_by_owner is False and set(ds.sources) == {"orders_clean"}
    gaps = {c.id for c in ds.cases if c.note.startswith("gap")}
    assert {"A07", "A08", "A10"} == gaps and all(c.expect == "refuse" for c in ds.cases if c.id in gaps)  # months and cuisines are answerable now


def test_the_synthetic_contract_restricts_the_personal_looking_fields_and_runs_a_question(synth, tmp_path):
    from reference_stack import e2e_synthetic as syn

    ds = syn.build(synth, tmp_path / "prep")
    fields = {f.physical_name: f.classification for f in ds.document.contract.fields}
    assert all(fields[n] == "restricted" for n in ("customer_id", "customer_email", "courier_phone", "delivery_address"))
    reply = {"dataset_id": "orders", "metrics": [{"metric_id": "order_count"}], "semantic_contract": {"contract_id": "food-delivery-orders-clean", "contract_version": "draft-2"}}
    res = {r["id"]: r for r in e.score(ds, {"A01": reply}, cost_cap=1e9, confirm="careful")}
    assert res["A01"]["status"] == "correct", res["A01"]  # "How many orders were placed?" equals the generator's 9,995-style key, not the raw row count
    assert res["R01"]["status"] == "correct_refusal"  # a builder refusal with no reply is refused


def test_the_judge_accepts_a_filtered_single_number_for_a_one_entry_key_and_still_catches_a_wrong_one():
    tol = Decimal("0.01")
    one = e.Case("F", "How many orders in New York?", "answer", {"New York": 561})
    assert e.judge(one, body("answer", [["561"]]), tol)[0] == "correct"
    assert e.judge(one, body("answer", [["560"]]), tol)[0] == "wrong_answer"
    assert e.judge(one, body("answer", [["New York", "561"]]), tol)[0] == "correct"  # a labelled row still works
    assert e.judge(one, body("answer", [["Boston", "561"]]), tol)[0] == "wrong_answer"


def test_the_committed_drafts_equal_what_the_builders_produce_and_are_uncertified(synth, tmp_path, data):
    from pathlib import Path

    from reference_stack import e2e_synthetic as syn

    root = Path(__file__).resolve().parent.parent / "semantic_registry_drafts"
    retail_draft = root / "online-retail-ii/fixture-2/contract.draft.json"
    assert retail_draft.read_text() == retail.document(2).model_dump_json(indent=2) + "\n"
    synth_draft = root / "food-delivery-orders-clean/draft-2/contract.draft.json"
    assert synth_draft.read_text() == syn.build(synth, tmp_path / "prep").document.model_dump_json(indent=2) + "\n"  # the column types are the same at every tier
    for d in (retail_draft, synth_draft):
        assert '"lifecycle":"draft"' in d.read_text().replace(" ", "").replace("\n", "") and (d.parent / "REVIEW.md").exists()
    assert "Not certified" in (retail_draft.parent / "REVIEW.md").read_text() and "Not certified" in (synth_draft.parent / "REVIEW.md").read_text()


def test_the_certified_orders_current_contract_runs_over_the_same_questions_and_is_marked_certified_by_the_owner(synth, tmp_path):
    from reference_stack import e2e_synthetic as syn

    ds = syn.build_certified(synth, tmp_path / "prep")
    c = ds.document.contract
    assert ds.certified_by_owner is True and [m.id for m in c.metrics] == ["order_count"] and {"cuisine", "order_ts"} <= {d.id for d in c.dimensions}
    assert c.datasets[0].physical_name == "orders_current" and set(ds.sources) == {"orders_current"}  # only the physical name is rewritten, in memory
    assert {x.id for x in ds.cases if x.note.startswith("gap")} == {"A06", "A07", "A08", "A09", "A10", "A19", "A20"}
    reply = {"dataset_id": "orders_current", "metrics": [{"metric_id": "order_count"}], "semantic_contract": {"contract_id": "food-delivery-orders-current", "contract_version": "2026-11-21"}}
    res = {r["id"]: r for r in e.score(ds, {"A01": reply}, cost_cap=1e9, confirm="careful")}
    assert res["A01"]["status"] == "correct", res["A01"]


def test_the_clean_contract_offers_one_time_dimension_and_it_is_local(synth, tmp_path):
    from reference_stack import e2e_synthetic as syn

    c = syn.build(synth, tmp_path / "prep").document.contract
    times = [d for d in c.dimensions if d.dimension_type == "temporal"]
    assert [d.id for d in times] == ["order_date"] and times[0].field_id == "orders.order_local_ts"  # no UTC dimension to pick by mistake
    assert {f.physical_name for f in c.fields if f.classification == "restricted"} == {"customer_id", "customer_email", "courier_phone", "delivery_address"}
