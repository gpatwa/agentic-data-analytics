"""Live-validation runner (stage S6 pre-work), exercised against a scripted fake session. No network, no account, no driver."""

import threading
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.execution import warehouse_validation as wv
from app.execution.warehouse_gateway import WarehouseCancelled, WarehouseTimeout
from app.execution.warehouse_sessions import WarehouseConfigError

SF_ENV = {
    "SNOWFLAKE_ACCOUNT": "org-acct", "SNOWFLAKE_USER": "svc-user", "SNOWFLAKE_PRIVATE_KEY_PATH": "/keys/secret-key.p8",
    "SNOWFLAKE_WAREHOUSE": "WH", "SNOWFLAKE_DATABASE": "COMPASS_VALIDATION", "SNOWFLAKE_SCHEMA": "SANDBOX",
    "SNOWFLAKE_ROLE": "COMPASS_VALIDATION_RO", "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE": "pass-secret-789",
}
DB_ENV = {
    "DATABRICKS_SERVER_HOSTNAME": "dbc-host.example", "DATABRICKS_HTTP_PATH": "/sql/1.0/warehouses/abc", "DATABRICKS_TOKEN": "tok-secret-321",
    "DATABRICKS_CATALOG": "compass_validation", "DATABRICKS_SCHEMA": "sbx_schema",
}
GOOD_SF_GRANTS = [
    ("USAGE", "DATABASE", "COMPASS_VALIDATION"), ("USAGE", "SCHEMA", "COMPASS_VALIDATION.SANDBOX"),
    ("SELECT", "TABLE", 'COMPASS_VALIDATION.SANDBOX."sales_orders"'), ("USAGE", "WAREHOUSE", "COMPASS_VALIDATION_WH"),
]
GOOD_DB_GRANTS = [("svc", "USE CATALOG", "CATALOG", "c"), ("svc", "USE SCHEMA", "SCHEMA", "s"), ("svc", "SELECT", "TABLE", "t")]


class Stream:
    def __init__(self, columns, rows):
        self.columns, self._rows, self.closed = tuple(columns), list(rows), False

    def fetchmany(self, size):
        batch, self._rows = self._rows[:size], self._rows[size:]
        return batch

    def close(self):
        self.closed = True


def expected_rows():
    return [(datetime(y, m, 1, tzinfo=timezone.utc), total) for (y, m), total in wv.expected_monthly_revenue()]


class World:
    def __init__(self, engine, **options):
        self.engine, self.o, self.sessions, self.log = engine, options, [], []


class FakeSession:
    def __init__(self, world):
        self.w, self.cancelled, self.timeout, self.closed = world, threading.Event(), None, False
        world.sessions.append(self)

    def prepare(self, *, timeout_seconds):
        self.timeout = timeout_seconds

    def explain_cost(self, sql, parameters):
        return self.w.o.get("estimate", 2.0)

    def execute(self, sql, parameters):
        self.w.log.append(sql)
        o = self.w.o
        if "SYSTEM$WAIT" in sql or "range(10000000000)" in sql:
            return self._slow()
        if sql.startswith("SHOW GRANTS TO ROLE"):
            return Stream(("created_on", "privilege", "granted_on", "name"), [("t", *g) for g in o.get("grants", GOOD_SF_GRANTS)])
        if "current_user" in sql:
            return Stream(("u",), [("svc",)])
        if sql.startswith("SHOW GRANTS ON"):
            return Stream(("Principal", "ActionType", "ObjectType", "ObjectKey"), o.get("grants", GOOD_DB_GRANTS))
        if "information_schema.columns" in sql:
            names = o.get("columns", ("id", "amount", "status", "created_at"))
            return Stream(("column_name", "data_type", "is_nullable"), [(n, "TEXT", "NO") for n in names])
        return Stream(("dimension_0", "metric_0"), o.get("rows", expected_rows()))

    def _slow(self):
        o = self.w.o
        if self.timeout == 2:
            if o.get("no_timeout"):
                return Stream((), [])
            raise WarehouseTimeout()
        if o.get("cancel_ignored"):
            return Stream((), [])
        if self.cancelled.wait(10):
            raise WarehouseCancelled()
        raise AssertionError("cancel never arrived")

    def cancel(self):
        self.cancelled.set()

    def close(self):
        self.closed = True


@pytest.fixture
def spec_for(monkeypatch):
    def make(engine, **options):
        world = World(engine, **options)
        name = "SnowflakeSession" if engine == "snowflake" else "DatabricksSession"
        monkeypatch.setattr(wv, name, lambda config: FakeSession(world))
        spec = wv.build_spec(engine, SF_ENV if engine == "snowflake" else DB_ENV)
        return spec, world

    return make


# ------------------------------------------------------------------------------------------------- expected rows


def test_the_expected_rows_come_from_the_seed_formula_alone():
    rows = wv.expected_monthly_revenue()
    assert len(rows) == 3 and [t for _, t in rows] == sorted((t for _, t in rows), reverse=True)
    assert sum(t for _, t in rows) == Decimal(4000)  # 1..100 is 5050, the multiples of five (refunded) are 1050


