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
from reference_stack.scale_test.guard import ScaleGuardError
from reference_stack.scale_test.history import HistoryRow
from reference_stack.scale_test.runner import Budget, Outcome, Plan, Stop, run
from reference_stack.scale_test.warehouses import PREFIX, WarehouseAdmin
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
                out[0] = (*out[0][:-2], str(float(out[0][-2]) + 1), out[0][-1])
            return f"stmt-{next(_IDS)}", out


SIZES3 = ("2X-Small", "X-Small", "Small")


def small_plan(**kw):
    return Plan(kw.pop("sizes", ("2X-Small", "Medium")), warm_runs=1, concurrency=(2,), concurrency_statements=4, **kw)


class FakeProvider:
    """One warehouse per size, answered from DuckDB. Records how many are up at once, and every open and close."""

    def __init__(self, con, refuse=(), tamper=None):
        self.con, self.refuse, self.tamper, self.up, self.max_up, self.log, self.warehouses = con, set(refuse), tamper, set(), 0, [], {}

    def open(self, size):
        if size in self.refuse:
            return None
        self.up.add(size)
        self.max_up = max(self.max_up, len(self.up))
        self.log.append(("open", size))
        self.warehouses[size] = FakeWarehouse(self.con, self.tamper)
        return self.warehouses[size]

    def close(self, size):
        self.up.discard(size)
        self.log.append(("close", size))
        return 60.0


def go(con, statements, plan, provider=None, budget=None, times=None, run_id="r"):
    provider = provider or FakeProvider(con)

    def history_for(rows):
        t = (times or {}).get(rows[0].size, 100.0) if rows else 100.0
        return {r.statement_id: HistoryRow(t, t, 0.0, 0.0, 1.0, 1.0, False) for r in rows}
    out = Outcome()
    run(plan, statements, provider, CATALOG, SCHEMA, run_id, budget or Budget(600, 1e18), history_for, out)
    return out, provider


def test_sizes_run_one_at_a_time_each_deleted_before_the_next_and_every_statement_runs_cold_then_warm(world):
    con, statements = world
    plan = small_plan(sizes=SIZES3, min_gain=0.0)
    out, prov = go(con, statements, plan, times={"2X-Small": 300.0, "X-Small": 200.0, "Small": 100.0})
    assert prov.max_up == 1 and prov.log == [("open", "2X-Small"), ("close", "2X-Small"), ("open", "X-Small"), ("close", "X-Small"), ("open", "Small"), ("close", "Small")]
    assert len(out.rows) == plan.statement_count(len(statements)) - 2 == sum(len(w.sent) for w in prov.warehouses.values())
    cold = [r for r in out.rows if r.phase == "cold"]
    assert len(cold) == 3 * 32 and {(r.key, r.size) for r in cold} == {(s.key, z) for s in statements for z in SIZES3}
    assert all(r.mismatch is None for r in out.rows) and {r.phase for r in out.rows} == {"cold", "warm", "concurrent"}
    all_sql = [sql for w in prov.warehouses.values() for sql in w.sent]
    assert len({sql.split("*/")[0] for sql in all_sql}) == len(all_sql)  # every text unique, so none can be a cache hit by text
    assert out.uptime == {"2X-Small": 60.0, "X-Small": 60.0, "Small": 60.0} and out.stopped_escalating_after is None


def test_a_larger_size_is_tried_only_while_the_last_step_paid_off(world):
    con, statements = world
    plan = small_plan(sizes=("2X-Small", "X-Small", "Small", "Medium"), min_gain=0.15)
    out, prov = go(con, statements, plan, times={"2X-Small": 1000.0, "X-Small": 500.0, "Small": 490.0, "Medium": 1.0})
    assert [e for e in prov.log if e[0] == "open"] == [("open", "2X-Small"), ("open", "X-Small"), ("open", "Small")]
    assert out.stopped_escalating_after == "Small" and "Medium" not in out.warm_median_ms and set(out.warm_median_ms) == {"2X-Small", "X-Small", "Small"}


def test_a_flat_curve_stops_after_the_second_size(world):
    con, statements = world
    out, prov = go(con, statements, small_plan(sizes=SIZES3), times={"2X-Small": 100.0, "X-Small": 100.0, "Small": 100.0})
    assert out.stopped_escalating_after == "X-Small" and ("open", "Small") not in prov.log


def test_a_refused_size_is_skipped_and_named_and_the_rest_still_run(world):
    con, statements = world
    out, prov = go(con, statements, small_plan(sizes=SIZES3, min_gain=0.0), FakeProvider(con, refuse={"X-Small"}), times={"2X-Small": 300.0, "Small": 100.0})
    assert out.skipped == ["X-Small"] and {r.size for r in out.rows} == {"2X-Small", "Small"}


