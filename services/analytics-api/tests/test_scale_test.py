"""The warehouse scale-test harness, with fakes only: no network, no warehouse. Nothing here claims a live run."""

from __future__ import annotations

import itertools
import threading
from pathlib import Path

import pytest
import yaml
from reference_stack.loaders.approval import ApprovalError, check_approval
from reference_stack.loaders.databricks_cli import EngineError
from reference_stack.scale_test import history as history_mod
from reference_stack.scale_test import report as report_mod
from reference_stack.scale_test import workload
from reference_stack.scale_test.__main__ import main
from reference_stack.scale_test.guard import ScaleGuardError, check_statement
from reference_stack.scale_test.history import HistoryRow
from reference_stack.scale_test.runner import Budget, Plan, Stop, run
from reference_stack.scale_test.warehouses import PREFIX, WarehouseAdmin, create_available
from reference_stack.synthetic.config import GeneratorConfig
from reference_stack.synthetic.food_delivery import generate

CATALOG, SCHEMA = "dev", "compass_synthetic_food_delivery_m"
_IDS = itertools.count()  # the warehouse gives every statement its own id


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("tier_t")
    generate(GeneratorConfig(tier="T"), out)
    con = workload.local_connection(out)
    statements = workload.build(workload.load_contract(), con, CATALOG, SCHEMA)
    return con, statements


class FakeWarehouse:
    """Answers the compiled Databricks SQL from DuckDB, as the warehouse would: every cell comes back as text. `tamper` changes one answer."""

    def __init__(self, con, tamper: str | None = None):
        self.cursor_lock = threading.Lock()
        self.con = con.cursor()
        if not self.con.execute("SELECT 1 FROM duckdb_databases() WHERE database_name = 'dev'").fetchall():
            self.con.execute("ATTACH ':memory:' AS dev")
            self.con.execute(f"CREATE SCHEMA dev.{SCHEMA}")
            for view in ("orders_current", "orders_usd_gross"):
                self.con.execute(f'CREATE VIEW dev.{SCHEMA}.{view} AS SELECT * FROM "dev.compass_synthetic_food_delivery.{view}"')
        self.tamper, self.sent, self.n = tamper, [], 0

    def query_with_id(self, sql: str):
        self.sent.append(sql)
        with self.cursor_lock:
            self.n += 1
            rows = self.con.execute(sql.replace("`", '"').replace("DATE_TRUNC", "date_trunc")).fetchall()
            out = [tuple(None if c is None else str(c) for c in r) for r in rows]
            if self.tamper and out and self.tamper in sql:
                out[0] = (*out[0][:-1], str(float(out[0][-1]) + 1))
            return f"stmt-{next(_IDS)}", out


def small_plan(**kw):
    return Plan(("2X-Small", "Medium"), warm_runs=1, concurrency=(2,), concurrency_statements=4, **kw)


def test_workload_is_the_compilers_output_deterministic_and_records_no_text_or_value(world):
    con, statements = world
    again = workload.build(workload.load_contract(), con, CATALOG, SCHEMA)
    assert [s.text_hash for s in statements] == [s.text_hash for s in again] and len(statements) == 32
    assert {s.shape for s in statements} == {"no_dimension", "one_low_cardinality", "one_high_cardinality", "two_dimensions", "time_grain"}
    assert all(f"`{CATALOG}`.`{SCHEMA}`." in s.sql for s in statements)
    for s in statements:
        check_statement(s.sql, CATALOG, SCHEMA)
        assert set(s.public()) == {"key", "shape", "metric", "dimensions", "grains", "sql_sha256", "groups", "limit"} and "SELECT" not in str(s.public())


def test_the_guard_lets_only_a_select_on_the_target_schema_through():
    ok = f"SELECT COUNT(*) FROM `{CATALOG}`.`{SCHEMA}`.`orders_current`"
    check_statement(f"/* scale 1 */ {ok}", CATALOG, SCHEMA)
    for bad, code in [("DROP VIEW `dev`.`s`.`orders_current`", "statement_not_select"), (ok + "; SELECT 1", "statement_has_semicolon"),
                      ("SELECT 1", "statement_has_no_qualified_name"), ("SELECT COUNT(*) FROM `dev`.`other`.`t`", "statement_outside_schema"),
                      (f"WITH x AS (DELETE FROM `{CATALOG}`.`{SCHEMA}`.`t`) SELECT 1 FROM `{CATALOG}`.`{SCHEMA}`.`t`", "statement_has_forbidden_word"),
                      ("SET use_cached_result = false", "statement_not_select")]:
        with pytest.raises(ScaleGuardError, match=code):
            check_statement(bad, CATALOG, SCHEMA)


