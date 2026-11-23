"""Per-tenant connection records and per-tenant secret custody (threats T4, T8, T16).

Every connection belongs to exactly one tenant. A lookup under any other tenant fails the same way as a missing id, so the
store never confirms that another tenant's connection exists. A warehouse host can be claimed by one tenant only. Secrets
live in a tenant-scoped store and are referenced by name; a connection record never contains one.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

STATUSES = ("pending", "active", "suspended", "removed")
AUTH_METHODS = ("key_pair", "federated", "oauth_m2m")


class ConnectionNotFound(KeyError):
    """Absent, or belongs to another tenant: deliberately indistinguishable."""


class ConnectionConflict(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ConnectionRecord:
    tenant_id: str
    connection_id: str
    engine: str
    host: str
    account: str
    user: str
    role: str
    warehouse: str
    database: str
    default_schema: str
    allowed_tables: tuple[str, ...]  # exact lower-case qualified names the gateway will allow, nothing else
    auth_method: str = "key_pair"
    secret_name: str | None = None  # a reference into the tenant's secret store, never a value
    teardown: tuple[str, ...] = ()  # the statements that remove what setup created; object names only, no secrets
    http_path: str = ""  # Databricks only: the SQL warehouse's HTTP path (not a secret)
    secret_expires_at: datetime | None = None  # when the stored secret stops working, if known (Databricks OAuth secrets expire)
    warm_up: bool = False  # an admin turned on warming this warehouse before a user's first question (app.connect.warmup); off unless set
    status: str = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.connection_id:
            raise ValueError("tenant_id and connection_id are required")
        if self.status not in STATUSES:
            raise ValueError("unknown status")
        if self.auth_method not in AUTH_METHODS:
            raise ValueError("unknown auth method")
        if not self.allowed_tables:
            raise ValueError("at least one allowed table is required")
        for table in self.allowed_tables:
            if table != table.lower() or "*" in table or "%" in table or table.count(".") != 2 or not all(table.split(".")):
                raise ValueError("allowed tables must be exact lower-case database.schema.table names")
        if self.auth_method == "key_pair" and not self.secret_name:
            raise ValueError("a key-pair connection needs a secret reference")
        if self.auth_method == "oauth_m2m" and (self.engine != "databricks" or not self.secret_name or not self.user):
            raise ValueError("an OAuth M2M connection is Databricks only and needs a client id (user) and a secret reference")
        if self.auth_method == "federated" and self.secret_name:
            raise ValueError("a federated connection stores no secret")


class ConnectionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, ConnectionRecord] = {}

    def add(self, record: ConnectionRecord) -> None:
        with self._lock:
            if record.connection_id in self._records:
                raise ConnectionConflict("connection_id_taken")
            for other in self._records.values():
                if other.status != "removed" and (other.engine, other.host) == (record.engine, record.host) and other.tenant_id != record.tenant_id:
                    raise ConnectionConflict("target_claimed")
            self._records[record.connection_id] = record

    def get(self, tenant_id: str, connection_id: str) -> ConnectionRecord:
        record = self._records.get(connection_id)
        if record is None or record.tenant_id != tenant_id:
            raise ConnectionNotFound("connection not found")
        return record

    def list(self, tenant_id: str) -> list[ConnectionRecord]:
        return [r for r in self._records.values() if r.tenant_id == tenant_id]

    def all(self) -> list[ConnectionRecord]:
        """Every tenant's records, for the system's own sweeps (the drift monitor); never for a request."""
        with self._lock:
            return list(self._records.values())

    def update(self, record: ConnectionRecord) -> ConnectionRecord:
        """Replace a record the caller's tenant owns. The tenant and id cannot change."""
        with self._lock:
            self.get(record.tenant_id, record.connection_id)
            self._records[record.connection_id] = record
            return record

    def set_status(self, tenant_id: str, connection_id: str, status: str) -> ConnectionRecord:
        if status not in STATUSES:
            raise ValueError("unknown status")
        with self._lock:
            updated = replace(self.get(tenant_id, connection_id), status=status)
            self._records[connection_id] = updated
            return updated


@dataclass(frozen=True)
class DeletionReceipt:
    tenant_id: str
    secret_name: str
    deleted_at: datetime


class TenantSecretStore:
    """Tenant-scoped secret custody for tests and local runs. Production replaces it with a vault adapter.

    Satisfies the `SecretProvider` read signature. Nothing prints a value: `repr` shows counts only.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[tuple[str, str], tuple[str, int]] = {}

    def __repr__(self) -> str:
        return f"TenantSecretStore(secrets={len(self._values)})"

    def put(self, tenant_id: str, name: str, value: str) -> int:
        if not tenant_id or not name or not value:
            raise ValueError("tenant_id, name and value are required")
        with self._lock:
            version = self._values.get((tenant_id, name), ("", 0))[1] + 1
            self._values[(tenant_id, name)] = (value, version)
            return version

    def read(self, tenant_id: str, secret_name: str) -> str:
        try:
            return self._values[(tenant_id, secret_name)][0]
        except KeyError as exc:
            raise KeyError("secret was not found") from exc

    def exists(self, tenant_id: str, name: str) -> bool:
        return (tenant_id, name) in self._values

    def version(self, tenant_id: str, name: str) -> int:
        """Changes whenever the value is replaced; anything built from the value (a cached gateway) is stale when it changes."""
        try:
            return self._values[(tenant_id, name)][1]
        except KeyError as exc:
            raise KeyError("secret was not found") from exc

    def delete(self, tenant_id: str, name: str) -> DeletionReceipt:
        with self._lock:
            if self._values.pop((tenant_id, name), None) is None:
                raise KeyError("secret was not found")
        return DeletionReceipt(tenant_id, name, datetime.now(timezone.utc))
