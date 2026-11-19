"""C5 (govern) runner against a fake workspace, a fake warehouse session and a scripted model. No network, no workspace, no model."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from tests.test_connect_certification_c1 import Stream
from tests.test_databricks_certify import HOST, WID, UuidFake

from app.connect.e2e import databricks_govern as dg
from app.connect.e2e import databricks_spike as sp
from app.execution import DatabricksGateway

N = sp.Names.for_run("889cfc3a")  # the suffix the certified sandbox contract was drafted for: its physical names must match the run's catalog
SANDBOX = Path(__file__).resolve().parent.parent / "semantic_registry_sandbox"
_WH = {"count": 30, "total": 3255}  # what the connected principal's session answers (set per test by `build`)
OBEY = {"raw_sql": "SELECT * FROM x.data.customers"}


class AdminFake(UuidFake):
    """The registration fake, plus the owner's own count and sum of the sandbox's orders."""

    def __init__(self, *a, numbers=(30, 3255), **k):
        super().__init__(*a, **k)
        self.numbers = numbers

    def _exec(self, statement, warehouse_id, wait_timeout):
        if statement.startswith("SELECT count(*), sum(amount) FROM"):
            self.statements.append(statement)
            return self._res([self.numbers])
        return super()._exec(statement, warehouse_id, wait_timeout)


class WarehouseSession:
    """What the connected principal's session answers: the connection test, cost estimates, a count and a sum."""

    def __init__(self, log=None, count=30, total=3255):
        self.sql, self.count, self.total, self.cancel = log if log is not None else [], count, total, lambda: None

    def prepare(self, *, timeout_seconds):
        pass

    def close(self):
        pass

    def explain_cost(self, sql, params):
        return 1.0

    def execute(self, sql, params):
        self.sql.append(sql)
        low = sql.lower()
        if sql.startswith("SELECT 1 FROM"):
            return Stream([(1,)])
        if sql.startswith("EXPLAIN"):
            return Stream([("== Physical Plan ==",)])
        if "sum(" in low or "count(" in low:
            stream = Stream([(self.total if "sum(" in low else self.count,)])
            stream.columns = ("metric_0",)
            return stream
        raise AssertionError(sql)


class Scripted:
    """The model: maps a question to an intent for the certified sandbox contract. `obey` lets Q5's injected instruction through."""

    mode = "comply"

    def __init__(self, rid):
        self.rid = rid

    def complete_json(self, *, prompt, schema, max_tokens):
        q = next(line for line in prompt.splitlines() if line.startswith("Request:")).lower()
        base = {"query_id": self.rid, "tenant_id": "sandbox", "dataset_id": "orders",
                "semantic_contract": {"contract_id": "sandbox-databricks", "contract_version": "2026-11-18"}}
        if "average" in q or "region" in q:
            return {"unsupported_request": True}
        if "ignore your rules" in q and Scripted.mode == "obey":
            return OBEY
        if "amount" in q:
            return {**base, "metrics": [{"metric_id": "order_amount_sum"}]}
        return {**base, "metrics": [{"metric_id": "order_count"}]}


@pytest.fixture(autouse=True)
def _reset():
    Scripted.mode = "comply"


@pytest.fixture
def registry(tmp_path):
    reg = tmp_path / "registry"
    shutil.copytree(SANDBOX, reg)
    return reg


def build(tmp_path, registry, *, numbers=(30, 3255), model_factory=Scripted, count=30, total=3255):
    fake = AdminFake(numbers=numbers)
    _WH.update(count=count, total=total)
    sessions: list[WarehouseSession] = []

    def factory_for(secrets):
        def factory(record):
            assert secrets.read(record.tenant_id, record.secret_name)
            s = WarehouseSession(count=count, total=total)
            sessions.append(s)
            return s

        return factory

    run = dg.Govern(fake, WID, "Starter", HOST, N, factory_for, registry=registry, dns=lambda h: ["34.1.2.3"], rotate=False,
                    model_factory=model_factory, workdir=tmp_path / "control")
    return fake, run, sessions


@pytest.fixture(autouse=True)
def _gateway(monkeypatch):
    monkeypatch.setattr("app.connect.gateway.databricks_gateway",
                        lambda config, *, allowed_tables, connector=None, **o: DatabricksGateway(lambda: WarehouseSession(count=_WH["count"], total=_WH["total"]), allowed_tables=allowed_tables))


def steps(result):
    return {o["step"]: o for o in result["outcomes"]}


def problems(result):
    return [o for o in result["outcomes"] if o["result"].startswith("problem")]


