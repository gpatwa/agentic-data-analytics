"""Snowflake and Databricks driver sessions (stage S5), run against recorded-response fake drivers. No network, no account.

A fake driver module stands in for the real connector. The last section checks, only when a real driver is installed, that
the methods these sessions call still exist on it. Nothing here proves behaviour on a live warehouse (stage S6).
"""

import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from tests.test_warehouse_gateways import _plan

from app.execution.gateway import (
    CancellationToken,
    CostLimitExceeded,
    ExecutionError,
    ExecutionLimits,
    ExecutionTimeout,
)
from app.execution.warehouse_gateway import WarehouseCancelled, WarehouseTimeout
from app.execution.warehouse_sessions import (
    DatabricksConfig,
    DatabricksSession,
    SnowflakeConfig,
    SnowflakeSession,
    WarehouseConfigError,
    WarehouseDriverMissing,
    databricks_gateway,
    snowflake_gateway,
)

SECRET = "tok-very-secret-123"
PASSPHRASE = "pass-very-secret-456"


@pytest.fixture(scope="module")
def key_file(tmp_path_factory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(PASSPHRASE.encode()),
    )
    path = tmp_path_factory.mktemp("keys") / "rsa_key.p8"
    path.write_bytes(pem)
    return path


def snowflake_config(key_file, passphrase=PASSPHRASE):
    return SnowflakeConfig(
        account="acct", user="svc", private_key_path=str(key_file), warehouse="WH", database="DB", schema="PUBLIC",
        role="COMPASS_RO", private_key_passphrase=passphrase,
    )


def databricks_config():
    return DatabricksConfig(server_hostname="h.example", http_path="/sql/1.0/warehouses/x", catalog="main", schema="s", access_token=SECRET)


# --------------------------------------------------------------------------------------------------------- fake drivers


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.sfqid = None
        self.closed = False
        self._described = [SimpleNamespace(name=c) for c in conn.columns]
        self.fetched = False
        self._rows = list(conn.rows)

    @property
    def description(self):
        # A live Snowflake async cursor has no description until the first fetch; the fake can do the same.
        return None if self.conn.late_description and not self.fetched else self._described

    def execute(self, sql, params=None):
        self.conn.log.append(("execute", sql, params))
        for needle, outcome in self.conn.script.items():
            if needle in sql:
                if isinstance(outcome, Exception):
                    raise outcome
                self.conn.result = outcome
        return self

    def execute_async(self, sql, params=None):
        self.conn.log.append(("execute_async", sql, params))
        self.sfqid = "qid-1"
        self.conn.qid = "qid-1"

    def get_results_from_sfqid(self, qid):
        self.conn.log.append(("get_results", qid))

    def fetchone(self):
        return (self.conn.result,)

    def fetchall(self):
        return [(self.conn.result,)]

    def fetchmany(self, size):
        self.fetched = True
        batch, self._rows = self._rows[:size], self._rows[size:]
        return batch

    def abort_query(self, qid):
        self.conn.log.append(("abort", qid))
        self.conn.statuses = ["ABORTED"]
        return True

    def cancel(self):
        self.conn.log.append(("cursor_cancel",))
        self.conn.cancelled = True

    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, kwargs, *, columns=("month", "revenue"), rows=((("2024-01-01"), 5),), script=None, statuses=None, result="{}", late_description=False):
        self.kwargs, self.columns, self.rows, self.late_description = kwargs, columns, rows, late_description
        self.script, self.statuses, self.result = script or {}, list(statuses or ["SUCCESS"]), result
        self.log, self.cursors, self.qid, self.cancelled, self.closed = [], [], None, False, False

    def cursor(self):
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def get_query_status_throw_if_error(self, qid):
        status = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
        if isinstance(status, Exception):
            raise status
        return SimpleNamespace(name=status)

    def close(self):
        self.closed = True


class FakeDriver:
    def __init__(self, **conn_options):
        self.options, self.conns = conn_options, []

    def connect(self, **kwargs):
        conn = FakeConn(kwargs, **self.options)
        self.conns.append(conn)
        return conn


