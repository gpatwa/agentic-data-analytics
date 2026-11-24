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
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.connect import databricks_registration as reg
from app.connect.api import MANAGER_GROUP
from app.connect.drift import ROTATE_WITHIN_DAYS
from app.connect.enrol import (
    AttemptLimiter,
    EnrolmentTokens,
    EnrolRefused,
    check_readback,
    render_enrol_file,
    render_rotate_file,
)
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
    tokens: EnrolmentTokens = field(default_factory=EnrolmentTokens)
    limiter: AttemptLimiter = field(default_factory=AttemptLimiter)
    enrol_state: dict[str, dict[str, Any]] = field(default_factory=dict)  # what the screen shows while the admin runs the file: waiting, refused or connected (codes and counts only)
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
            secret_expires_at=datetime.now(timezone.utc) + timedelta(seconds=reg.SECRET_LIFETIME_SECONDS),
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
        checks = self._test(record, session_factory)
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

    def _test(self, record: ConnectionRecord, session_factory: Callable[[ConnectionRecord], Any]) -> list[tuple[str, bool]]:
        """Through the product's session as the principal: read each named table. Writes are not probed on a customer's data."""
        checks: list[tuple[str, bool]] = []
        try:
            session = session_factory(record)
        except Exception:  # noqa: BLE001 - never echo a driver message
            return [(t, False) for t in record.allowed_tables]
        try:
            for label in record.allowed_tables:
                catalog, schema, table = label.split(".")
                try:
                    stream = session.execute(f"SELECT 1 FROM {reg.bt(catalog)}.{reg.bt(schema)}.{reg.bt(table)} LIMIT 1", {})
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

    # ---- rotation, expiry, health -----------------------------------------------------------------------------------------

    def _owned(self, tenant_id: str, connection_id: str) -> ConnectionRecord:
        record = self.store.get(tenant_id, connection_id)
        if record.engine != "databricks" or record.auth_method != "oauth_m2m" or record.status == "removed":
            raise ConnectionNotFound("connection not found")
        return record

    def rotate(self, tenant_id: str, connection_id: str, *, actor_id: str, client_secret: str,
               session_factory: Callable[[ConnectionRecord], Any]) -> Completed:
        """Replace the stored secret with a new one the admin made. The old value is kept until the new one is proven: if the check
        fails the old value is put back and the connection is left as it was."""
        if not 16 <= len(client_secret or "") <= 512 or any(c.isspace() for c in client_secret):
            raise Refused("invalid_secret")
        record = self._owned(tenant_id, connection_id)
        recorder = SetupRecorder(self.chain, tenant_id=tenant_id, connection_id=connection_id, admin_id=actor_id)
        recorder.watch(client_secret)
        try:
            previous: str | None = self.secrets.read(tenant_id, record.secret_name or "")
        except KeyError:
            previous = None
        self.secrets.put(tenant_id, record.secret_name or "", client_secret)
        checks = self._test(record, session_factory)
        failed = [name for name, passed in checks if not passed]
        if failed:
            if previous is not None:
                self.secrets.put(tenant_id, record.secret_name or "", previous)
            else:
                self._remove_credential(record, recorder)
            recorder.health_checked(ok=False, failed_checks=failed)
            recorder.close()
            return Completed(False, "connection_test_failed", tuple(checks), record.status)
        expires = datetime.now(timezone.utc) + timedelta(seconds=reg.SECRET_LIFETIME_SECONDS)
        updated = self.store.update(replace(record, status="active", secret_expires_at=expires))
        recorder.secret_replaced(expires_at=expires.isoformat())
        recorder.close()
        return Completed(True, None, tuple(checks), updated.status)

    def health_check(self, tenant_id: str, connection_id: str, *, actor_id: str, session_factory: Callable[[ConnectionRecord], Any],
                     now: datetime | None = None) -> Completed:
        """Read each named table now. A pass keeps (or restores) `active`; a fail suspends the connection, so the gateway stops using
        it until the admin rotates the secret. An expired secret is suspended without a call."""
        record = self._owned(tenant_id, connection_id)
        recorder = SetupRecorder(self.chain, tenant_id=tenant_id, connection_id=connection_id, admin_id=actor_id)
        moment = now or datetime.now(timezone.utc)
        if record.secret_expires_at is not None and record.secret_expires_at <= moment:
            checks: list[tuple[str, bool]] = [(t, False) for t in record.allowed_tables]
        else:
            checks = self._test(record, session_factory)
        failed = [name for name, passed in checks if not passed]
        status = "suspended" if failed else "active"
        if status != record.status:
            self.store.set_status(tenant_id, connection_id, status)
        recorder.health_checked(ok=not failed, failed_checks=failed)
        recorder.close()
        return Completed(not failed, "connection_test_failed" if failed else None, tuple(checks), status)

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
    enrol_url: str = ""  # the public address of POST .../enrol; empty means the enrol file is not offered (503)


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