def test_row_matching_accepts_dates_and_decimals_and_counts_only_exact_rows():
    rows = [(d, t) for d, t in expected_rows()]
    assert wv._rows_match(rows) == 3
    assert wv._rows_match([(rows[0][0], Decimal("1")), *rows[1:]]) == 2
    assert wv._rows_match([("not a date", 1)]) == 0


# ------------------------------------------------------------------------------------------------- the checks


@pytest.mark.parametrize("engine", ["snowflake", "databricks"])
def test_every_check_passes_against_a_correct_fake(spec_for, engine):
    spec, world = spec_for(engine)
    report = wv.run_validation(spec)
    assert report.passed, [(c.name, c.code) for c in report.checks]
    assert [c.name for c in report.checks] == [
        "read_only_grants", "metadata_read", "compiled_query", "result_equivalence", "cost_ceiling", "timeout", "cancel", "no_leaks",
    ]
    assert report.statements <= 30 and all(s.closed for s in world.sessions)


@pytest.mark.parametrize(
    ("engine", "option", "stopped_at", "code"),
    [
        ("snowflake", {"grants": [*GOOD_SF_GRANTS, ("INSERT", "TABLE", "COMPASS_VALIDATION.SANDBOX.x")]}, "read_only_grants", "extra_privilege"),
        ("snowflake", {"grants": [("USAGE", "DATABASE", "OTHER_DB")]}, "read_only_grants", "extra_privilege"),
        ("snowflake", {"grants": []}, "read_only_grants", "grants_not_visible"),
        ("databricks", {"grants": [("svc", "MODIFY", "TABLE", "t")]}, "read_only_grants", "extra_privilege"),
        ("databricks", {"grants": [("someone-else", "SELECT", "TABLE", "t")]}, "read_only_grants", "grants_not_visible"),
        ("snowflake", {"columns": ("id", "amount")}, "metadata_read", "columns_differ"),
        ("snowflake", {"estimate": 50.0}, "compiled_query", "cost_over_cap"),
        ("databricks", {"rows": expected_rows()[:2]}, "result_equivalence", "result_mismatch"),
        ("databricks", {"rows": [(d, t + 1) for d, t in expected_rows()]}, "result_equivalence", "result_mismatch"),
        ("snowflake", {"estimate": 0.0}, "cost_ceiling", "estimate_zero"),
        ("snowflake", {"no_timeout": True}, "timeout", "no_timeout"),
        ("databricks", {"cancel_ignored": True}, "cancel", "finished"),
    ],
)
def test_the_first_failed_check_stops_the_run_and_the_rest_are_not_run(spec_for, engine, option, stopped_at, code):
    spec, _ = spec_for(engine, **option)
    report = wv.run_validation(spec)
    failed = report.checks[-1]
    assert (failed.name, failed.code, failed.ok) == (stopped_at, code, False)
    assert not report.passed and len(report.checks) + len(report.not_run) == 8
    assert all(c.ok for c in report.checks[:-1])


def test_the_statement_cap_stops_the_run_before_the_statement_is_sent(spec_for):
    spec, world = spec_for("snowflake")
    report = wv.run_validation(spec, max_statements=3)
    assert report.checks[-1].code == "statement_cap" and report.statements <= 3
    assert len(world.log) <= 3


def test_the_cost_ceiling_check_refuses_before_any_query_statement(spec_for):
    spec, world = spec_for("databricks", estimate=4.0)
    report = wv.run_validation(spec)
    assert report.passed
    queries = [sql for sql in world.log if "DATE_TRUNC" in sql]
    assert len(queries) == 1  # one real run, none for the refused ceiling run


def test_a_secret_that_reached_the_report_is_caught(spec_for):
    spec, _ = spec_for("snowflake")
    leaky = wv.EngineSpec(**{**spec.__dict__, "secrets": ("compiled_query",)})  # a value that the report does contain
    report = wv.run_validation(leaky)
    assert report.checks[-1].name == "no_leaks" and report.checks[-1].code == "secret_in_report"


@pytest.mark.parametrize("engine", ["snowflake", "databricks"])
def test_the_report_holds_numbers_and_codes_only(spec_for, engine):
    spec, _ = spec_for(engine)
    text = wv.render_markdown(wv.run_validation(spec))
    env = SF_ENV if engine == "snowflake" else DB_ENV
    for value in (*env.values(), "SELECT", "DATE_TRUNC", "sales_orders", "SYSTEM$WAIT", "range("):
        assert value not in text
    assert "Numbers and codes only" in text and "all checks ok" in text


def test_a_driver_message_never_reaches_the_report(spec_for, monkeypatch):
    spec, _ = spec_for("snowflake")

    class Boom(FakeSession):
        def execute(self, sql, parameters):
            raise RuntimeError("SELECT secret-value FROM private_table")

    monkeypatch.setattr(wv, "SnowflakeSession", lambda config: Boom(World("snowflake")))
    text = wv.render_markdown(wv.run_validation(wv.build_spec("snowflake", SF_ENV)))
    assert "secret-value" not in text and "private_table" not in text and "RuntimeError" in text


# ------------------------------------------------------------------------------------------------- plumbing