class TimeoutError630(Exception):
    errno = 630


def statements(conn):
    return [entry[1] for entry in conn.log if entry[0] == "execute"]


# ------------------------------------------------------------------------------------------------------------- config


def test_config_errors_name_variables_and_never_values():
    with pytest.raises(WarehouseConfigError) as info:
        SnowflakeConfig.from_env({"SNOWFLAKE_ACCOUNT": "a", "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE": PASSPHRASE})
    assert "SNOWFLAKE_USER" in str(info.value) and PASSPHRASE not in str(info.value)
    with pytest.raises(WarehouseConfigError) as info:
        DatabricksConfig.from_env({"DATABRICKS_TOKEN": SECRET})
    assert "DATABRICKS_HTTP_PATH" in str(info.value) and SECRET not in str(info.value)


def test_a_config_never_shows_a_secret(key_file):
    assert PASSPHRASE not in repr(snowflake_config(key_file)) and SECRET not in repr(databricks_config())
    assert SECRET not in str(databricks_config())


def test_from_env_reads_every_named_variable(key_file):
    env = {
        "SNOWFLAKE_ACCOUNT": "a", "SNOWFLAKE_USER": "u", "SNOWFLAKE_PRIVATE_KEY_PATH": str(key_file), "SNOWFLAKE_WAREHOUSE": "w",
        "SNOWFLAKE_DATABASE": "d", "SNOWFLAKE_SCHEMA": "s", "SNOWFLAKE_ROLE": "r", "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE": "p",
    }
    assert SnowflakeConfig.from_env(env).role == "r"
    env = {"DATABRICKS_SERVER_HOSTNAME": "h", "DATABRICKS_HTTP_PATH": "/p", "DATABRICKS_TOKEN": "t", "DATABRICKS_CATALOG": "c", "DATABRICKS_SCHEMA": "s"}
    assert DatabricksConfig.from_env(env).catalog == "c"


def test_a_missing_driver_is_a_clear_error_not_an_import_failure_of_the_module(key_file, monkeypatch):
    import importlib

    def refuse(name, *a, **k):
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", refuse)
    with pytest.raises(WarehouseDriverMissing) as info:
        SnowflakeSession(snowflake_config(key_file))
    assert "requirements-warehouses.txt" in str(info.value)
    with pytest.raises(WarehouseDriverMissing):
        DatabricksSession(databricks_config())


# ------------------------------------------------------------------------------------------------------------ Snowflake


def test_snowflake_connects_with_a_key_pair_a_named_role_and_utc_and_never_a_password(key_file):
    driver = FakeDriver()
    SnowflakeSession(snowflake_config(key_file), connector=driver)
    kwargs = driver.conns[0].kwargs
    assert kwargs["role"] == "COMPASS_RO" and kwargs["session_parameters"]["TIMEZONE"] == "UTC"
    assert isinstance(kwargs["private_key"], bytes) and kwargs["private_key"][0] == 0x30  # a DER sequence
    assert "password" not in kwargs and PASSPHRASE not in json.dumps({k: str(v) for k, v in kwargs.items() if k != "private_key"})


def test_an_unreadable_snowflake_key_is_reported_without_a_path_or_passphrase(key_file):
    with pytest.raises(WarehouseConfigError) as info:
        SnowflakeSession(snowflake_config(key_file, passphrase="wrong"), connector=FakeDriver())
    assert str(key_file) not in str(info.value) and "wrong" not in str(info.value)


def test_snowflake_prepare_sets_an_integer_timeout_and_drops_secondary_roles(key_file):
    driver = FakeDriver()
    session = SnowflakeSession(snowflake_config(key_file), connector=driver)
    session.prepare(timeout_seconds=2.2)
    assert statements(driver.conns[0]) == ["ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 3", "USE SECONDARY ROLES NONE"]
    with pytest.raises(ValueError):
        session.prepare(timeout_seconds=0)
    assert all(c.closed for c in driver.conns[0].cursors)


