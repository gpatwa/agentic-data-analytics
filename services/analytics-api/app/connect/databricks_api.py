"""`/api/v2/connect/databricks`: connecting a customer's Databricks workspace with a principal the admin pre-created.

The admin runs the generated script (`app/connect/databricks_registration.py`), then gives Compass the principal's application id
and OAuth secret **once**. This module is the custody step: the secret goes straight into the tenant secret store (the vault
adapter in production), is never returned, logged or recorded, and is deleted again if the connection test fails or the connection
is removed. Compass holds no admin credential.

Inert (503) until a service and an identity verifier are configured. Only the `connection-manager` group may act. Every error is
a short stable code. Verified on a workspace only through the dev end-to-end check (CONNECT_DATABRICKS_REGISTRATION_RESULTS.md);
this API, the secret store adapter and the screen have only run against fakes.
"""

from __future__ import annotations

import hashlib
import re
import secrets as _random
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.connect import databricks_registration as reg
from app.connect.api import MANAGER_GROUP
from app.connect.grants import ConnectScope, ScopeError
from app.connect.records import SetupRecorder
from app.connect.store import (
    ConnectionConflict,
    ConnectionNotFound,
    ConnectionRecord,
    ConnectionStore,
    TenantSecretStore,
)
from app.connect.targets import Resolver, TargetRejected, system_resolver, validate_target
from app.security import AuthenticationError, IdentityResolver, OIDCVerifier
from app.security.audit import AuditChain
from packages.platform_contracts.security import AnalyticsIdentity

_APP_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_HTTP_PATH = re.compile(r"^/sql/1\.0/(warehouses|endpoints)/[0-9a-f]{8,32}$")
_SUFFIXES = (".cloud.databricks.com", ".azuredatabricks.net", ".gcp.databricks.com")


class SetupNotFound(KeyError):
    pass