def test_the_order_is_seeded_so_a_rerun_is_identical(world):
    con, statements = world
    a = [(r.key, r.size) for r in go(con, statements, small_plan(), run_id="a")[0].rows if r.phase != "concurrent"]
    b = [(r.key, r.size) for r in go(con, statements, small_plan(), run_id="b")[0].rows if r.phase != "concurrent"]
    assert a == b


def test_one_wrong_answer_stops_the_run_deletes_the_warehouse_and_keeps_what_was_measured(world):
    con, statements = world
    prov = FakeProvider(con, tamper="COUNT(*)")
    out = Outcome()
    with pytest.raises(Stop, match="answer_mismatch") as exc:
        run(small_plan(), statements, prov, CATALOG, SCHEMA, "r", Budget(600, 1e18), lambda rows: {}, out)
    assert "value_differs" in str(exc.value) and prov.up == set() and prov.log[-1] == ("close", "2X-Small") and out.rows and out.rows[-1].mismatch == "value_differs"


def test_the_statement_budget_and_the_time_cap_stop_the_run(world):
    con, statements = world
    with pytest.raises(Stop, match="statement_budget_reached"):
        go(con, statements, small_plan(), budget=Budget(5, 1e18))
    with pytest.raises(Stop, match="time_cap_reached"):
        go(con, statements, small_plan(), budget=Budget(600, -1.0))


def test_a_statement_that_is_not_a_select_never_reaches_the_session(world):
    con, statements = world
    bad = workload.Statement(**{**statements[0].__dict__, "sql": "DELETE FROM `dev`.`compass_synthetic_food_delivery_m`.`orders_current`"})
    prov = FakeProvider(con)
    with pytest.raises(ScaleGuardError):
        go(con, [bad], small_plan(), prov)
    assert prov.warehouses["2X-Small"].sent == []


def test_the_report_uses_the_warehouse_record_excludes_cache_hits_and_needs_a_price_for_cost(world):
    con, statements = world
    rows = go(con, statements, small_plan())[0].rows
    hist = {r.statement_id: HistoryRow(500.0, 400.0, 50.0, 0.0, 1e6, 1e5, False) for r in rows}
    hist[rows[0].statement_id] = HistoryRow(1.0, 1.0, 0.0, 0.0, 0.0, 0.0, True)
    rep = report_mod.build(rows, hist, usd_per_dbu=None, uptime_seconds={"2X-Small": 600.0})
    assert rep["served_from_result_cache"] == 1 and any(e["phase"] == "cached_repeat" for e in rep["table"]) and rep["uptime_cost_usd_upper_bound"] is None and rep["no_warehouse_record"] == 0
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
            return {"res": [{"query_id": i, "duration": 10, "metrics": {"execution_time_ms": 7, "read_bytes": 100, "result_from_cache": False}} for i in ids[: self.n]]}
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
    assert out.startswith("DRY RUN: nothing was executed and no network call was made") and "at most 418 warehouse statements" in out
    assert main(["--tier", "T", "--execute"]) == 2
    assert main(["--tier", "T", "--execute", "--profile", "p", "--host", "h", "--approval-id", "no_such_approval", "--out", "/nonexistent-never-written"]) == 2


def test_release_deletes_one_warehouse_now_and_reports_how_long_it_was_up():
    api, t = FakeApi(), [0.0]
    admin = WarehouseAdmin(api, clock=lambda: t[0], sleep=lambda s: None)
    wid = admin.create("Small", "r1")
    t[0] = 90.0
    assert admin.release(wid) == 90.0 and api.warehouses == {} and admin.created == {} and admin.teardown() == []


def test_a_borrowed_baseline_needs_the_smallest_size_and_its_name_in_the_approval(capsys):
    assert main(["--tier", "T", "--sizes", "Small,Medium", "--baseline-warehouse-name", "Serverless Starter Warehouse"]) == 2
    assert "smallest size must be 2X-Small" in capsys.readouterr().err


def test_each_run_has_its_own_plan_by_a_constant_column_and_the_answer_check_ignores_it(world):
    from reference_stack.scale_test.guard import check_statement
    from reference_stack.scale_test.runner import unique
    con, statements = world
    a, b = unique(statements[0].sql, "r-1"), unique(statements[0].sql, "r-2")
    assert a != b and "'r-1' AS scale_tag" in a and a.count("scale_tag") == 1 and a.split("*/", 1)[1].count("\nFROM ") == 1
    check_statement(a, CATALOG, SCHEMA)
    out, prov = go(con, statements[:3], small_plan(sizes=("2X-Small",)))
    assert all(r.mismatch is None for r in out.rows) and "AS scale_tag" in prov.warehouses["2X-Small"].sent[0]