def test_snowflake_cost_is_mib_assigned_and_missing_means_none(key_file):
    plan = json.dumps({"GlobalStats": {"bytesAssigned": 5 * 1024 * 1024}})
    driver = FakeDriver(result=plan)
    session = SnowflakeSession(snowflake_config(key_file), connector=driver)
    assert session.explain_cost("SELECT 1", {"p1": 1}) == 5.0
    assert driver.conns[0].log[0][1] == "EXPLAIN USING JSON SELECT 1"
    for body in ("{}", "not json", json.dumps({"GlobalStats": {"bytesAssigned": True}}), json.dumps({"GlobalStats": {}})):
        assert SnowflakeSession(snowflake_config(key_file), connector=FakeDriver(result=body)).explain_cost("SELECT 1", {}) is None


def test_snowflake_runs_asynchronously_polls_then_streams(key_file):
    driver = FakeDriver(statuses=["QUEUED", "RUNNING", "SUCCESS"], rows=[("2024-01-01", 5), ("2024-02-01", 7)])
    session = SnowflakeSession(snowflake_config(key_file), connector=driver, poll_seconds=0)
    stream = session.execute("SELECT x", {"p1": 1})
    assert stream.columns == ("month", "revenue") and [tuple(r) for r in stream.fetchmany(10)] == [("2024-01-01", 5), ("2024-02-01", 7)]
    assert [e[0] for e in driver.conns[0].log] == ["execute_async", "get_results"]
    stream.close()


def test_snowflake_timeout_error_code_becomes_a_timeout(key_file):
    session = SnowflakeSession(snowflake_config(key_file), connector=FakeDriver(statuses=[TimeoutError630("secret sql here")]), poll_seconds=0)
    with pytest.raises(WarehouseTimeout):
        session.execute("SELECT x", {})
    other = SnowflakeSession(snowflake_config(key_file), connector=FakeDriver(statuses=[RuntimeError("boom")]), poll_seconds=0)
    with pytest.raises(RuntimeError):
        other.execute("SELECT x", {})


def test_snowflake_cancel_aborts_the_query_by_id_and_the_poll_sees_it(key_file):
    driver = FakeDriver(statuses=["RUNNING"])
    session = SnowflakeSession(snowflake_config(key_file), connector=driver, poll_seconds=0)
    original = driver.conns[0].get_query_status_throw_if_error

    def status_then_cancel(qid):
        session.cancel()  # arrives while the query is running
        return original(qid)

    driver.conns[0].get_query_status_throw_if_error = status_then_cancel
    with pytest.raises(WarehouseCancelled):
        session.execute("SELECT x", {})
    assert ("abort", "qid-1") in driver.conns[0].log


def test_snowflake_a_cancel_before_any_query_is_a_no_op_that_still_cancels_the_next_run(key_file):
    driver = FakeDriver(statuses=["RUNNING"])
    session = SnowflakeSession(snowflake_config(key_file), connector=driver, poll_seconds=0)
    session.cancel()
    assert not driver.conns[0].log
    with pytest.raises(WarehouseCancelled):
        session.execute("SELECT x", {})


def test_snowflake_gateway_end_to_end_with_a_cost_ceiling(key_file):
    plan_json = json.dumps({"GlobalStats": {"bytesAssigned": 2 * 1024 * 1024}})
    driver = FakeDriver(result=plan_json, rows=[("2024-01-01", 5)])
    gateway = snowflake_gateway(snowflake_config(key_file), allowed_tables={"sales_orders"}, connector=driver)
    result = gateway.execute(_plan("snowflake"), limits=ExecutionLimits(timeout_seconds=5))
    assert result.dialect == "snowflake" and result.estimated_cost_units == 2.0 and result.rows == (("2024-01-01", 5),)
    assert all(conn.closed for conn in driver.conns)  # every session is closed
    with pytest.raises(CostLimitExceeded):
        gateway.execute(_plan("snowflake"), limits=ExecutionLimits(timeout_seconds=5, max_cost_units=1.0))
    assert not any(e[0] == "execute_async" for e in driver.conns[-1].log)  # refused before the query was submitted