class Refused(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PendingDatabricks:
    setup_id: str
    tenant_id: str
    connection_id: str
    admin_id: str
    host: str
    scope: ConnectScope
    warehouse_name: str
    suffix: str
    script: reg.Script
    application_id: str = ""


@dataclass(frozen=True)
class Completed:
    ok: bool
    code: str | None
    checks: tuple[tuple[str, bool], ...]
    connection_status: str


@dataclass
class DatabricksSetups:
    store: ConnectionStore
    secrets: TenantSecretStore
    chain: AuditChain
    _pending: dict[str, PendingDatabricks] = field(default_factory=dict)
    _recorders: dict[str, SetupRecorder] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def begin(self, *, tenant_id: str, admin_id: str, host: str, catalog: str, tables: list[tuple[str, str]], warehouse_name: str,
              resolver: Resolver = system_resolver) -> PendingDatabricks:
        name = (host or "").strip().lower()
        suffix_found = next((s for s in _SUFFIXES if name.endswith(s)), None)
        validate_target("databricks", name, expected_account=name[: -len(suffix_found)] if suffix_found else "", resolver=resolver)
        scope = ConnectScope(catalog.strip(), tuple((s.strip(), t.strip()) for s, t in tables))
        suffix = _random.token_hex(4)
        script = reg.generate(scope, suffix=suffix, warehouse_name=warehouse_name)
        setup_id, connection_id = _random.token_hex(16), f"dbx-{suffix}"
        pending = PendingDatabricks(setup_id, tenant_id, connection_id, admin_id, name, scope, warehouse_name.strip(), suffix, script)
        recorder = SetupRecorder(self.chain, tenant_id=tenant_id, connection_id=connection_id, admin_id=admin_id)
        recorder.consent_shown([s.text for s in script.steps], engine="databricks", host=name)
        with self._lock:
            self._pending[setup_id] = pending
            self._recorders[setup_id] = recorder
        return pending

    def get(self, tenant_id: str, setup_id: str) -> PendingDatabricks:
        pending = self._pending.get(setup_id)
        if pending is None or pending.tenant_id != tenant_id:
            raise SetupNotFound(setup_id)
        return pending

    def with_application_id(self, pending: PendingDatabricks, application_id: str) -> PendingDatabricks:
        if not _APP_ID.match(application_id or ""):
            raise Refused("invalid_application_id")
        updated = replace(pending, application_id=application_id,
                          script=reg.generate(pending.scope, suffix=pending.suffix, warehouse_name=pending.warehouse_name, application_id=application_id))
        with self._lock:
            self._pending[pending.setup_id] = updated
        return updated

    def cancel(self, pending: PendingDatabricks) -> None:
        with self._lock:
            self._pending.pop(pending.setup_id, None)
            recorder = self._recorders.pop(pending.setup_id, None)
        if recorder:
            recorder.close()

    def complete(self, pending: PendingDatabricks, *, application_id: str, client_secret: str, http_path: str,
                 session_factory: Callable[[ConnectionRecord], Any]) -> Completed:
        if not _APP_ID.match(application_id or ""):
            raise Refused("invalid_application_id")
        if not _HTTP_PATH.match(http_path or ""):
            raise Refused("invalid_http_path")
        if not 16 <= len(client_secret or "") <= 512 or any(c.isspace() for c in client_secret):
            raise Refused("invalid_secret")
        recorder = self._recorders.get(pending.setup_id)
        if recorder is None:
            raise SetupNotFound(pending.setup_id)
        recorder.watch(client_secret)
        tenant, cid, secret_name = pending.tenant_id, pending.connection_id, f"databricks/{pending.connection_id}"
        tables = tuple(f"{pending.scope.database}.{s}.{t}".lower() for s, t in sorted(pending.scope.tables))
        record = ConnectionRecord(
            tenant_id=tenant, connection_id=cid, engine="databricks", host=pending.host, account=pending.host, user=application_id,
            role="", warehouse=pending.warehouse_name, database=pending.scope.database, default_schema=pending.scope.schemas[0],
            allowed_tables=tables, auth_method="oauth_m2m", secret_name=secret_name, http_path=http_path,
            teardown=tuple(s.arg if s.op == "sql" else s.text for s in reg.generate(pending.scope, suffix=pending.suffix, warehouse_name=pending.warehouse_name,
                                                       application_id=application_id).teardown),
        )
        self.secrets.put(tenant, secret_name, client_secret)  # custody first: from here the value lives only in the store
        try:
            self.store.add(record)
        except ConnectionConflict as exc:
            self.secrets.delete(tenant, secret_name)
            raise Refused(exc.code) from None
        recorder.script_run_reported()
        checks = self._test(record, pending, session_factory)
        failed = [name for name, passed in checks if not passed]
        recorder.verified(ok=not failed, failed_checks=failed)
        if failed:
            self._remove_credential(record, recorder)
            self.store.set_status(tenant, cid, "removed")
            code = "connection_test_failed"
        else:
            self.store.set_status(tenant, cid, "active")
            code = None
        self.cancel(pending)
        return Completed(not failed, code, tuple(checks), "removed" if failed else "active")

    def _test(self, record: ConnectionRecord, pending: PendingDatabricks, session_factory: Callable[[ConnectionRecord], Any]) -> list[tuple[str, bool]]:
        """Through the product's session as the new principal: read each named table. Writes are not probed on a customer's data."""
        checks: list[tuple[str, bool]] = []
        try:
            session = session_factory(record)
        except Exception:  # noqa: BLE001 - never echo a driver message
            return [(t, False) for t in record.allowed_tables]
        try:
            for schema, table in sorted(pending.scope.tables):
                label = f"{pending.scope.database}.{schema}.{table}".lower()
                try:
                    stream = session.execute(f"SELECT 1 FROM {reg.bt(pending.scope.database)}.{reg.bt(schema)}.{reg.bt(table)} LIMIT 1", {})
                    stream.fetchmany(1)
                    stream.close()
                    checks.append((label, True))
                except Exception:  # noqa: BLE001
                    checks.append((label, False))
        finally:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
        return checks

    def _remove_credential(self, record: ConnectionRecord, recorder: SetupRecorder) -> None:
        try:
            recorder.credential_deleted(self.secrets.delete(record.tenant_id, record.secret_name or ""))
        except KeyError:
            pass


def make_session_factory(secrets: TenantSecretStore, *, connector: Any | None = None, sdk_core: Any | None = None) -> Callable[[ConnectionRecord], Any]:
    """Opens the product's Databricks session as the principal, reading the secret from custody only at this moment."""
    from app.execution.warehouse_sessions import DatabricksConfig, DatabricksSession

    def factory(record: ConnectionRecord) -> Any:
        config = DatabricksConfig(server_hostname=record.host, http_path=record.http_path, catalog=record.database, schema=record.default_schema,
                                  client_id=record.user, client_secret=secrets.read(record.tenant_id, record.secret_name or ""))
        return DatabricksSession(config, connector=connector, sdk_core=sdk_core)

    return factory


@dataclass
class DatabricksRuntime:
    setups: DatabricksSetups | None = None
    verifier: OIDCVerifier | None = None
    resolver: IdentityResolver | None = None
    session_factory: Callable[[ConnectionRecord], Any] | None = None
    dns: Resolver = system_resolver


class TableRef(BaseModel):
    schema_name: str = Field(alias="schema", min_length=1, max_length=255)
    table: str = Field(min_length=1, max_length=255)


class BeginRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    catalog: str = Field(min_length=1, max_length=255)
    tables: list[TableRef] = Field(min_length=1, max_length=50)
    warehouse_name: str = Field(min_length=1, max_length=255)


class ScriptRequest(BaseModel):
    application_id: str = Field(min_length=1, max_length=64)


class StepView(BaseModel):
    kind: str
    text: str


class ScriptView(BaseModel):
    setup_id: str
    connection_id: str
    host: str
    catalog: str
    tables: list[str]
    summary: list[str]
    steps: list[StepView]
    removal: list[StepView]
    digest: str
    principal_name: str


class CompleteView(BaseModel):
    ok: bool
    code: str | None
    checks: list[dict[str, Any]]
    connection_status: str


def _view(p: PendingDatabricks) -> ScriptView:
    kinds = {"sql": "sql"}
    return ScriptView(
        setup_id=p.setup_id, connection_id=p.connection_id, host=p.host, catalog=p.scope.database,
        tables=[f"{s}.{t}" for s, t in sorted(p.scope.tables)], summary=list(p.script.summary),
        steps=[StepView(kind=kinds.get(s.op, "admin"), text=s.text) for s in p.script.steps],
        removal=[StepView(kind=kinds.get(s.op, "admin"), text=s.text) for s in p.script.teardown], digest=p.script.digest,
        principal_name=p.script.principal,
    )


def build_databricks_router(runtime: DatabricksRuntime, api_key_dependency) -> APIRouter:
    router = APIRouter(prefix="/api/v2/connect/databricks", tags=["connect-databricks"], dependencies=[Depends(api_key_dependency)])

    def identity(authorization: str | None = Header(default=None)) -> AnalyticsIdentity:
        if runtime.setups is None or (runtime.verifier is None and runtime.resolver is None):
            raise HTTPException(503, "connect_not_configured")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "missing_bearer_token", headers={"WWW-Authenticate": "Bearer"})
        try:
            return runtime.resolver.resolve(token) if runtime.resolver else runtime.verifier.verify(token)
        except AuthenticationError as exc:
            raise HTTPException(401, "invalid_identity_token", headers={"WWW-Authenticate": "Bearer"}) from exc

    def manager(who: AnalyticsIdentity = Depends(identity)) -> AnalyticsIdentity:
        if MANAGER_GROUP not in who.groups:
            raise HTTPException(403, "connection_manager_required")
        return who

    def pending_for(setup_id: str, who: AnalyticsIdentity) -> PendingDatabricks:
        try:
            return runtime.setups.get(who.tenant_id, setup_id)
        except SetupNotFound as exc:
            raise HTTPException(404, "setup_not_found") from exc

    @router.get("/capabilities")
    def capabilities(who: AnalyticsIdentity = Depends(identity)) -> dict[str, Any]:
        return {"engine": "databricks", "setup_method": "admin_pre_creates_principal", "can_manage": MANAGER_GROUP in who.groups,
                "stage": "Internal testing. The script ran on one workspace; this screen and the secret custody have only run against fakes."}

    @router.post("/setups", response_model=ScriptView, status_code=201)
    def begin(body: BeginRequest, who: AnalyticsIdentity = Depends(manager)) -> ScriptView:
        try:
            pending = runtime.setups.begin(tenant_id=who.tenant_id, admin_id=who.user_id, host=body.host, catalog=body.catalog,
                                           tables=[(t.schema_name, t.table) for t in body.tables], warehouse_name=body.warehouse_name, resolver=runtime.dns)
        except TargetRejected as exc:
            raise HTTPException(422, exc.code) from exc
        except ScopeError as exc:
            raise HTTPException(422, "invalid_scope") from exc
        return _view(pending)

    @router.post("/setups/{setup_id}/script", response_model=ScriptView)
    def with_id(setup_id: str, body: ScriptRequest, who: AnalyticsIdentity = Depends(manager)) -> ScriptView:
        try:
            return _view(runtime.setups.with_application_id(pending_for(setup_id, who), body.application_id))
        except Refused as exc:
            raise HTTPException(422, exc.code) from exc

    @router.post("/setups/{setup_id}/credentials", response_model=CompleteView)
    def credentials(setup_id: str, body: dict[str, Any] = Body(...), who: AnalyticsIdentity = Depends(manager)) -> CompleteView:
        # The body is read as a plain dict, not a model: a validation error would echo the submitted input, secret included.
        if runtime.session_factory is None:
            raise HTTPException(503, "connection_test_not_configured")
        values = [body.get(k) for k in ("application_id", "client_secret", "http_path")]
        if not all(isinstance(v, str) for v in values):
            raise HTTPException(422, "invalid_request")
        application_id, client_secret, http_path = values
        try:
            result = runtime.setups.complete(pending_for(setup_id, who), application_id=application_id, client_secret=client_secret,
                                             http_path=http_path, session_factory=runtime.session_factory)
        except Refused as exc:
            raise HTTPException(409 if exc.code in ("connection_id_taken", "target_claimed") else 422, exc.code) from exc
        except SetupNotFound as exc:
            raise HTTPException(404, "setup_not_found") from exc
        return CompleteView(ok=result.ok, code=result.code, checks=[{"table": t, "passed": p} for t, p in result.checks],
                            connection_status=result.connection_status)

    @router.post("/setups/{setup_id}/cancel", status_code=204, response_model=None)
    def cancel(setup_id: str, who: AnalyticsIdentity = Depends(manager)) -> None:
        runtime.setups.cancel(pending_for(setup_id, who))

    @router.delete("/connections/{connection_id}")
    def remove(connection_id: str, who: AnalyticsIdentity = Depends(manager)) -> dict[str, Any]:
        try:
            record = runtime.setups.store.get(who.tenant_id, connection_id)
        except ConnectionNotFound as exc:
            raise HTTPException(404, "connection_not_found") from exc
        if record.engine != "databricks":
            raise HTTPException(404, "connection_not_found")
        recorder = SetupRecorder(runtime.setups.chain, tenant_id=who.tenant_id, connection_id=connection_id, admin_id=who.user_id)
        runtime.setups._remove_credential(record, recorder)
        runtime.setups.store.set_status(who.tenant_id, connection_id, "removed")
        recorder.access_removed(teardown_digest=hashlib.sha256("\n".join(record.teardown).encode()).hexdigest()[:16])
        return {"connection_id": connection_id, "status": "removed", "credential_deleted": True, "teardown": list(record.teardown),
                "note": "Compass can no longer connect. The principal and its grants remain until your admin runs the removal script."}

    return router