def test_the_query_runner_translates_named_markers_and_refuses_a_mismatch():
    seen = []

    class Capture:
        def execute(self, sql, parameters):
            seen.append((sql, parameters))
            return Stream(("a",), [(1,)])

    run = wv.session_query_runner(Capture(), "pyformat")
    assert run("SELECT a WHERE x = :table_schema AND y::int = :n", {"table_schema": "S", "n": 2}) == [(1,)]
    assert seen[0] == ("SELECT a WHERE x = %(table_schema)s AND y::int = %(n)s", {"table_schema": "S", "n": 2})
    assert wv.session_query_runner(Capture(), "named")("SELECT :a", {"a": 1}) == [(1,)] and seen[1][0] == "SELECT :a"
    with pytest.raises(ValueError):
        run("SELECT :a", {"b": 1})


def test_a_configured_name_that_is_not_a_plain_identifier_is_refused():
    with pytest.raises(WarehouseConfigError):
        wv._ident("x; DROP TABLE y")


# ------------------------------------------------------------------------------------------------- the command line


def argv(tmp_path, *extra, engine="snowflake"):
    return [engine, "--out", str(tmp_path / "r.md"), *extra]


def test_it_refuses_without_the_live_flag_and_without_a_named_target(tmp_path, capsys):
    assert wv.main(argv(tmp_path, "--expect-target", "x"), SF_ENV) == 2
    assert wv.main(argv(tmp_path, "--allow-live"), SF_ENV) == 2
    assert "approval" in capsys.readouterr().err and not (tmp_path / "r.md").exists()


def test_it_refuses_a_different_target_before_any_connection_and_echoes_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(wv, "SnowflakeSession", lambda config: pytest.fail("connected"))
    assert wv.main(argv(tmp_path, "--allow-live", "--expect-target", "other-account"), SF_ENV) == 2
    err = capsys.readouterr().err
    assert "does not match" in err and "org-acct" not in err and "other-account" not in err


def test_missing_variables_are_named_and_no_value_is_shown(tmp_path, capsys):
    assert wv.main(argv(tmp_path, "--allow-live", "--expect-target", "x"), {"SNOWFLAKE_ACCOUNT": "secret-acct"}) == 2
    err = capsys.readouterr().err
    assert "SNOWFLAKE_USER" in err and "secret-acct" not in err


def test_silly_limits_are_refused(tmp_path):
    assert wv.main(argv(tmp_path, "--allow-live", "--expect-target", "org-acct", "--max-statements", "500"), SF_ENV) == 2
    assert wv.main(argv(tmp_path, "--allow-live", "--expect-target", "org-acct", "--max-cost-mib", "0"), SF_ENV) == 2


def test_a_passing_run_writes_the_report_and_exits_zero(spec_for, tmp_path, capsys):
    spec_for("databricks")
    out = tmp_path / "deep" / "r.md"
    code = wv.main(["databricks", "--allow-live", "--expect-target", "dbc-host.example", "--out", str(out)], DB_ENV)
    assert code == 0 and "all checks ok" in out.read_text()
    assert "8 of 8 checks ok" in capsys.readouterr().out


def test_a_failing_run_exits_one_and_still_writes_a_report(spec_for, tmp_path):
    spec_for("snowflake", columns=("id",))
    out = tmp_path / "r.md"
    assert wv.main(["snowflake", "--allow-live", "--expect-target", "org-acct", "--out", str(out)], SF_ENV) == 1
    assert "columns_differ" in out.read_text() and "not run" in out.read_text()


def test_a_slow_statement_that_ignores_the_timeout_is_reported_cancelled_and_does_not_hang_the_run(spec_for, monkeypatch):
    monkeypatch.setattr(wv, "SLOW_WAIT_SECONDS", 0.5)

    class Stubborn(FakeSession):
        def _slow(self):
            if self.timeout == 2:  # never times out; only a cancel ends it
                if self.cancelled.wait(5):
                    raise WarehouseCancelled()
                raise AssertionError("never cancelled")
            return super()._slow()

    spec, world = spec_for("databricks")
    monkeypatch.setattr(wv, "DatabricksSession", lambda config: Stubborn(world))
    report = wv.run_validation(wv.build_spec("databricks", DB_ENV))
    failed = report.checks[-1]
    assert (failed.name, failed.code, failed.ok) == ("timeout", "no_timeout", False)
    assert all(s.closed for s in world.sessions)


def test_a_check_that_never_returns_is_stopped_by_the_wall_clock_bound(spec_for, monkeypatch):
    monkeypatch.setattr(wv, "STEP_SECONDS", 0.3)
    spec, world = spec_for("snowflake")

    class Hangs(FakeSession):
        def execute(self, sql, parameters):
            threading.Event().wait(5)  # a driver call that never comes back
            return super().execute(sql, parameters)

    monkeypatch.setattr(wv, "SnowflakeSession", lambda config: Hangs(world))
    report = wv.run_validation(wv.build_spec("snowflake", SF_ENV))
    assert (report.checks[-1].name, report.checks[-1].code) == ("read_only_grants", "check_hung") and len(report.not_run) == 7