def test_the_matrix_runs_every_statement_cold_then_warm_and_counts_match_the_plan(world):
    con, statements = world
    fake = FakeWarehouse(con)
    plan = small_plan()
    rows = run(plan, statements, {s: fake for s in plan.sizes}, CATALOG, SCHEMA, "r1", Budget(600, 1e18))
    assert len(rows) == plan.statement_count(len(statements)) - 2 == len(fake.sent)
    cold = [r for r in rows if r.phase == "cold"]
    assert len(cold) == 2 * 32 and {(r.key, r.size) for r in cold} == {(s.key, z) for s in statements for z in plan.sizes}
    assert all(r.mismatch is None for r in rows) and {r.phase for r in rows} == {"cold", "warm", "concurrent"}
    assert len({sql.split("*/")[0] for sql in fake.sent}) == len(fake.sent)  # every text unique, so none can be a cache hit by text


def test_the_order_is_seeded_so_a_rerun_is_identical(world):
    con, statements = world
    plan = small_plan()
    a = [(r.key, r.size) for r in run(plan, statements, {s: FakeWarehouse(con) for s in plan.sizes}, CATALOG, SCHEMA, "a", Budget(600, 1e18)) if r.phase != "concurrent"]
    b = [(r.key, r.size) for r in run(plan, statements, {s: FakeWarehouse(con) for s in plan.sizes}, CATALOG, SCHEMA, "b", Budget(600, 1e18)) if r.phase != "concurrent"]
    assert a == b


def test_one_wrong_answer_stops_the_run_with_a_code_and_no_value(world):
    con, statements = world
    fake = FakeWarehouse(con, tamper="COUNT(*)")
    plan = small_plan()
    with pytest.raises(Stop, match="answer_mismatch") as exc:
        run(plan, statements, {s: fake for s in plan.sizes}, CATALOG, SCHEMA, "r", Budget(600, 1e18))
    assert "value_differs" in str(exc.value) and fake.n < 10


def test_the_statement_budget_and_the_time_cap_stop_the_run(world):
    con, statements = world
    plan = small_plan()
    with pytest.raises(Stop, match="statement_budget_reached"):
        run(plan, statements, {s: FakeWarehouse(con) for s in plan.sizes}, CATALOG, SCHEMA, "r", Budget(5, 1e18))
    with pytest.raises(Stop, match="time_cap_reached"):
        run(plan, statements, {s: FakeWarehouse(con) for s in plan.sizes}, CATALOG, SCHEMA, "r", Budget(600, -1.0))


def test_a_statement_that_is_not_a_select_never_reaches_the_session(world):
    con, statements = world
    fake = FakeWarehouse(con)
    bad = workload.Statement(**{**statements[0].__dict__, "sql": "DELETE FROM `dev`.`compass_synthetic_food_delivery_m`.`orders_current`"})
    with pytest.raises(ScaleGuardError):
        run(small_plan(), [bad], {s: fake for s in ("2X-Small", "Medium")}, CATALOG, SCHEMA, "r", Budget(600, 1e18))
    assert fake.sent == []


def test_the_report_uses_the_warehouse_record_excludes_cache_hits_and_needs_a_price_for_cost(world):
    con, statements = world
    plan = small_plan()
    rows = run(plan, statements, {s: FakeWarehouse(con) for s in plan.sizes}, CATALOG, SCHEMA, "r", Budget(600, 1e18))
    hist = {r.statement_id: HistoryRow(500.0, 400.0, 50.0, 0.0, 1e6, 1e5, False) for r in rows}
    hist[rows[0].statement_id] = HistoryRow(1.0, 1.0, 0.0, 0.0, 0.0, 0.0, True)
    rep = report_mod.build(rows, hist, usd_per_dbu=None, uptime_seconds={"2X-Small": 600.0})
    assert rep["excluded_served_from_cache"] == 1 and rep["uptime_cost_usd_upper_bound"] is None and rep["no_warehouse_record"] == 0
    assert all("attributable_usd_per_answer" not in e for e in rep["table"]) and report_mod.build(rows, hist, usd_per_dbu=None, uptime_seconds={}) == rep | {"uptime_cost_usd_upper_bound": None}
    priced = report_mod.build(rows, hist, usd_per_dbu=0.5, uptime_seconds={"2X-Small": 3600.0})
    assert priced["uptime_cost_usd_upper_bound"] == {"2X-Small": 2.0} and "attributable_usd_per_answer" in priced["table"][0]
    assert "| size |" in report_mod.markdown(priced) and "flat curve" in report_mod.markdown(priced)