def test_snowflake_gateway_fails_closed_without_an_estimate_and_hides_driver_text(key_file):
    gateway = snowflake_gateway(snowflake_config(key_file), allowed_tables={"sales_orders"}, connector=FakeDriver(result="{}"))
    with pytest.raises(ExecutionError) as info:
        gateway.execute(_plan("snowflake"), limits=ExecutionLimits())
    assert "no cost estimate" in str(info.value)
    broken = FakeDriver(result=json.dumps({"GlobalStats": {"bytesAssigned": 1}}), statuses=[RuntimeError("SELECT secret FROM t")])
    gateway = snowflake_gateway(snowflake_config(key_file), allowed_tables={"sales_orders"}, connector=broken)
    with pytest.raises(ExecutionError) as info:
        gateway.execute(_plan("snowflake"), limits=ExecutionLimits())
    assert "secret" not in str(info.value) and "RuntimeError" in str(info.value)


def test_snowflake_timeout_maps_through_the_gateway(key_file):
    driver = FakeDriver(result=json.dumps({"GlobalStats": {"bytesAssigned": 1}}), statuses=[TimeoutError630("x")])
    gateway = snowflake_gateway(snowflake_config(key_file), allowed_tables={"sales_orders"}, connector=driver)
    with pytest.raises(ExecutionTimeout):
        gateway.execute(_plan("snowflake"), limits=ExecutionLimits())


# ----------------------------------------------------------------------------------------------------------- Databricks


def test_databricks_connects_with_a_token_catalog_and_schema_and_names_the_client():
    driver = FakeDriver()
    DatabricksSession(databricks_config(), connector=driver)
    kwargs = driver.conns[0].kwargs
    assert kwargs["catalog"] == "main" and kwargs["schema"] == "s" and kwargs["_user_agent_entry"] == "compass-governed-analytics"
    assert kwargs["access_token"] == SECRET  # handed to the driver and nowhere else


def test_databricks_prepare_sets_an_integer_timeout_and_utc():
    driver = FakeDriver()
    DatabricksSession(databricks_config(), connector=driver).prepare(timeout_seconds=0.4)
    assert statements(driver.conns[0]) == ["SET STATEMENT_TIMEOUT = 1", "SET TIME ZONE 'UTC'"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("== Optimized Logical Plan ==\nProject [a], Statistics(sizeInBytes=1024.0 B, rowCount=3)", 1024 / 1048576),
        ("Statistics(sizeInBytes=2.0 MiB)", 2.0),
        ("Statistics(sizeInBytes=1.5 GiB, rowCount=9)", 1536.0),
        ("Statistics(sizeInBytes=3 KiB)", 3 / 1024),
        ("no statistics here", None),
    ],
)
def test_databricks_cost_is_parsed_from_explain_cost_text(text, expected):
    session = DatabricksSession(databricks_config(), connector=FakeDriver(result=text))
    cost = session.explain_cost("SELECT 1", {"p1": 1})
    assert cost == pytest.approx(expected) if expected is not None else cost is None


def test_databricks_runs_with_native_named_markers_and_streams():
    driver = FakeDriver(rows=[("2024-01-01", 5)])
    session = DatabricksSession(databricks_config(), connector=driver)
    stream = session.execute("SELECT x WHERE a = :p1", {"p1": 4})
    assert driver.conns[0].log[0] == ("execute", "SELECT x WHERE a = :p1", {"p1": 4})
    assert stream.columns == ("month", "revenue") and [tuple(r) for r in stream.fetchmany(5)] == [("2024-01-01", 5)]


def test_databricks_timeout_and_cancel_are_classified_without_echoing_the_driver_message():
    session = DatabricksSession(databricks_config(), connector=FakeDriver(script={"SELECT": RuntimeError("STATEMENT_TIMEOUT SELECT secret")}))
    with pytest.raises(WarehouseTimeout):
        session.execute("SELECT x", {})
    cancelled = DatabricksSession(databricks_config(), connector=FakeDriver(script={"SELECT": RuntimeError("interrupted")}))
    cancelled._cancelled = True
    with pytest.raises(WarehouseCancelled):
        cancelled.execute("SELECT x", {})
    with pytest.raises(RuntimeError):
        DatabricksSession(databricks_config(), connector=FakeDriver(script={"SELECT": RuntimeError("other")})).execute("SELECT x", {})


