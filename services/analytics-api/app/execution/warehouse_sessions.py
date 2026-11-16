"""Real driver sessions for Snowflake and Databricks (plan stage S5). Local only: nothing here has met a live warehouse.

`WarehouseGateway` owns the policy (validation, caps, cost ceiling, cancel, error mapping). These classes own what is
driver-specific: connecting with a read-only role, the session settings, a cost estimate, cancel and row streaming.

- The drivers are optional extras (`requirements-warehouses.txt`), imported only when a session is opened. The default
  image holds neither. A test or caller can inject a driver module, which is how the tests run without one.
- Credentials come from the environment only. A config never prints, logs or reprs a secret, and an error names
  variables, never values. Authentication is Snowflake key-pair and Databricks personal access token; adding another
  method (OAuth, SSO) is a separate security review (docs/WAREHOUSE_AND_DBT_PLAN.md, decision 3).
- Read-only is defence in depth: the gateway validates the SQL, and the role the owner provisions must hold SELECT only.
  Neither engine has a per-session read-only switch comparable to PostgreSQL's, so a role with more than SELECT is the
  owner's configuration error, not something this code can fix.
- Cost units are MiB the engine says it will scan (Snowflake `EXPLAIN` `bytesAssigned`, Databricks `EXPLAIN COST`
  `sizeInBytes`). That is an engine estimate, not a bill. A missing estimate returns None and the gateway fails closed.

Everything the live warehouses do (marker syntax, timeout error codes, cancel, the shape of `EXPLAIN` output) is taken
from the installed driver's API and vendor documentation and is unverified until the sandbox run (stage S6).
"""

from __future__ import annotations

import importlib
import json
import math
import os
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.execution.warehouse_gateway import (
    DatabricksGateway,
    RowStream,
    SnowflakeGateway,
    WarehouseCancelled,
    WarehouseTimeout,
)

_MIB = 1024 * 1024
_QUERY_TAG = "compass-governed-analytics"
_SNOWFLAKE_TIMEOUT_ERRNO = 630  # "Statement reached its statement or warehouse timeout" (unverified until S6)
_RUNNING = {"RUNNING", "QUEUED", "RESUMING_WAREHOUSE", "QUEUED_REPARING_WAREHOUSE", "BLOCKED", "NO_DATA"}
_ABORTED = {"ABORTING", "ABORTED"}
_UNITS = {"B": 1, "KiB": 1024, "MiB": _MIB, "GiB": 1024**3, "TiB": 1024**4, "PiB": 1024**5, "EiB": 1024**6}
_SIZE = re.compile(r"sizeInBytes=([0-9]+(?:\.[0-9]+)?)\s*(B|KiB|MiB|GiB|TiB|PiB|EiB)")


class WarehouseConfigError(Exception):
    """A configuration problem. The message names variables only, never a value."""


class WarehouseDriverMissing(Exception):
    """The optional driver is not installed."""


# --------------------------------------------------------------------------------------------------------------- config


def _required(env: Mapping[str, str], names: Iterable[str]) -> dict[str, str]:
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise WarehouseConfigError("missing environment variables: " + ", ".join(missing))
    return {name: env[name] for name in names}


@dataclass(frozen=True)
class SnowflakeConfig:
    account: str
    user: str
    warehouse: str
    database: str
    schema: str
    role: str
    private_key_path: str | None = None
    private_key_passphrase: str | None = field(default=None, repr=False)
    private_key_der: bytes | None = field(default=None, repr=False)  # in-memory key from the sandbox provisioner

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SnowflakeConfig:
        env = os.environ if env is None else env
        names = (
            "SNOWFLAKE_ACCOUNT",
            "SNOWFLAKE_USER",
            "SNOWFLAKE_PRIVATE_KEY_PATH",
            "SNOWFLAKE_WAREHOUSE",
            "SNOWFLAKE_DATABASE",
            "SNOWFLAKE_SCHEMA",
            "SNOWFLAKE_ROLE",
        )
        v = _required(env, names)
        return cls(
            account=v["SNOWFLAKE_ACCOUNT"],
            user=v["SNOWFLAKE_USER"],
            private_key_path=v["SNOWFLAKE_PRIVATE_KEY_PATH"],
            warehouse=v["SNOWFLAKE_WAREHOUSE"],
            database=v["SNOWFLAKE_DATABASE"],
            schema=v["SNOWFLAKE_SCHEMA"],
            role=v["SNOWFLAKE_ROLE"],
            private_key_passphrase=env.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") or None,
        )


