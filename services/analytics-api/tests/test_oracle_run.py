"""L3: the recurring-oracle command, offline with fakes. Caps, exit codes, no values in the output, answer failure vs oracle failure."""

from __future__ import annotations

import json
from pathlib import Path

from test_oracle import gateway

from app.contract_review.oracle_run import main

CONTRACT = Path(__file__).resolve().parent.parent / "semantic_registry_sandbox/contracts/sandbox-databricks-v1.json"
SQL = "SELECT sum(amount) FROM cat.sch.orders"


def eval_file(tmp_path, n=1, oracle=True, contract_id="sandbox-databricks"):
    cases = [{"case_id": f"c{i}", "question": "q", "recorded_intent": {}, "expect": {"outcome": "answered"},
              **({"oracle": {"sql": SQL}} if oracle else {})} for i in range(n)]
    path = tmp_path / "eval.json"
    path.write_text(json.dumps({"contract_id": contract_id, "cases": cases}))
    return path


def run(tmp_path, *, g, answer, n=1, extra=(), **kw):
    out = tmp_path / "out.json"
    code = main(["--contract", str(CONTRACT), "--eval", str(eval_file(tmp_path, n, **kw)), "--out", str(out), *extra],
                factory=lambda doc: (g, answer))
    return code, json.loads(out.read_text()) if out.exists() else None


def test_all_match_exits_zero_and_is_not_certified(tmp_path):
    g, _ = gateway()
    code, res = run(tmp_path, g=g, answer=lambda c: 100, n=2)
    assert code == 0 and res["ok"] and res["certified"] is False and res["tally"]["match"] == 2 and res["statements_used"] == 2


def test_a_mismatch_exits_nonzero_and_is_reported(tmp_path):
    g, _ = gateway()
    code, res = run(tmp_path, g=g, answer=lambda c: 99)
    assert code == 1 and res["cases"][0]["status"] == "mismatch" and not res["ok"]


def test_answer_failure_and_oracle_failure_are_different_statuses_and_run_no_oracle_for_the_first(tmp_path):
    g, log = gateway()

    def broken(c):
        raise LookupError("customer 4417")

    code, res = run(tmp_path, g=g, answer=broken)
    assert code == 1 and res["cases"][0] == {"case_id": "c0", "status": "answer_failed", "detail": "LookupError"} and log == []
    g2, _ = gateway(fail=True)
    code, res = run(tmp_path, g=g2, answer=lambda c: 100)
    assert code == 1 and res["cases"][0]["status"] == "oracle_failed"


def test_the_statement_cap_stops_the_run_and_the_rest_are_not_run(tmp_path):
    g, log = gateway()
    code, res = run(tmp_path, g=g, answer=lambda c: 100, n=5, extra=["--max-statements", "2"])
    assert code == 1 and len(log) == 2 and res["tally"]["match"] == 2 and res["tally"]["not_run"] == 3
    assert res["complete"] is False and res["cases"][-1]["detail"] == "max_statements"


def test_the_wall_clock_cap_stops_the_run(tmp_path):
    g, log = gateway()
    ticks = iter(range(0, 1000, 10))  # every clock read advances ten seconds
    out = tmp_path / "o.json"
    code = main(["--contract", str(CONTRACT), "--eval", str(eval_file(tmp_path, 4)), "--out", str(out), "--max-seconds", "25"],
                factory=lambda d: (g, lambda c: 100), clock=lambda: float(next(ticks)))
    res = json.loads(out.read_text())
    assert code == 1 and res["tally"]["not_run"] >= 1 and res["cases"][-1]["detail"] == "max_seconds" and len(log) < 4


def test_the_counting_wrapper_refuses_a_statement_past_the_cap():
    from app.contract_review.oracle_run import _CountingGateway

    g, log = gateway()
    counting = _CountingGateway(g, 0)
    try:
        counting.execute(object())
    except RuntimeError as exc:
        assert str(exc) == "statement_cap_reached"
    else:
        raise AssertionError("the wrapper let a statement through")
    assert log == []


def test_no_values_or_driver_messages_in_the_output(tmp_path):
    g, _ = gateway(fail=True)
    _, res = run(tmp_path, g=g, answer=lambda c: 100)
    text = json.dumps(res)
    assert "4417" not in text and "PERMISSION" not in text
    g, _ = gateway(rows=((123456.78,),))
    _, res = run(tmp_path, g=g, answer=lambda c: 99887.5)
    text = json.dumps(res)
    assert "123456" not in text and "99887" not in text


def test_no_oracle_cases_is_not_a_pass(tmp_path, capsys):
    g, _ = gateway()
    code, res = run(tmp_path, g=g, answer=lambda c: 100, oracle=False)
    assert code == 1 and res["ok"] is False and "nothing was checked" in capsys.readouterr().err


def test_refusals_exit_two_by_type_only(tmp_path, capsys):
    g, _ = gateway()
    assert main(["--contract", str(CONTRACT), "--eval", str(eval_file(tmp_path))]) == 2  # no factory: no connection of its own
    assert "no gateway factory" in capsys.readouterr().err
    assert main(["--contract", str(CONTRACT), "--eval", str(eval_file(tmp_path, contract_id="other"))], factory=lambda d: (g, None)) == 2
    assert "ContractMismatch" in capsys.readouterr().err
    assert main(["--contract", str(tmp_path / "missing.json"), "--eval", str(eval_file(tmp_path))], factory=lambda d: (g, None)) == 2
    assert main(["--contract", str(CONTRACT), "--eval", str(eval_file(tmp_path)), "--max-statements", "0"], factory=lambda d: (g, None)) == 2


def test_a_factory_that_raises_is_refused_by_type_only(tmp_path, capsys):
    def bad(doc):
        raise ValueError("token abc123")

    assert main(["--contract", str(CONTRACT), "--eval", str(eval_file(tmp_path))], factory=bad) == 2
    assert "abc123" not in capsys.readouterr().err