def test_databricks_cancel_reaches_the_running_cursor():
    driver = FakeDriver()
    session = DatabricksSession(databricks_config(), connector=driver)
    session.cancel()  # nothing running: harmless
    session.execute("SELECT x", {})
    session.cancel()
    assert ("cursor_cancel",) in driver.conns[0].log


def test_databricks_gateway_end_to_end_and_cancellation_token():
    driver = FakeDriver(result="Statistics(sizeInBytes=4.0 MiB)", rows=[("2024-01-01", 5)])
    gateway = databricks_gateway(databricks_config(), allowed_tables={"sales_orders"}, connector=driver)
    result = gateway.execute(_plan("databricks"), limits=ExecutionLimits(timeout_seconds=5), cancellation=CancellationToken())
    assert result.dialect == "databricks" and result.estimated_cost_units == 4.0 and result.rows == (("2024-01-01", 5),)
    assert all(conn.closed for conn in driver.conns)
    with pytest.raises(CostLimitExceeded):
        gateway.execute(_plan("databricks"), limits=ExecutionLimits(timeout_seconds=5, max_cost_units=1.0))


def test_databricks_gateway_refuses_a_plan_for_another_dialect_before_any_connection():
    driver = FakeDriver()
    gateway = databricks_gateway(databricks_config(), allowed_tables={"sales_orders"}, connector=driver)
    with pytest.raises(Exception):
        gateway.execute(_plan("snowflake"), limits=ExecutionLimits())
    assert driver.conns == []


# ------------------------------------------------------------------- the real drivers still offer what the sessions call


def test_the_installed_snowflake_connector_still_has_the_calls_the_session_uses():
    connector = pytest.importorskip("snowflake.connector")
    from snowflake.connector.connection import SnowflakeConnection
    from snowflake.connector.cursor import SnowflakeCursor

    for name in ("execute", "execute_async", "fetchmany", "fetchone", "get_results_from_sfqid", "abort_query", "close"):
        assert callable(getattr(SnowflakeCursor, name)), name
    assert hasattr(SnowflakeCursor, "sfqid") and hasattr(SnowflakeCursor, "description")
    assert callable(SnowflakeConnection.get_query_status_throw_if_error)
    assert callable(connector.connect)


def test_the_installed_databricks_connector_still_has_the_calls_the_session_uses():
    sql = pytest.importorskip("databricks.sql")
    from databricks.sql.client import Cursor

    for name in ("execute", "fetchmany", "fetchall", "cancel", "close"):
        assert callable(getattr(Cursor, name)), name
    assert hasattr(Cursor, "description") and sql.paramstyle == "named"


def test_snowflake_async_results_get_their_column_names_after_the_first_fetch(key_file):
    """Seen live: an async cursor's description is None until the first fetch. The stream reads the first batch to get it."""
    driver = FakeDriver(statuses=["SUCCESS"], rows=[("2024-01-01", 5), ("2024-02-01", 7), ("2024-03-01", 9)], late_description=True)
    session = SnowflakeSession(snowflake_config(key_file), connector=driver, poll_seconds=0)
    stream = session.execute("SELECT x", {})
    assert stream.columns == ("month", "revenue")
    assert [tuple(r) for r in stream.fetchmany(2)] == [("2024-01-01", 5), ("2024-02-01", 7)]  # the prefetched rows are not lost
    assert [tuple(r) for r in stream.fetchmany(2)] == [("2024-03-01", 9)] and stream.fetchmany(2) == []


def test_a_result_with_no_rows_still_streams_cleanly_when_the_description_is_late(key_file):
    driver = FakeDriver(statuses=["SUCCESS"], rows=[], late_description=True)
    stream = SnowflakeSession(snowflake_config(key_file), connector=driver, poll_seconds=0).execute("SELECT x", {})
    assert stream.columns == ("month", "revenue") and stream.fetchmany(5) == []