class DbxConnectionView(BaseModel):
    connection_id: str
    host: str
    catalog: str
    tables: list[str]
    status: str
    expires_at: str | None
    expires_in_days: int | None
    needs_rotation: bool
    warm_up: bool = False
    application_id: str = ""  # the principal's id (not a secret): the screen shows the file's --remove command with it




def connection_view(record: ConnectionRecord, now: datetime | None = None) -> DbxConnectionView:
    moment = now or datetime.now(timezone.utc)
    days = None if record.secret_expires_at is None else (record.secret_expires_at - moment).days
    return DbxConnectionView(
        connection_id=record.connection_id, host=record.host, catalog=record.database, tables=list(record.allowed_tables), status=record.status,
        expires_at=record.secret_expires_at.isoformat() if record.secret_expires_at else None, expires_in_days=days,
        needs_rotation=record.status != "active" or (days is not None and days <= ROTATE_WITHIN_DAYS), warm_up=record.warm_up, application_id=record.user,
    )


class WarmUpRequest(BaseModel):
    enabled: bool


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
    warehouse_name: str = ""


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
        principal_name=p.script.principal, warehouse_name=p.warehouse_name,
    )


ENROL_PATH = "/api/v2/connect/databricks/enrol"
ROTATE_PATH = ENROL_PATH + "/rotate"