class FakeApi:
    def __init__(self, fail_delete=False, leftover=()):
        self.calls, self.warehouses, self.fail_delete, self.leftover, self.polls = [], {}, fail_delete, list(leftover), 0

    def api(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "post":
            wid = f"w{len(self.warehouses)}"
            self.warehouses[wid] = body["name"]
            return {"id": wid}
        if method == "delete":
            if self.fail_delete:
                raise EngineError("cli_call_failed")
            self.warehouses.pop(path.rsplit("/", 1)[1])
            raise EngineError("cli_output_not_json")
        if path.endswith("/warehouses"):
            return {"warehouses": [{"name": n} for n in [*self.warehouses.values(), *self.leftover]]}
        self.polls += 1
        return {"state": "RUNNING" if self.polls > 1 else "STARTING"}


def test_teardown_deletes_what_the_run_created_and_proves_none_remains():
    api = FakeApi()
    admin = WarehouseAdmin(api, sleep=lambda s: None)
    ids = [admin.create(size, "r1") for size in ("2X-Small", "Small")]
    admin.wait_running(ids[0])
    assert all(n.startswith(PREFIX) for n in api.warehouses.values())
    assert admin.teardown() == [] and api.warehouses == {}


def test_a_warehouse_that_cannot_be_deleted_is_reported_by_name():
    api = FakeApi(fail_delete=True)
    admin = WarehouseAdmin(api, sleep=lambda s: None)
    admin.create("Small", "r1")
    assert admin.teardown() == [f"{PREFIX}small-r1"]


def test_history_is_polled_until_every_statement_appears():
    class Api:
        def __init__(self):
            self.n = 0

        def api(self, method, path, body=None):
            self.n += 1
            ids = [p.split("=")[1] for p in path.split("&") if p.startswith("filter_by.statement_ids")]
            return {"res": [{"query_id": i, "duration": 10, "metrics": {"execution_total_time_ms": 7, "read_bytes": 100, "result_from_cache": False}} for i in ids[: self.n]]}
    api, t = Api(), [0.0]
    got = history_mod.fetch(api, ["a", "b"], sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert set(got) == {"a", "b"} and got["a"].execution_ms == 7 and got["a"].from_cache is False


def _program(tmp_path: Path, scope: str, status="approved") -> Path:
    p = tmp_path / "program.yaml"
    p.write_text(yaml.safe_dump({"x": {"scale_test": {"status": status, "approved_by": "user", "scope": scope}}}))
    return p


def test_the_approval_must_name_the_target_the_warehouse_prefix_and_the_extras(tmp_path):
    scope = "host h.example dev.compass_synthetic_food_delivery_m compass-scale- create_warehouses orders_current orders_usd_gross"
    check_approval("scale_test", "h.example", CATALOG, SCHEMA, PREFIX, 45, _program(tmp_path, scope), also=("create_warehouses", "orders_current"), max_cap_minutes=45)
    with pytest.raises(ApprovalError, match="target_not_in_approval"):
        check_approval("scale_test", "h.example", CATALOG, SCHEMA, PREFIX, 45, _program(tmp_path, scope.replace("create_warehouses", "")), also=("create_warehouses",), max_cap_minutes=45)
    with pytest.raises(ApprovalError, match="cap_above_approved"):
        check_approval("scale_test", "h.example", CATALOG, SCHEMA, PREFIX, 46, _program(tmp_path, scope), max_cap_minutes=45)
    with pytest.raises(ApprovalError, match="approval_not_recorded"):
        check_approval("scale_test", "h.example", CATALOG, SCHEMA, PREFIX, 45, _program(tmp_path, scope, status="pending"), max_cap_minutes=45)


def test_the_command_is_a_dry_run_by_default_and_execute_refuses_without_its_inputs(capsys, monkeypatch):
    monkeypatch.setattr("reference_stack.loaders.databricks_cli._run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network call in a dry run")))
    assert main(["--tier", "T"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("DRY RUN: nothing was executed and no network call was made") and "warehouse statements planned: 578" in out
    assert main(["--tier", "T", "--execute"]) == 2
    assert main(["--tier", "T", "--execute", "--profile", "p", "--host", "h", "--approval-id", "no_such_approval", "--out", "/nonexistent-never-written"]) == 2


def test_a_size_the_workspace_refuses_is_skipped_and_named_not_fatal():
    class Refusing(FakeApi):
        def api(self, method, path, body=None):
            if method == "post" and body["cluster_size"] != "2X-Small":
                raise EngineError("cli_call_failed")
            return super().api(method, path, body)
    api = Refusing()
    admin = WarehouseAdmin(api, sleep=lambda s: None)
    made, skipped = create_available(admin, ("2X-Small", "Small", "Medium"), "r1")
    assert list(made) == ["2X-Small"] and skipped == ["Small", "Medium"] and admin.teardown() == []