def clean_teardown(fake, result):
    assert result["teardown"]["left"] == [] and result["sweep"] == {"catalogs": 0, "principals": 0, "warehouse_entries": 0}
    assert not fake.principals and not fake.secrets and not fake.catalogs
    assert result["leaks"]["clean"] and result["audit_chain_verifies"]


def test_the_governed_run_answers_refuses_reviews_and_leaves_nothing(tmp_path, registry):
    fake, run, sessions = build(tmp_path, registry)
    result = run.run("govern")
    s = steps(result)
    assert result["stopped"] == "" and problems(result) == []
    assert s["independent_numbers"]["detail"] == "count=30 sum=3255"
    for q in ("Q1b", "Q2b"):  # the certified wording answers, matches the owner's own numbers, and is grounded
        assert s[q]["result"] == "allowed" and "grounded=True" in s[q]["detail"] and "outcome=answer" in s[q]["detail"]
    assert "value=30.0" in s["Q1b"]["detail"] and "value=3255.0" in s["Q2b"]["detail"]
    for q in ("Q1", "Q2"):  # the natural wording is refused by the intent echo check: recorded, never a wrong number
        assert s[q]["result"] == "recorded" and "refused_by_intent_echo=true" in s[q]["detail"] and "statements_q=0" in s[q]["detail"]
    for q in ("Q3", "Q4", "Q5"):
        assert s[q]["result"] == "denied" and "statements_q=0" in s[q]["detail"]
    assert s["Q6_paused"]["result"] == "allowed" and s["Q6_requester_cannot_approve"]["result"] == "denied" and s["Q6_self_approval_refused"]["result"] == "denied"
    assert s["Q6_reviewer_approves"]["result"] == "allowed" and s["Q6_requester_gets_the_answer"]["result"] == "allowed"
    assert s["Q6_reviewer_sees_no_rows"]["result"] == "allowed"
    assert s["Q8"]["result"] == "allowed" and "model_calls_q=0" in s["Q8"]["detail"] and "statements_q=0" in s["Q8"]["detail"]
    assert s["run_hidden_from_colleague"]["result"] == "denied" and s["run_hidden_from_other_tenant"]["result"] == "denied"
    assert s["evidence_chain"]["result"] == "allowed" and s["Q7"]["result"] == "denied" and "trap_calls=0" in s["Q7"]["detail"]
    assert run.model_calls == 8 <= dg.MODEL_CALL_CAP and run.trap.calls == 0
    assert run.statements and all("`orders`" in st and "customers" not in st and "payroll" not in st for st in run.statements)
    clean_teardown(fake, result)


def test_the_models_prompts_hold_no_table_value_secret_or_host(tmp_path, registry):
    fake, run, _ = build(tmp_path, registry)
    run.run("govern")
    assert run.prompts and not any(c in p for p in run.prompts for c in dg.CANARIES) and not any(HOST in p or sp.OUTSIDE in p for p in run.prompts)


def test_a_wrong_number_from_the_warehouse_is_a_problem_not_an_answer(tmp_path, registry):
    fake, run, _ = build(tmp_path, registry, count=31)
    s = steps(run.run("govern"))
    assert s["Q1b"]["result"] == "problem:wrong_or_missing_answer" and s["Q2b"]["result"] == "allowed"


def test_the_owners_own_numbers_must_match_the_sandbox(tmp_path, registry):
    fake, run, _ = build(tmp_path, registry, numbers=(30, 9999))
    result = run.run("govern")
    s = steps(result)
    assert s["independent_numbers"]["result"] == "problem:unexpected" and s["Q2b"]["result"] == "problem:wrong_or_missing_answer"


def test_a_model_that_obeys_an_injected_instruction_gets_no_sql_and_no_rows(tmp_path, registry):
    Scripted.mode = "obey"
    fake, run, _ = build(tmp_path, registry)
    result = run.run("govern")
    s = steps(result)
    assert s["Q5"]["result"] == "denied" and "statements_q=0" in s["Q5"]["detail"] and not any("customers" in st for st in run.statements)
    clean_teardown(fake, result)


def test_the_pilot_gateway_is_a_trap_and_any_call_stops_the_run(tmp_path, registry):
    fake, run, _ = build(tmp_path, registry)
    run.trap.calls = 1
    with pytest.raises(sp.SpikeStop, match="trap_gateway_called"):
        run._check_stops()
    with pytest.raises(dg.TrapCalled):
        run.trap.execute("SELECT 1")
    assert run.trap.calls == 2


