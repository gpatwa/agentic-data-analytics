"""Build the query gateway for one tenant's connection (threat T7): the allowlist is exactly what the admin named."""

from __future__ import annotations

import base64
from typing import Any

from app.connect.store import ConnectionNotFound, ConnectionRecord, ConnectionStore
from app.execution.warehouse_gateway import DatabricksGateway, SnowflakeGateway
from app.execution.warehouse_sessions import (
    DatabricksConfig,
    SnowflakeConfig,
    SnowflakeSession,
    databricks_gateway,
    snowflake_gateway,
)


class ConnectionUnavailable(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def snowflake_gateway_for(
    store: ConnectionStore, secrets: Any, tenant_id: str, connection_id: str, *, connector: Any | None = None, **options: Any
) -> SnowflakeGateway:
    """The record is read under the caller's tenant, so another tenant's id behaves like a missing one."""
    try:
        record = store.get(tenant_id, connection_id)
    except ConnectionNotFound as exc:
        raise ConnectionUnavailable("connection_not_found") from exc
    if record.engine != "snowflake":
        raise ConnectionUnavailable("engine_not_supported")
    if record.status != "active":
        raise ConnectionUnavailable("connection_not_active")
    if record.auth_method != "key_pair":
        raise ConnectionUnavailable("federation_not_available")
    try:
        der = base64.b64decode(secrets.read(tenant_id, record.secret_name or ""), validate=True)
    except Exception as exc:  # noqa: BLE001 - never echo why: a message could carry key material.
        raise ConnectionUnavailable("credential_unavailable") from exc
    config = SnowflakeConfig(
        account=record.account,
        user=record.user,
        warehouse=record.warehouse,
        database=record.database,
        schema=record.default_schema,
        role=record.role,
        private_key_der=der,
    )
    return snowflake_gateway(config, allowed_tables=record.allowed_tables, connector=connector, **options)


def snowflake_session_for(record: ConnectionRecord, secrets: Any, *, connector: Any | None = None) -> SnowflakeSession:
    """Open one session as the connection's own read-only service user (key pair from the tenant's secret store).

    Used by the connection test right after setup, so it does not require the connection to be active yet. A missing or corrupt
    credential is one stable code and never says why.
    """
    if record.engine != "snowflake" or record.auth_method != "key_pair":
        raise ConnectionUnavailable("engine_not_supported")
    try:
        der = base64.b64decode(secrets.read(record.tenant_id, record.secret_name or ""), validate=True)
    except Exception as exc:  # noqa: BLE001 - never echo why: a message could carry key material.
        raise ConnectionUnavailable("credential_unavailable") from exc
    config = SnowflakeConfig(
        account=record.account, user=record.user, warehouse=record.warehouse, database=record.database,
        schema=record.default_schema, role=record.role, private_key_der=der,
    )
    return SnowflakeSession(config, connector=connector)


def _databricks_config(record: ConnectionRecord, secrets: Any) -> DatabricksConfig:
    """OAuth M2M as the connection's own read-only principal; the secret is read from custody only here."""
    try:
        client_secret = secrets.read(record.tenant_id, record.secret_name or "")
    except Exception as exc:  # noqa: BLE001 - never echo why: a message could carry the secret.
        raise ConnectionUnavailable("credential_unavailable") from exc
    return DatabricksConfig(
        server_hostname=record.host, http_path=record.http_path, catalog=record.database, schema=record.default_schema,
        client_id=record.user, client_secret=client_secret,
    )


def databricks_gateway_for(
    store: ConnectionStore, secrets: Any, tenant_id: str, connection_id: str, *, connector: Any | None = None, **options: Any
) -> DatabricksGateway:
    """The record is read under the caller's tenant, so another tenant's id behaves like a missing one. The allowlist is exactly
    the tables the admin named (lower-case catalog.schema.table); the gateway refuses anything else before it reaches Databricks."""
    try:
        record = store.get(tenant_id, connection_id)
    except ConnectionNotFound as exc:
        raise ConnectionUnavailable("connection_not_found") from exc
    if record.engine != "databricks" or record.auth_method != "oauth_m2m" or not record.http_path:
        raise ConnectionUnavailable("engine_not_supported")
    if record.status != "active":
        raise ConnectionUnavailable("connection_not_active")
    return databricks_gateway(_databricks_config(record, secrets), allowed_tables=record.allowed_tables, connector=connector, **options)


def gateway_for(store: ConnectionStore, secrets: Any, tenant_id: str, connection_id: str, **options: Any) -> SnowflakeGateway | DatabricksGateway:
    """The right gateway for the connection's engine. An engine with no gateway is one stable code."""
    try:
        engine = store.get(tenant_id, connection_id).engine
    except ConnectionNotFound as exc:
        raise ConnectionUnavailable("connection_not_found") from exc
    if engine == "snowflake":
        return snowflake_gateway_for(store, secrets, tenant_id, connection_id, **options)
    if engine == "databricks":
        return databricks_gateway_for(store, secrets, tenant_id, connection_id, **options)
    raise ConnectionUnavailable("engine_not_supported")
