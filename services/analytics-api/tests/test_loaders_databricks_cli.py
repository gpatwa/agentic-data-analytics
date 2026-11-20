import json

import pytest
from reference_stack.loaders.approval import ApprovalError, check_approval
from reference_stack.loaders.databricks_cli import CapExceeded, DatabricksCliSession, EngineError, find_warehouse_id


class FakeCli:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, argv):
        self.calls.append(argv)
        code, body = self.responses.pop(0)
        return code, json.dumps(body) if not isinstance(body, str) else body


OK = {"statement_id": "s1", "status": {"state": "SUCCEEDED"}, "result": {"data_array": [["0"]]}}


def test_query_returns_rows_and_uses_profile_without_shell():
    cli = FakeCli([(0, OK)])
    s = DatabricksCliSession("p", "wh1", 900, runner=cli)
    assert s.query("SELECT 1") == [("0",)]
    assert cli.calls[0][:3] == ["databricks", "api", "post"] and "-p" in cli.calls[0]


def test_polls_until_done_then_fails_closed_without_statement_text():
    cli = FakeCli([(0, {"statement_id": "s", "status": {"state": "RUNNING"}}), (0, {"statement_id": "s", "status": {"state": "FAILED", "error": {"error_code": "BAD"}}})])
    s = DatabricksCliSession("p", "wh1", 900, runner=cli, sleep=lambda _: None)
    with pytest.raises(EngineError, match="statement_FAILED:BAD") as e:
        s.run("CREATE TABLE secret_name")
    assert "secret_name" not in str(e.value)


def test_cap_stops_the_run():
    t = iter([0, 5, 2000])
    s = DatabricksCliSession("p", "wh1", 900, runner=FakeCli([(0, OK)]), clock=lambda: next(t))
    s.run("SELECT 1")
    with pytest.raises(CapExceeded):
        s.run("SELECT 2")


def test_upload_and_nonzero_exit():
    cli = FakeCli([(1, "")])
    with pytest.raises(EngineError, match="upload_failed"):
        DatabricksCliSession("p", "w", 900, runner=cli).upload("/a", "/Volumes/c/s/staging/a")


def test_warehouse_lookup_needs_exactly_one_match():
    assert find_warehouse_id("p", "W", FakeCli([(0, [{"name": "W", "id": "i1"}])])) == "i1"
    with pytest.raises(EngineError):
        find_warehouse_id("p", "W", FakeCli([(0, [])]))


def test_approval_must_name_the_exact_target(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text("a:\n  g5:\n    status: approved\n    approved_by: user\n    scope: 'dev.sch on https://h with Wh'\n")
    check_approval("g5", "https://h", "dev", "sch", "Wh", 15, f)
    for args in (("https://other", "dev", "sch", "Wh", 15), ("https://h", "dev", "other", "Wh", 15), ("https://h", "dev", "sch", "Wh", 60)):
        with pytest.raises(ApprovalError):
            check_approval("g5", *args, program=f)
    with pytest.raises(ApprovalError):
        check_approval("missing", "https://h", "dev", "sch", "Wh", 15, f)


def test_the_recorded_approval_matches_the_planned_target():
    check_approval("synthetic_data_g5_load_tier_s", "https://dbc-b8672746-8e43.cloud.databricks.com", "dev", "compass_synthetic_food_delivery", "Serverless Starter Warehouse", 15)