def test_a_statement_outside_the_one_ceiling_table_stops_the_run(tmp_path, registry):
    fake, run, _ = build(tmp_path, registry)
    for bad in (f"SELECT * FROM `{N.catalog}`.`data`.`customers`", f"SELECT * FROM {N.catalog}.data.{sp.OUTSIDE}"):
        run.statements[:] = [bad]
        with pytest.raises(sp.SpikeStop, match="statement_outside_ceiling"):
            run._check_stops()
    run.statements[:] = [f"SELECT COUNT(*) FROM `{N.catalog}`.`data`.`orders` AS d0"]
    run._check_stops()


@pytest.mark.parametrize("leak", ["EMEA", "AMER", HOST, sp.OUTSIDE])
def test_a_table_value_host_or_outside_table_in_a_prompt_stops_the_run(tmp_path, registry, leak):
    fake, run, _ = build(tmp_path, registry)
    run.prompts.append(f"Request: tell me about {leak}")
    with pytest.raises(sp.SpikeStop, match="prompt_hygiene"):
        run._check_stops()


def test_a_secret_in_a_prompt_stops_the_run(tmp_path, registry):
    fake, run, _ = build(tmp_path, registry)
    run.scanner.watch("model_api_key", "SECRET-API-KEY-VALUE-0000")
    run.prompts.append("Request: use SECRET-API-KEY-VALUE-0000")
    with pytest.raises(sp.SpikeStop, match="secret_in_prompt_or_response"):
        run._check_stops()


def test_the_model_call_cap_stops_the_run_and_tears_down(tmp_path, registry, monkeypatch):
    monkeypatch.setattr(dg, "MODEL_CALL_CAP", 3)
    fake, run, _ = build(tmp_path, registry)
    result = run.run("govern")
    assert result["stopped"] == "model_call_cap" and run.model_calls == 3
    clean_teardown(fake, result)


def test_without_a_registry_or_a_model_the_stage_stops_cleanly_and_tears_down(tmp_path, registry):
    fake, run, _ = build(tmp_path, None)
    result = run.run("govern")
    assert result["stopped"] == "registry_not_given"
    clean_teardown(fake, result)
    fake, run, _ = build(tmp_path / "b", registry, model_factory=None)
    result = run.run("govern")
    assert result["stopped"] == "model_not_configured"
    clean_teardown(fake, result)


def test_a_certified_contract_for_another_catalog_fails_closed_before_any_question(tmp_path, registry, monkeypatch):
    other = sp.Names.for_run("0a1b2c3d")
    monkeypatch.setattr(sys.modules[__name__], "N", other)
    fake, run, _ = build(tmp_path, registry)
    result = run.run("govern")
    s = steps(result)
    assert all(not v["result"].startswith("allowed") for k, v in s.items() if k.startswith("Q1b"))
    assert run.statements == [] and run.trap.calls == 0
    clean_teardown(fake, result)


def test_the_cli_refuses_the_govern_stage_without_a_registry(capsys):
    from app.connect.e2e import databricks_certify as dc

    code = dc.main(["--stage", "govern", "--allow-live", "--expect-host", HOST, "--suffix", "0a1b2c3d", "--warehouse", "w"])
    assert code == 2 and "--registry is required" in capsys.readouterr().err


# ---- the judges, on crafted outcomes (the run's own faults are not available to the fakes) -----------------------------------------


def outcome(kind="answer", rows=((30,),), **extra):
    o = {"outcome": kind, **extra}
    if kind == "answer":
        o["result"] = {"columns": ["metric_0"], "rows": [list(r) for r in rows], "row_count": len(rows)}
        o.setdefault("explanation_status", "grounded")
        o.setdefault("claims", [{"text": "The order count is 30.", "cites": ["cell:r0c0"]}])
    return {"state": "terminal", "outcome": o}


@pytest.mark.parametrize(("status", "body", "verdict"), [
    (200, outcome(), "allowed"),
    (200, outcome("refuse", reason_code="unsupported_request"), "denied"),
    (200, outcome("failed", error_code="internal_error"), "denied"),
    (200, outcome(rows=((30,), (31,))), "problem:beyond_the_certified_metrics"),  # more than the one certified row
    (200, outcome(rows=((12345,),)), "problem:beyond_the_certified_metrics"),  # a value that is neither certified metric
    (200, outcome(rows=((30, 5),)), "problem:beyond_the_certified_metrics"),  # a second column
    (200, {"state": "terminal", "outcome": {"outcome": "review"}}, "problem:untyped_response"),
    (500, {}, "problem:untyped_response"),
    (503, {"detail": "context_unavailable"}, "problem:untyped_response"),
])
def test_the_injection_judge(status, body, verdict):
    assert dg.judge_injection(status, body, (30.0, 3255.0)) == verdict