@dataclass(frozen=True)
class DatabricksConfig:
    server_hostname: str
    http_path: str
    catalog: str
    schema: str
    access_token: str = field(repr=False, default="")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DatabricksConfig:
        env = os.environ if env is None else env
        names = (
            "DATABRICKS_SERVER_HOSTNAME",
            "DATABRICKS_HTTP_PATH",
            "DATABRICKS_TOKEN",
            "DATABRICKS_CATALOG",
            "DATABRICKS_SCHEMA",
        )
        v = _required(env, names)
        return cls(
            server_hostname=v["DATABRICKS_SERVER_HOSTNAME"],
            http_path=v["DATABRICKS_HTTP_PATH"],
            catalog=v["DATABRICKS_CATALOG"],
            schema=v["DATABRICKS_SCHEMA"],
            access_token=v["DATABRICKS_TOKEN"],
        )


# --------------------------------------------------------------------------------------------------------------- helpers


def _load_driver(module: str, extra: str, injected: Any | None) -> Any:
    if injected is not None:
        return injected
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise WarehouseDriverMissing(f"{module} is not installed; install requirements-warehouses.txt ({extra})") from exc


def _seconds(timeout_seconds: float) -> int:
    if not timeout_seconds > 0:
        raise ValueError("timeout_seconds must be positive")
    return max(1, math.ceil(timeout_seconds))


def _mib(size: float, unit: str) -> float:
    return float(size) * _UNITS[unit] / _MIB


def _run(connection: Any, statement: str, parameters: Mapping[str, Any] | None = None) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute(statement, dict(parameters)) if parameters else cursor.execute(statement)
    finally:
        cursor.close()


def _column_names(cursor: Any) -> tuple[str, ...]:
    return tuple(getattr(d, "name", None) or d[0] for d in cursor.description)


class _CursorStream:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor
        self._buffer: list[Any] = []
        if cursor.description is None:
            # Snowflake's async results carry no column names until the first fetch (seen on a live account, 2026-11-15).
            self._buffer = list(cursor.fetchmany(1024))
        self.columns = _column_names(cursor) if cursor.description is not None else ()

    def fetchmany(self, size: int) -> Sequence[tuple[Any, ...]]:
        if self._buffer:
            batch, self._buffer = self._buffer[:size], self._buffer[size:]
            return batch
        return self._cursor.fetchmany(size)

    def close(self) -> None:
        self._cursor.close()


def _status_name(status: Any) -> str:
    return str(getattr(status, "name", status)).upper()


def _der_private_key(path: str, passphrase: str | None) -> bytes:
    from cryptography.hazmat.primitives import serialization

    try:
        with open(path, "rb") as handle:
            key = serialization.load_pem_private_key(handle.read(), password=passphrase.encode() if passphrase else None)
    except Exception:  # noqa: BLE001 - never echo the path, the file or the passphrase
        raise WarehouseConfigError("the Snowflake private key could not be read") from None
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


# --------------------------------------------------------------------------------------------------------------- Snowflake


class SnowflakeSession:
    """One Snowflake connection. Cancellable: the query runs asynchronously and is aborted by its query id."""

    def __init__(self, config: SnowflakeConfig, *, connector: Any | None = None, poll_seconds: float = 0.05) -> None:
        driver = _load_driver("snowflake.connector", "snowflake extra", connector)
        self._poll = poll_seconds
        self._qid: str | None = None
        self._cancelled = False
        self._conn = driver.connect(
            account=config.account,
            user=config.user,
            private_key=config.private_key_der or _der_private_key(config.private_key_path or "", config.private_key_passphrase),
            warehouse=config.warehouse,
            database=config.database,
            schema=config.schema,
            role=config.role,
            login_timeout=30,
            session_parameters={"TIMEZONE": "UTC", "QUERY_TAG": _QUERY_TAG},
        )

    def prepare(self, *, timeout_seconds: float) -> None:
        # Integer and fixed text only: no caller value is ever interpolated into these statements.
        _run(self._conn, f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {_seconds(timeout_seconds)}")
        _run(self._conn, "USE SECONDARY ROLES NONE")  # privileges come from the one named role and nothing else

    def explain_cost(self, sql: str, parameters: Mapping[str, Any]) -> float | None:
        cursor = self._conn.cursor()
        try:
            cursor.execute("EXPLAIN USING JSON " + sql, dict(parameters))
            row = cursor.fetchone()
        finally:
            cursor.close()
        try:
            assigned = json.loads(row[0])["GlobalStats"]["bytesAssigned"]
        except (TypeError, KeyError, IndexError, ValueError):
            return None
        return float(assigned) / _MIB if isinstance(assigned, (int, float)) and not isinstance(assigned, bool) else None

    def execute(self, sql: str, parameters: Mapping[str, Any]) -> RowStream:
        cursor = self._conn.cursor()
        try:
            cursor.execute_async(sql, dict(parameters))
            self._qid = cursor.sfqid
            delay = self._poll
            while True:
                if self._cancelled:
                    self._abort()
                    raise WarehouseCancelled()
                name = _status_name(self._status())
                if name in _RUNNING:
                    time.sleep(delay)
                    delay = min(delay * 2, 1.0)
                elif name in _ABORTED:
                    raise WarehouseCancelled()
                elif name == "SUCCESS":
                    break
                else:
                    raise RuntimeError("query ended in a failed state")
            cursor.get_results_from_sfqid(self._qid)
            return _CursorStream(cursor)
        except BaseException:
            cursor.close()
            raise

    def _status(self) -> Any:
        try:
            return self._conn.get_query_status_throw_if_error(self._qid)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "errno", None) == _SNOWFLAKE_TIMEOUT_ERRNO:
                raise WarehouseTimeout() from None
            raise

    def _abort(self) -> None:
        if self._qid is None:
            return
        cursor = self._conn.cursor()
        try:
            cursor.abort_query(self._qid)
        finally:
            cursor.close()

    def cancel(self) -> None:
        self._cancelled = True  # seen by the poll loop, so a cancel that lands before the query id exists still works
        self._abort()

    def close(self) -> None:
        self._conn.close()


