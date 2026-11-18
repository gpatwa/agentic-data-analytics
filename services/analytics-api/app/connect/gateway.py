"""Build the query gateway for one tenant's connection (threat T7): the allowlist is exactly what the admin named."""

from __future__ import annotations

import base64
from typing import Any

from app.connect.store import ConnectionNotFound, ConnectionRecord, ConnectionStore
from app.execution.warehouse_gateway import SnowflakeGateway
from app.execution.warehouse_sessions import SnowflakeConfig, SnowflakeSession, snowflake_gateway


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