def build_enrol_router(runtime: DatabricksRuntime) -> APIRouter:
    """The one call the enrol file makes. Authorised by the single-use enrolment token alone (no identity token, no API key: the file runs on the admin's machine), so it can do nothing but
    submit one credential for the one setup the token is bound to. Every answer is a short code; the body is read as a plain dict so no validation error can echo the secret."""
    router = APIRouter(tags=["connect-databricks"])

    def redeem(request: Request, authorization: str | None, purpose: str):
        """The shared front of both calls: the failed-attempt limit, then the bearer token, spent on first valid use."""
        setups = runtime.setups
        if setups is None or runtime.session_factory is None:
            raise HTTPException(503, "connect_not_configured")
        caller = request.client.host if request.client else "unknown"
        try:
            setups.limiter.check(caller)
        except EnrolRefused as exc:
            raise HTTPException(429, exc.code) from exc
        scheme, _, token = (authorization or "").partition(" ")
        try:
            if scheme.lower() != "bearer":
                raise EnrolRefused("token_invalid")
            return setups.tokens.redeem(token, purpose)
        except EnrolRefused as exc:
            setups.limiter.failed(caller)
            raise HTTPException(401, exc.code, headers={"WWW-Authenticate": "Bearer"}) from exc

    @router.post(ENROL_PATH)
    def enrol(request: Request, body: dict[str, Any] = Body(...), authorization: str | None = Header(default=None)) -> dict[str, Any]:
        setups = runtime.setups
        entry = redeem(request, authorization, "enrol")
        try:
            pending = setups.get(entry.tenant_id, entry.setup_id)
        except SetupNotFound as exc:
            raise HTTPException(404, "setup_not_found") from exc
        if pending.script.digest != entry.digest:
            raise HTTPException(409, "setup_changed")
        values = [body.get(k) for k in ("application_id", "client_secret", "http_path")]
        if not all(isinstance(v, str) for v in values):
            raise HTTPException(422, "invalid_request")
        application_id, client_secret, http_path = values

        def audit(kind: str, payload: dict[str, Any]) -> None:
            setups.chain.append(f"{pending.connection_id}:enrol:{len(setups.chain.events) + 1}", kind, pending.tenant_id, pending.admin_id, {"connection_id": pending.connection_id, **payload})

        try:
            report = check_readback(pending, application_id, body.get("grants"), body.get("warehouse_acl"))
        except EnrolRefused as exc:
            raise HTTPException(422, exc.code) from exc

        def note(state: str, code: str | None, checks: list[dict[str, Any]]) -> None:
            known = setups.enrol_state.get(pending.setup_id)
            if known is not None:
                known.update({"state": state, "code": code, "checks": checks})

        if not report.ok:
            note("refused", report.code, [])
            audit("connect.enrol_refused", {"code": report.code})
            raise HTTPException(422, report.code or "registration_not_verified")
        try:
            result = setups.complete(pending, application_id=application_id, client_secret=client_secret, http_path=http_path, session_factory=runtime.session_factory)
        except Refused as exc:
            raise HTTPException(409 if exc.code in ("connection_id_taken", "target_claimed") else 422, exc.code) from exc
        note("connected" if result.ok else "refused", result.code, [{"table": t, "passed": p} for t, p in result.checks])
        audit("connect.enrolled", {"ok": result.ok, "code": result.code})
        return {"ok": result.ok, "code": result.code, "connection_id": pending.connection_id, "connection_status": result.connection_status,
                "checks": [{"table": t, "passed": p} for t, p in result.checks]}

    @router.post(ROTATE_PATH)
    def rotate_by_file(request: Request, body: dict[str, Any] = Body(...), authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """The rotate file's one call: a new secret for an existing connection. The old one is kept until the new one reads every table; if it does not, the old one is put back."""
        setups = runtime.setups
        entry = redeem(request, authorization, "rotate")
        try:
            record = setups._owned(entry.tenant_id, entry.setup_id)  # for a rotation the token's subject is the connection id
        except ConnectionNotFound as exc:
            raise HTTPException(404, "connection_not_found") from exc
        if record.user != entry.digest:
            raise HTTPException(409, "connection_changed")
        value = body.get("client_secret")
        if not isinstance(value, str):
            raise HTTPException(422, "invalid_request")
        try:
            result = setups.rotate(entry.tenant_id, record.connection_id, actor_id=entry.actor_id or "enrol-file", client_secret=value, session_factory=runtime.session_factory)
        except Refused as exc:
            raise HTTPException(422, exc.code) from exc
        known = setups.enrol_state.get(f"rotate:{record.connection_id}")
        if known is not None:
            known.update({"state": "connected" if result.ok else "refused", "code": result.code, "checks": [{"table": t, "passed": p} for t, p in result.checks]})
        setups.chain.append(f"{record.connection_id}:rotate_file:{len(setups.chain.events) + 1}", "connect.rotated_by_file", record.tenant_id, entry.actor_id or "enrol-file",
                            {"connection_id": record.connection_id, "ok": result.ok, "code": result.code})
        return {"ok": result.ok, "code": result.code, "connection_status": result.connection_status, "checks": [{"table": t, "passed": p} for t, p in result.checks]}

    return router


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

    @router.post("/setups/{setup_id}/enrol-file")
    def enrol_file(setup_id: str, who: AnalyticsIdentity = Depends(manager)) -> dict[str, Any]:
        """The one file the admin runs (docs/design/SELF_SERVE_CONNECT_DESIGN.md). It carries a single-use token valid for 30 minutes; a new file revokes the old token."""
        if not runtime.enrol_url:
            raise HTTPException(503, "enrol_not_configured")
        pending = pending_for(setup_id, who)
        try:
            token, expires_at = runtime.setups.tokens.mint(tenant_id=who.tenant_id, setup_id=pending.setup_id, digest=pending.script.digest)
            file = render_enrol_file(pending.scope, suffix=pending.suffix, warehouse_name=pending.warehouse_name, host=pending.host, enrol_url=runtime.enrol_url, token=token)
        except EnrolRefused as exc:
            raise HTTPException(503 if exc.code == "enrol_url_invalid" else 422, exc.code) from exc
        runtime.setups.enrol_state[pending.setup_id] = {"tenant_id": who.tenant_id, "state": "waiting", "code": None, "checks": [], "expires_at": expires_at}
        runtime.setups.chain.append(f"{pending.connection_id}:enrol_file:{len(runtime.setups.chain.events) + 1}", "connect.enrol_file_issued", who.tenant_id, who.user_id,
                                    {"connection_id": pending.connection_id, "file_digest": file.digest, "script_digest": pending.script.digest})
        return {"filename": file.filename, "content": file.content, "digest": file.digest, "expires_in_minutes": int((expires_at - runtime.setups.tokens._clock()) // 60),
                "run": "python3 compass-enrol.py   (add --profile <name> to use a Databricks CLI profile; python3 compass-enrol.py --digest prints the digest above)"}

    @router.get("/setups/{setup_id}/status")
    def enrol_status(setup_id: str, who: AnalyticsIdentity = Depends(manager)) -> dict[str, Any]:
        """What the screen shows while the admin runs the enrol file: waiting, refused (with a code), connected (with the per-table checks) or expired. Codes and counts only."""
        state = runtime.setups.enrol_state.get(setup_id)
        if state is None or state["tenant_id"] != who.tenant_id:
            raise HTTPException(404, "setup_not_found")
        now = runtime.setups.tokens._clock()
        expired = state["state"] == "waiting" and state["expires_at"] < now
        return {"state": "expired" if expired else state["state"], "code": state["code"], "checks": state["checks"],
                "expires_in_minutes": max(0, int((state["expires_at"] - now) // 60)) if state["state"] == "waiting" and not expired else 0}

    @router.post("/setups/{setup_id}/cancel", status_code=204, response_model=None)
    def cancel(setup_id: str, who: AnalyticsIdentity = Depends(manager)) -> None:
        runtime.setups.cancel(pending_for(setup_id, who))

    @router.get("/connections", response_model=list[DbxConnectionView])
    def connections(who: AnalyticsIdentity = Depends(identity)) -> list[DbxConnectionView]:
        return [connection_view(r) for r in runtime.setups.store.list(who.tenant_id) if r.engine == "databricks" and r.status != "removed"]

    @router.post("/connections/{connection_id}/secret", response_model=CompleteView)
    def rotate(connection_id: str, body: dict[str, Any] = Body(...), who: AnalyticsIdentity = Depends(manager)) -> CompleteView:
        # A plain dict, for the same reason as `credentials`: a validation error would echo the secret.
        if runtime.session_factory is None:
            raise HTTPException(503, "connection_test_not_configured")
        value = body.get("client_secret")
        if not isinstance(value, str):
            raise HTTPException(422, "invalid_request")
        try:
            result = runtime.setups.rotate(who.tenant_id, connection_id, actor_id=who.user_id, client_secret=value, session_factory=runtime.session_factory)
        except Refused as exc:
            raise HTTPException(422, exc.code) from exc
        except ConnectionNotFound as exc:
            raise HTTPException(404, "connection_not_found") from exc
        return CompleteView(ok=result.ok, code=result.code, checks=[{"table": t, "passed": p} for t, p in result.checks], connection_status=result.connection_status)

    @router.post("/connections/{connection_id}/rotate-file")
    def rotate_file(connection_id: str, who: AnalyticsIdentity = Depends(manager)) -> dict[str, Any]:
        """The one file that gives an existing connection a new secret, with no secret to copy. Same token rules as the enrol file."""
        if not runtime.enrol_url:
            raise HTTPException(503, "enrol_not_configured")
        try:
            record = runtime.setups._owned(who.tenant_id, connection_id)
        except ConnectionNotFound as exc:
            raise HTTPException(404, "connection_not_found") from exc
        try:
            token, expires_at = runtime.setups.tokens.mint(tenant_id=who.tenant_id, setup_id=connection_id, digest=record.user, purpose="rotate", actor_id=who.user_id)
            file = render_rotate_file(application_id=record.user, host=record.host, rotate_url=f"{runtime.enrol_url.rstrip('/')}/rotate", token=token)
        except EnrolRefused as exc:
            raise HTTPException(503 if exc.code == "enrol_url_invalid" else 422, exc.code) from exc
        runtime.setups.enrol_state[f"rotate:{connection_id}"] = {"tenant_id": who.tenant_id, "state": "waiting", "code": None, "checks": [], "expires_at": expires_at}
        runtime.setups.chain.append(f"{connection_id}:rotate_file:{len(runtime.setups.chain.events) + 1}", "connect.rotate_file_issued", who.tenant_id, who.user_id,
                                    {"connection_id": connection_id, "file_digest": file.digest})
        return {"filename": file.filename, "content": file.content, "digest": file.digest, "expires_in_minutes": int((expires_at - runtime.setups.tokens._clock()) // 60),
                "run": "python3 compass-rotate.py   (add --profile <name> to use a Databricks CLI profile; python3 compass-rotate.py --digest prints the digest above)"}

    @router.get("/connections/{connection_id}/rotate-status")
    def rotate_status(connection_id: str, who: AnalyticsIdentity = Depends(manager)) -> dict[str, Any]:
        state = runtime.setups.enrol_state.get(f"rotate:{connection_id}")
        if state is None or state["tenant_id"] != who.tenant_id:
            raise HTTPException(404, "setup_not_found")
        now = runtime.setups.tokens._clock()
        expired = state["state"] == "waiting" and state["expires_at"] < now
        return {"state": "expired" if expired else state["state"], "code": state["code"], "checks": state["checks"],
                "expires_in_minutes": max(0, int((state["expires_at"] - now) // 60)) if state["state"] == "waiting" and not expired else 0}

    @router.post("/connections/{connection_id}/check", response_model=CompleteView)
    def check(connection_id: str, who: AnalyticsIdentity = Depends(manager)) -> CompleteView:
        if runtime.session_factory is None:
            raise HTTPException(503, "connection_test_not_configured")
        try:
            result = runtime.setups.health_check(who.tenant_id, connection_id, actor_id=who.user_id, session_factory=runtime.session_factory)
        except ConnectionNotFound as exc:
            raise HTTPException(404, "connection_not_found") from exc
        return CompleteView(ok=result.ok, code=result.code, checks=[{"table": t, "passed": p} for t, p in result.checks], connection_status=result.connection_status)

    @router.put("/connections/{connection_id}/warm-up", response_model=DbxConnectionView)
    def set_warm_up(connection_id: str, body: WarmUpRequest, who: AnalyticsIdentity = Depends(manager)) -> DbxConnectionView:
        """Turn warming on or off for one connection. Cost, said here and in the screen that calls this: when it is on, opening a workspace after a quiet period runs a few
        short read-only queries on the customer's warehouse, and starts the warehouse if it was stopped. Off by default."""
        try:
            record = runtime.setups.store.get(who.tenant_id, connection_id)
        except ConnectionNotFound as exc:
            raise HTTPException(404, "connection_not_found") from exc
        if record.engine != "databricks" or record.status == "removed":
            raise HTTPException(404, "connection_not_found")
        if record.warm_up != body.enabled:
            record = runtime.setups.store.update(replace(record, warm_up=body.enabled))
            runtime.setups.chain.append(f"{connection_id}:warm_up:{len(runtime.setups.chain.events) + 1}", "connect.warm_up_changed", who.tenant_id, who.user_id,
                                        {"connection_id": connection_id, "enabled": body.enabled})
        return connection_view(record)

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