# ------------------------------------------------------------------------------------------------------------- Databricks


class DatabricksSession:
    """One Databricks SQL connection. Native named markers; cancelled through the cursor."""

    def __init__(self, config: DatabricksConfig, *, connector: Any | None = None) -> None:
        driver = _load_driver("databricks.sql", "databricks extra", connector)
        self._cursor: Any | None = None
        self._cancelled = False
        self._conn = driver.connect(
            server_hostname=config.server_hostname,
            http_path=config.http_path,
            access_token=config.access_token,
            catalog=config.catalog,
            schema=config.schema,
            _user_agent_entry=_QUERY_TAG,
        )

    def prepare(self, *, timeout_seconds: float) -> None:
        _run(self._conn, f"SET STATEMENT_TIMEOUT = {_seconds(timeout_seconds)}")
        _run(self._conn, "SET TIME ZONE 'UTC'")

    def explain_cost(self, sql: str, parameters: Mapping[str, Any]) -> float | None:
        cursor = self._conn.cursor()
        try:
            cursor.execute("EXPLAIN COST " + sql, dict(parameters))
            rows = cursor.fetchall()
        finally:
            cursor.close()
        match = _SIZE.search(" ".join(str(cell) for row in rows for cell in row))
        return _mib(float(match.group(1)), match.group(2)) if match else None

    def execute(self, sql: str, parameters: Mapping[str, Any]) -> RowStream:
        cursor = self._conn.cursor()
        self._cursor = cursor
        try:
            cursor.execute(sql, dict(parameters))
        except Exception as exc:  # noqa: BLE001 - classify only; the driver message may carry SQL or values
            cursor.close()
            if self._cancelled:
                raise WarehouseCancelled() from None
            text = str(exc).upper()
            if "STATEMENT_TIMEOUT" in text or "TIMED OUT" in text:
                raise WarehouseTimeout() from None
            raise
        return _CursorStream(cursor)

    def cancel(self) -> None:
        self._cancelled = True
        if self._cursor is not None:
            self._cursor.cancel()

    def close(self) -> None:
        self._conn.close()


# ------------------------------------------------------------------------------------------------------------- factories


def snowflake_gateway(
    config: SnowflakeConfig, *, allowed_tables: Iterable[str], connector: Any | None = None, **options: Any
) -> SnowflakeGateway:
    return SnowflakeGateway(lambda: SnowflakeSession(config, connector=connector), allowed_tables=allowed_tables, **options)


def databricks_gateway(
    config: DatabricksConfig, *, allowed_tables: Iterable[str], connector: Any | None = None, **options: Any
) -> DatabricksGateway:
    return DatabricksGateway(lambda: DatabricksSession(config, connector=connector), allowed_tables=allowed_tables, **options)


__all__ = [
    "DatabricksConfig",
    "DatabricksSession",
    "SnowflakeConfig",
    "SnowflakeSession",
    "WarehouseConfigError",
    "WarehouseDriverMissing",
    "databricks_gateway",
    "snowflake_gateway",
]