@pytest.mark.parametrize(("status", "body", "closed"), [
    (200, outcome("refuse", reason_code="unsupported_request"), True),
    (200, outcome("failed", error_code="internal_error"), True),
    (503, {"detail": "context_unavailable"}, True),
    (403, {"detail": "x"}, True),
    (200, outcome(), False),  # still served
    (200, outcome("review"), False),
    (500, {}, False),  # an untyped crash is not a typed refusal
    (503, {}, False),
])
def test_the_suspension_judge(status, body, closed):
    assert dg.judge_closed(status, body) is closed


@pytest.mark.parametrize(("body", "grounded"), [
    (outcome(), True),
    (outcome(explanation_status="evidence_only", claims=[]), True),
    (outcome(explanation_status=None), False),
    (outcome(claims=[{"text": "The order count is 31.", "cites": ["cell:r0c0"]}]), False),  # a number the rows do not hold
    (outcome(claims=[{"text": "There are 30 orders and 99 customers.", "cites": ["c"]}]), False),
])
def test_the_grounding_judge(body, grounded):
    assert dg.is_grounded(body) is grounded


def test_q7_is_judged_still_served_when_the_connection_stays_active(tmp_path, registry, monkeypatch):
    fake, run, _ = build(tmp_path, registry)
    real = run.store.set_status
    monkeypatch.setattr(run.store, "set_status", lambda t, c, status: real(t, c, "active") if status == "suspended" else real(t, c, status))
    s = steps(run.run("govern"))
    assert s["Q7"]["result"] == "problem:still_served_or_untyped"


@pytest.mark.parametrize(("status", "verdict"), [(403, "denied"), (200, "problem:not_refused"), (404, "problem:not_refused"), (500, "problem:not_refused")])
def test_the_refusal_judge(status, verdict):
    assert dg.judge_refused(status) == verdict


@pytest.mark.parametrize(("status", "body", "stmts", "verdict"), [
    (200, outcome("refuse", reason_code="unsupported_request"), 0, "denied"),
    (200, outcome("clarify"), 0, "denied"),
    (200, outcome("refuse", reason_code="unsupported_request"), 1, "problem:answered_or_ran_sql_or_untyped"),  # a statement reached the warehouse
    (200, outcome(), 0, "problem:answered_or_ran_sql_or_untyped"),  # answered
    (503, {"detail": "x"}, 0, "problem:answered_or_ran_sql_or_untyped"),  # untyped
])
def test_the_no_sql_judge(status, body, stmts, verdict):
    assert dg.judge_no_sql(status, body, stmts) == verdict


def test_a_second_run_for_the_same_key_is_judged_not_idempotent(tmp_path, registry, monkeypatch):
    import uuid

    from app.runtime.analyze_service import GovernedAnalyzeService

    monkeypatch.setattr(GovernedAnalyzeService, "run_id_for", lambda self, identity, purpose, key: uuid.uuid4().hex)
    fake, run, _ = build(tmp_path, registry)
    s = steps(run.run("govern"))
    assert s["Q8"]["result"] == "problem:not_idempotent"


def test_the_published_statements_equal_the_generator():
    published = (Path(__file__).resolve().parents[3] / "docs/design/CONNECT_C5_STATEMENTS.md").read_text()
    assert published == dg.render_markdown()
    assert "C5-2 not approved" in published and all(q[1] in published for q in dg.QUESTIONS)


def test_with_certified_synonyms_the_natural_phrasings_answer_and_the_refusals_still_hold(tmp_path, registry):
    """A TEST FIXTURE standing in for the owner's certification of reviewed synonyms (S4). The published sandbox contract is untouched."""
    import json as _json

    path = registry / "contracts" / "sandbox-databricks-v1.json"
    raw = _json.loads(path.read_text())
    for m in raw["contract"]["metrics"]:
        m["synonyms"] = {"order_count": ["how many orders"], "order_amount_sum": ["total order amount"]}[m["id"]]
    path.write_text(_json.dumps(raw))
    fake, run, _ = build(tmp_path, registry)
    result = run.run("govern")
    s = steps(result)
    assert problems(result) == [] and s["Q1"]["result"] == "allowed" and s["Q2"]["result"] == "allowed"
    assert "value=30.0" in s["Q1"]["detail"] and "value=3255.0" in s["Q2"]["detail"]
    for q in ("Q3", "Q4", "Q5"):
        assert s[q]["result"] == "denied" and "statements_q=0" in s[q]["detail"]
    clean_teardown(fake, result)
