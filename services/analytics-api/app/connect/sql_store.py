"""Durable Connect state in the control database: connection records and the setup audit chain.

`SqlConnectionStore` has the interface of `ConnectionStore` (add, get, list, update, set_status) and the same rules: a lookup
under another tenant is indistinguishable from a missing id, and one (engine, host) can be claimed by one tenant at a time. The
claim is a primary-key row taken atomically, so two tenants cannot both win it, even from two processes. `SqlAuditChain` keeps
the hash chain of `app.security.audit.AuditChain` in a table; the chain is extended under a unique key, so two writers cannot fork it.

No secret is stored here: a record holds only a reference name into custody.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, MetaData, String, Table, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.connect.store import STATUSES, ConnectionConflict, ConnectionNotFound, ConnectionRecord
from app.security.audit import AuditChain
from packages.platform_contracts.security import AuditEvent

_META = MetaData()
_S = String
CONNECTIONS = Table(
    "connect_connections", _META,
    Column("tenant_id", _S(255)), Column("connection_id", _S(255), primary_key=True), Column("engine", _S(32)), Column("host", _S(255)),
    Column("account", _S(255)), Column("user_name", _S(255)), Column("role", _S(255)), Column("warehouse", _S(255)),
    Column("database_name", _S(255)), Column("default_schema", _S(255)), Column("allowed_tables", JSON), Column("auth_method", _S(32)),
    Column("secret_name", _S(255), nullable=True), Column("teardown", JSON), Column("http_path", _S(512)),
    Column("secret_expires_at", DateTime(timezone=True), nullable=True), Column("status", _S(16)), Column("created_at", DateTime(timezone=True)),
    Column("warm_up", Boolean, nullable=False, server_default="0"),
)
CLAIMS = Table("connect_target_claims", _META, Column("engine", _S(32), primary_key=True), Column("host", _S(255), primary_key=True),
               Column("tenant_id", _S(255)))
AUDIT = Table(
    "connect_audit_events", _META,
    Column("seq", Integer, primary_key=True, autoincrement=False), Column("event_id", _S(255)), Column("event_type", _S(100)),
    Column("tenant_id", _S(255)), Column("actor_id", _S(255)), Column("payload", JSON), Column("previous_hash", _S(64), nullable=True),
    Column("event_hash", _S(64), unique=True), Column("created_at", DateTime(timezone=True)),
)  # mirrors migrations 0013 and 0015; a test keeps the two equal, and building them touches no database


def _aware(value: datetime | None) -> datetime | None:
    return None if value is None else (value if value.tzinfo else value.replace(tzinfo=timezone.utc))


def _row(r: ConnectionRecord) -> dict:
    return {"tenant_id": r.tenant_id, "connection_id": r.connection_id, "engine": r.engine, "host": r.host, "account": r.account,
            "user_name": r.user, "role": r.role, "warehouse": r.warehouse, "database_name": r.database, "default_schema": r.default_schema,
            "allowed_tables": list(r.allowed_tables), "auth_method": r.auth_method, "secret_name": r.secret_name, "teardown": list(r.teardown),
            "http_path": r.http_path, "secret_expires_at": r.secret_expires_at, "status": r.status, "created_at": r.created_at, "warm_up": r.warm_up}


def _record(m) -> ConnectionRecord:
    return ConnectionRecord(tenant_id=m["tenant_id"], connection_id=m["connection_id"], engine=m["engine"], host=m["host"], account=m["account"],
                            user=m["user_name"], role=m["role"], warehouse=m["warehouse"], database=m["database_name"], default_schema=m["default_schema"],
                            allowed_tables=tuple(m["allowed_tables"]), auth_method=m["auth_method"], secret_name=m["secret_name"],
                            teardown=tuple(m["teardown"]), http_path=m["http_path"] or "", secret_expires_at=_aware(m["secret_expires_at"]),
                            status=m["status"], created_at=_aware(m["created_at"]), warm_up=bool(m["warm_up"]))


class SqlConnectionStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._t, self._claims = CONNECTIONS, CLAIMS
        self._after_owner_read = None  # test seam: runs between reading a claim's owner and taking it over

    def _get(self, conn, tenant_id: str, connection_id: str) -> ConnectionRecord:
        row = conn.execute(select(self._t).where(self._t.c.connection_id == connection_id)).mappings().first()
        if row is None or row["tenant_id"] != tenant_id:
            raise ConnectionNotFound("connection not found")
        return _record(row)

    def _claim(self, conn, record: ConnectionRecord) -> None:
        """Take the (engine, host) claim for this tenant, or raise `target_claimed`. A claim whose owner has no live record is free."""
        c = self._claims.c
        try:
            with conn.begin_nested():
                conn.execute(insert(self._claims).values(engine=record.engine, host=record.host, tenant_id=record.tenant_id))
            return
        except IntegrityError:
            pass
        owner = conn.execute(select(c.tenant_id).where((c.engine == record.engine) & (c.host == record.host))).scalar()
        if owner == record.tenant_id:
            return
        if self._after_owner_read:
            self._after_owner_read(conn)
        live = conn.execute(select(func.count()).select_from(self._t).where(
            (self._t.c.engine == record.engine) & (self._t.c.host == record.host) & (self._t.c.tenant_id == owner) & (self._t.c.status != "removed"))).scalar()
        if live:
            raise ConnectionConflict("target_claimed")
        taken = conn.execute(update(self._claims).where((c.engine == record.engine) & (c.host == record.host) & (c.tenant_id == owner))
                             .values(tenant_id=record.tenant_id)).rowcount  # compare-and-set: only one taker can win a freed claim
        if taken != 1:
            raise ConnectionConflict("target_claimed")

    def add(self, record: ConnectionRecord) -> None:
        with self._engine.begin() as conn:
            if conn.execute(select(self._t.c.connection_id).where(self._t.c.connection_id == record.connection_id)).first():
                raise ConnectionConflict("connection_id_taken")
            self._claim(conn, record)
            try:
                with conn.begin_nested():
                    conn.execute(insert(self._t).values(**_row(record)))
            except IntegrityError:
                raise ConnectionConflict("connection_id_taken") from None

    def get(self, tenant_id: str, connection_id: str) -> ConnectionRecord:
        with self._engine.connect() as conn:
            return self._get(conn, tenant_id, connection_id)

    def list(self, tenant_id: str) -> list[ConnectionRecord]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(self._t).where(self._t.c.tenant_id == tenant_id).order_by(self._t.c.created_at, self._t.c.connection_id)).mappings().all()
        return [_record(r) for r in rows]

    def all(self) -> list[ConnectionRecord]:
        """Every tenant's records, for the system's own sweeps (the drift monitor); never for a request."""
        with self._engine.connect() as conn:
            rows = conn.execute(select(self._t).order_by(self._t.c.created_at, self._t.c.connection_id)).mappings().all()
        return [_record(r) for r in rows]

    def update(self, record: ConnectionRecord) -> ConnectionRecord:
        """Replace a record the caller's tenant owns. The tenant and id cannot change."""
        with self._engine.begin() as conn:
            self._get(conn, record.tenant_id, record.connection_id)
            values = _row(record)
            values.pop("connection_id"), values.pop("tenant_id")
            conn.execute(update(self._t).where(self._t.c.connection_id == record.connection_id).values(**values))
        return record

    def set_status(self, tenant_id: str, connection_id: str, status: str) -> ConnectionRecord:
        if status not in STATUSES:
            raise ValueError("unknown status")
        with self._engine.begin() as conn:
            updated = replace(self._get(conn, tenant_id, connection_id), status=status)
            conn.execute(update(self._t).where(self._t.c.connection_id == connection_id).values(status=status))
        return updated


