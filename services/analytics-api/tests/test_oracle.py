"""E5: the oracle runs through the same read-only gateway, compares with the product's real answer, and can never read as a pass by failing."""

from __future__ import annotations

import pytest

from app.contract_review import EvalCase, Expect, Oracle, render_oracle_report, run_oracle_checks
from app.execution import DatabricksGateway

ALLOWED = {"cat.sch.orders"}


class Stream:
    columns = ("v",)

    def __init__(self, rows):
        self.rows = list(rows)

    def fetchmany(self, n):
        out, self.rows = self.rows[:n], self.rows[n:]
        return out

    def close(self):
        pass


class Session:
    def __init__(self, rows, log, fail=False):
        self.rows, self.log, self.fail = rows, log, fail

    def prepare(self, *, timeout_seconds):
        pass

    def explain_cost(self, sql, parameters):
        return 1.0

    def execute(self, sql, parameters):
        self.log.append(sql)
        if self.fail:
            raise RuntimeError("PERMISSION_DENIED customer 4417 row value")  # a driver message that quotes data
        return Stream(self.rows)

    def cancel(self):
        pass

    def close(self):
        pass


def gateway(rows=((100,),), fail=False):
    log: list[str] = []
    return DatabricksGateway(lambda: Session(list(rows), log, fail), allowed_tables=ALLOWED), log


def case(cid="c", sql="SELECT sum(amount) FROM cat.sch.orders", **oracle):
    return EvalCase(case_id=cid, question="What is the total order amount?", recorded_intent={}, expect=Expect(outcome="answered"),
                    oracle=Oracle(sql=sql, **oracle))


def test_a_matching_answer_is_a_match_and_runs_through_the_gateway():
    g, log = gateway()
    [r] = run_oracle_checks([case()], answer=lambda c: 100, gateway=g)
    assert r.status == "match" and str(r.oracle_value) == "100" and log == ["SELECT sum(amount) FROM cat.sch.orders"]


def test_a_wrong_answer_is_a_mismatch_never_hidden():
    g, _ = gateway()
    [r] = run_oracle_checks([case()], answer=lambda c: 99, gateway=g)
    assert r.status == "mismatch" and "mismatch" in render_oracle_report([r]) and "not certification" in render_oracle_report([r])


@pytest.mark.parametrize("kw,answer,status", [({"tolerance_abs": 0.5}, 100.4, "match"), ({"tolerance_abs": 0.5}, 100.6, "mismatch"),
                                              ({"tolerance_rel": 0.01}, 100.9, "match"), ({"tolerance_rel": 0.01}, 101.1, "mismatch")])
def test_tolerances_are_absolute_or_relative(kw, answer, status):
    g, _ = gateway()
    assert run_oracle_checks([case(**kw)], answer=lambda c: answer, gateway=g)[0].status == status


def test_an_oracle_outside_the_ceiling_or_not_read_only_is_refused_and_never_runs():
    for sql in ("SELECT sum(salary) FROM cat.sch.payroll", "DELETE FROM cat.sch.orders", "SELECT 1; SELECT 2", "SELECT * FROM cat.sch.orders, cat.sch.payroll"):
        g, log = gateway()
        [r] = run_oracle_checks([case(sql=sql)], answer=lambda c: 100, gateway=g)
        assert r.status == "oracle_failed" and r.detail == "QueryRejected" and log == [], sql


def test_a_failing_oracle_is_its_own_status_and_leaks_no_driver_message():
    g, _ = gateway(fail=True)
    [r] = run_oracle_checks([case()], answer=lambda c: 100, gateway=g)
    assert r.status == "oracle_failed" and r.detail == "ExecutionError" and "4417" not in render_oracle_report([r])


@pytest.mark.parametrize("rows", [(), ((1,), (2,)), ((1, 2),), (("text",),), ((None,),)])
def test_an_oracle_must_return_exactly_one_number(rows):
    g, _ = gateway(rows)
    assert run_oracle_checks([case()], answer=lambda c: 1, gateway=g)[0].status == "oracle_failed"


def test_a_failing_product_answer_is_reported_and_the_oracle_is_not_run():
    g, log = gateway()

    def broken(c):
        raise LookupError("no answer")

    [r] = run_oracle_checks([case()], answer=broken, gateway=g)
    assert r.status == "answer_failed" and log == []
    assert run_oracle_checks([case()], answer=lambda c: True, gateway=g)[0].status == "answer_failed"  # a boolean is not a number


def test_cases_without_an_oracle_are_skipped_and_the_report_counts_every_status():
    g, _ = gateway()
    plain = EvalCase(case_id="p", question="q", recorded_intent={}, expect=Expect(outcome="refused"))
    results = run_oracle_checks([plain, case("a"), case("b")], answer=lambda c: 100 if c.case_id == "a" else 5, gateway=g)
    assert [r.case_id for r in results] == ["a", "b"]
    assert "1 match, 1 mismatch, 0 oracle failed, 0 answer failed" in render_oracle_report(results)


def test_the_oracle_module_never_opens_a_connection_of_its_own():
    from pathlib import Path

    import app.contract_review.oracle as mod

    text = Path(mod.__file__).read_text()
    assert "connect(" not in text and "databricks" not in text.lower().replace("databricks_", "")
