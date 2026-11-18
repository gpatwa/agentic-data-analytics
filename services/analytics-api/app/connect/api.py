"""`/api/v2/connect` endpoints for connecting a customer's own warehouse (CM4).

Inert until a runtime is configured: with no coordinator or identity verifier every call returns 503, like the v2 analytics
router, so nothing activates by implication. Only people in the `connection-manager` group can start, verify, cancel or
remove a connection. Today the only setup method offered is the script the customer's admin runs (`method: "script"`);
product-run setup is reported as unavailable until a sign-in exists and is verified (see the capabilities response).

No response carries a key, token or secret reference. Every error is a short stable code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.connect.checks import CHECK_WORDS, NOT_RUN_ON_CUSTOMER_DATA, grants_connection_test
from app.connect.grants import ConnectScope, ScopeError
from app.connect.store import ConnectionConflict, ConnectionNotFound, ConnectionRecord, ConnectionStore
from app.connect.targets import Resolver, TargetRejected, system_resolver
from app.connect.worker import PendingSetup, SetupCoordinator, SetupNotFound
from app.security import AuthenticationError, IdentityResolver, OIDCVerifier
from packages.platform_contracts.security import AnalyticsIdentity

MANAGER_GROUP = "connection-manager"


@dataclass
class ConnectRuntime:
    coordinator: SetupCoordinator | None = None
    store: ConnectionStore | None = None
    verifier: OIDCVerifier | None = None
    resolver: IdentityResolver | None = None
    session_factory: Callable[[ConnectionRecord], Any] | None = None  # opens a session as the service identity
    dns: Resolver = system_resolver
    allowed_networks: tuple[str, ...] = field(default_factory=tuple)  # Compass's egress addresses, put in the script


class TableRef(BaseModel):
    schema_name: str = Field(alias="schema", min_length=1, max_length=255)
    table: str = Field(min_length=1, max_length=255)


class SetupRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    account: str = Field(min_length=1, max_length=255)
    database: str = Field(min_length=1, max_length=255)
    tables: list[TableRef] = Field(min_length=1, max_length=50)
    credit_quota: int = Field(default=1, ge=1, le=100)


class SetupView(BaseModel):
    setup_id: str
    connection_id: str
    status: str
    method: str
    host: str
    account: str
    database: str
    tables: list[str]
    summary: list[str]
    statements: list[str]
    teardown: list[str]
    digest: str


class CheckView(BaseModel):
    name: str
    label: str
    passed: bool


class VerifyView(BaseModel):
    ok: bool
    code: str | None
    checks: list[CheckView]
    not_run_on_customer_data: list[str]
    connection_status: str


class ConnectionView(BaseModel):
    connection_id: str
    engine: str
    host: str
    account: str
    status: str
    tables: list[str]
    auth_method: str


class RemovalView(BaseModel):
    connection_id: str
    status: str
    credential_deleted: bool
    teardown: list[str]
    teardown_digest: str
    note: str


def _connection_view(record: ConnectionRecord) -> ConnectionView:
    return ConnectionView(
        connection_id=record.connection_id, engine=record.engine, host=record.host, account=record.account,
        status=record.status, tables=list(record.allowed_tables), auth_method=record.auth_method,
    )


def _setup_view(pending: PendingSetup) -> SetupView:
    return SetupView(
        setup_id=pending.setup_id, connection_id=pending.connection_id, status="awaiting_admin", method="script",
        host=pending.host, account=pending.account, database=pending.scope.database, tables=list(pending.scope.allowed_tables),
        summary=list(pending.script.summary), statements=list(pending.script.statements),
        teardown=list(pending.script.teardown), digest=pending.script.digest,
    )


def build_connect_router(runtime: ConnectRuntime, api_key_dependency) -> APIRouter:
    router = APIRouter(prefix="/api/v2/connect", tags=["connect"], dependencies=[Depends(api_key_dependency)])

    def identity(authorization: str | None = Header(default=None)) -> AnalyticsIdentity:
        if runtime.coordinator is None or runtime.store is None or (runtime.verifier is None and runtime.resolver is None):
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

    @router.get("/capabilities")
    def capabilities(who: AnalyticsIdentity = Depends(identity)) -> dict[str, Any]:
        return {
            "engines": [
                {
                    "engine": "snowflake",
                    "stage": "Connect mode is in internal testing. It has not had a security review and has not run against a customer account.",
                    "setup_methods": ["script"],
                    "product_run_setup": {"available": False, "reason": "admin_sign_in_not_built"},
                    "credential": {"kind": "key_pair", "note": "Created by Compass; only the public half goes in the script."},
                },
            ],
            "can_manage": MANAGER_GROUP in who.groups,
        }

    @router.post("/setups", response_model=SetupView, status_code=201)
    def begin(body: SetupRequest, who: AnalyticsIdentity = Depends(manager)) -> SetupView:
        try:
            scope = ConnectScope(body.database, tuple((t.schema_name, t.table) for t in body.tables))
            pending = runtime.coordinator.begin(
                tenant_id=who.tenant_id, admin_id=who.user_id, requested_by=who.user_id, host=body.host,
                account=body.account, scope=scope, credit_quota=body.credit_quota,
                allowed_networks=runtime.allowed_networks, resolver=runtime.dns,
            )
        except TargetRejected as exc:
            raise HTTPException(422, exc.code) from exc
        except ScopeError as exc:
            raise HTTPException(422, "invalid_scope") from exc
        except ConnectionConflict as exc:
            raise HTTPException(409, exc.code) from exc
        return _setup_view(pending)

    @router.get("/setups/{setup_id}", response_model=SetupView)
    def get_setup(setup_id: str, who: AnalyticsIdentity = Depends(identity)) -> SetupView:
        try:
            return _setup_view(runtime.coordinator.get_pending(who.tenant_id, setup_id))
        except SetupNotFound as exc:
            raise HTTPException(404, "setup_not_found") from exc

    @router.post("/setups/{setup_id}/verify", response_model=VerifyView)
    def verify(setup_id: str, who: AnalyticsIdentity = Depends(manager)) -> VerifyView:
        if runtime.session_factory is None:
            raise HTTPException(503, "connection_test_not_configured")
        try:
            pending = runtime.coordinator.get_pending(who.tenant_id, setup_id)
            result = runtime.coordinator.verify_script_run(pending, connection_test=grants_connection_test(runtime.session_factory, pending))
        except SetupNotFound as exc:
            raise HTTPException(404, "setup_not_found") from exc
        failed = set(result.failed_checks)
        checks = [CheckView(name=name, label=label, passed=name not in failed) for name, label in CHECK_WORDS.items()]
        record = runtime.store.get(who.tenant_id, result.connection_id)
        return VerifyView(
            ok=result.ok, code=result.code, checks=checks,
            not_run_on_customer_data=list(NOT_RUN_ON_CUSTOMER_DATA), connection_status=record.status,
        )

    @router.post("/setups/{setup_id}/cancel", status_code=204, response_model=None)
    def cancel(setup_id: str, who: AnalyticsIdentity = Depends(manager)) -> None:
        try:
            runtime.coordinator.cancel(runtime.coordinator.get_pending(who.tenant_id, setup_id))
        except SetupNotFound as exc:
            raise HTTPException(404, "setup_not_found") from exc

    @router.get("/connections", response_model=list[ConnectionView])
    def connections(who: AnalyticsIdentity = Depends(identity)) -> list[ConnectionView]:
        return [_connection_view(r) for r in runtime.store.list(who.tenant_id) if r.status != "removed"]

    @router.delete("/connections/{connection_id}", response_model=RemovalView)
    def remove(connection_id: str, who: AnalyticsIdentity = Depends(manager)) -> RemovalView:
        try:
            teardown, digest = runtime.coordinator.remove_access(who.tenant_id, connection_id, actor_id=who.user_id)
        except ConnectionNotFound as exc:
            raise HTTPException(404, "connection_not_found") from exc
        return RemovalView(
            connection_id=connection_id, status="removed", credential_deleted=True, teardown=list(teardown), teardown_digest=digest,
            note="Compass can no longer connect. The objects in your warehouse remain until your admin runs the statements above.",
        )

    return router