class SqlAuditChain(AuditChain):
    """The same chain, durable. Another writer extending the chain at the same moment makes the insert fail on the unique key and
    this writer re-reads the head and tries again, so the chain never forks."""

    def __init__(self, engine: Engine, *, attempts: int = 8) -> None:
        super().__init__()
        self._engine, self._attempts, self._lock = engine, attempts, threading.Lock()
        self._t = AUDIT

    @property
    def events(self) -> list[AuditEvent]:  # type: ignore[override]
        with self._engine.connect() as conn:
            rows = conn.execute(select(self._t).order_by(self._t.c.seq)).mappings().all()
        return [AuditEvent(event_id=r["event_id"], event_type=r["event_type"], tenant_id=r["tenant_id"], actor_id=r["actor_id"],
                           payload=r["payload"], previous_hash=r["previous_hash"], event_hash=r["event_hash"], created_at=_aware(r["created_at"]))
                for r in rows]

    @events.setter
    def events(self, value) -> None:  # AuditChain.__init__ assigns an empty list; the table is the only store
        pass

    def _head(self):
        with self._engine.connect() as conn:
            return conn.execute(select(self._t.c.seq, self._t.c.event_hash).order_by(self._t.c.seq.desc()).limit(1)).first()

    def append(self, event_id: str, event_type: str, tenant_id: str, actor_id: str, payload: dict) -> AuditEvent:
        import hashlib
        import json

        with self._lock:
            for _ in range(self._attempts):
                head = self._head()
                seq, previous = (head[0] + 1, head[1]) if head else (1, None)
                body = {"event_id": event_id, "event_type": event_type, "tenant_id": tenant_id, "actor_id": actor_id, "payload": payload, "previous_hash": previous}
                digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                event = AuditEvent(**body, event_hash=digest)
                try:
                    with self._engine.begin() as conn:
                        conn.execute(insert(self._t).values(seq=seq, **body, event_hash=digest, created_at=event.created_at))
                    return event
                except IntegrityError:
                    continue
        raise RuntimeError("audit_chain_busy")


__all__ = ["SqlAuditChain", "SqlConnectionStore"]
