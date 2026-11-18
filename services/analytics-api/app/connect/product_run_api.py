"""`/api/v2/connect/product-run` endpoints (CM5c): register once, plan a connection, approve it, run it after sign-in.

The sign-in itself is `app/connect/oauth_api.py` (`POST /api/v2/connect/oauth/start` then Snowflake's redirect). The order is
register, plan, approve, sign in, run; `run` refuses an unapproved setup before touching the token. Only the
`connection-manager` group can use any of it, and everything is tenant-scoped: another tenant's registration or setup is simply
not found. Inert (503) until a service and an identity verifier are configured. No response carries a key, token or code.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.connect.approval import ApprovalError
from app.connect.checks import grants_connection_test
from app.connect.grants import ConnectScope, ScopeError
from app.connect.oauth import TokenHandoffError
from app.connect.oauth_api import MANAGER_GROUP
from app.connect.product_run import ProductRunService, Registration, RegistrationConflict, RegistrationNotFound
from app.connect.registration import OutsideCeiling, generate_registration
from app.connect.store import ConnectionConflict
from app.connect.targets import Resolver, TargetRejected, system_resolver
from app.connect.worker import PendingSetup, SetupCoordinator, SetupNotFound
from app.security import AuthenticationError, IdentityResolver, OIDCVerifier
from packages.platform_contracts.security import AnalyticsIdentity


@dataclass
class ProductRunRuntime:
    service: ProductRunService | None = None
    coordinator: SetupCoordinator | None = None
    verifier: OIDCVerifier | None = None
    resolver: IdentityResolver | None = None
    redirect_uri: str | None = None  # the one fixed https callback of this deployment; never taken from a request
    egress_networks: tuple[str, ...] = ()  # Compass's own public addresses, put in the registration script (not user-supplied)
    session_factory: Callable[[Any], Any] | None = None  # opens a session as the new service user, for the connection test
    dns: Resolver = field(default=system_resolver)


class TableRef(BaseModel):
    schema_name: str = Field(alias="schema", min_length=1, max_length=255)
    table: str = Field(min_length=1, max_length=255)


class RegisterBody(BaseModel):
    registration_id: str = Field(min_length=8, max_length=64)
    reg_suffix: str = Field(min_length=8, max_length=8)
    host: str = Field(min_length=1, max_length=255)
    client_id: str = Field(min_length=8, max_length=200)
    account_locator: str = Field(min_length=3, max_length=40)
    database: str = Field(min_length=1, max_length=255)
    tables: list[TableRef] = Field(min_length=1, max_length=50)
    setup_users: list[str] = Field(min_length=1, max_length=10)


class ScriptBody(BaseModel):
    database: str = Field(min_length=1, max_length=255)
    tables: list[TableRef] = Field(min_length=1, max_length=50)
    setup_users: list[str] = Field(min_length=1, max_length=10)
    credit_quota: int = Field(default=1, ge=1, le=100)


class ScriptView(BaseModel):
    reg_suffix: str
    summary: list[str]
    statements: list[str]
    teardown: list[str]
    digest: str


class SetupBody(BaseModel):
    registration_id: str = Field(min_length=8, max_length=64)
    tables: list[TableRef] = Field(min_length=1, max_length=50)


class DigestBody(BaseModel):
    digest: str = Field(min_length=64, max_length=64)


class TableOut(BaseModel):
    model_config = {"populate_by_name": True}
    schema_name: str = Field(alias="schema")
    table: str


class RegistrationView(BaseModel):
    registration_id: str
    host: str
    setup_role: str
    database: str
    ceiling_tables: list[str]  # lower-case qualified names, for display
    ceiling: list[TableOut]  # the exact names a connection may be planned from
    setup_user_count: int


class ProductSetupView(BaseModel):
    setup_id: str
    connection_id: str
    registration_id: str
    status: str
    approved: bool
    signed_in: bool
    summary: list[str]
    statements: list[str]
    teardown: list[str]
    digest: str


class RunView(BaseModel):
    ok: bool
    code: str | None
    created: list[str]
    leftovers: list[str]
    connection_id: str
    connection_status: str
    failed_checks: list[str]


def _registration_view(r: Registration) -> RegistrationView:
    return RegistrationView(
        registration_id=r.registration_id, host=r.config.host, setup_role=r.names.setup_role, database=r.ceiling.database,
        ceiling_tables=list(r.ceiling.allowed_tables), setup_user_count=len(r.setup_users),
        ceiling=[TableOut(schema=s, table=t) for s, t in sorted(r.ceiling.tables)],
    )


def build_product_run_router(runtime: ProductRunRuntime, api_key_dependency) -> APIRouter:
    router = APIRouter(prefix="/api/v2/connect/product-run", tags=["connect"], dependencies=[Depends(api_key_dependency)])

    def identity(authorization: str | None = Header(default=None)) -> AnalyticsIdentity:
        if runtime.service is None or runtime.coordinator is None or (runtime.verifier is None and runtime.resolver is None):
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

    def pending_for(who: AnalyticsIdentity, setup_id: str) -> PendingSetup:
        try:
            return runtime.coordinator.get_pending(who.tenant_id, setup_id)
        except SetupNotFound:
            raise HTTPException(404, "setup_not_found") from None

    def view(pending: PendingSetup) -> ProductSetupView:
        approved, signed_in = runtime.service.state(pending)
        status = "awaiting_approval" if not approved else "awaiting_sign_in" if not signed_in else "ready_to_run"
        return ProductSetupView(
            setup_id=pending.setup_id, connection_id=pending.connection_id, registration_id=runtime.service.registration_of(pending.setup_id),
            status=status, approved=approved, signed_in=signed_in, summary=list(pending.script.summary),
            statements=list(pending.script.statements), teardown=list(pending.script.teardown), digest=pending.script.digest,
        )

    @router.post("/registration-script", response_model=ScriptView)
    def registration_script(body: ScriptBody, who: AnalyticsIdentity = Depends(manager)) -> ScriptView:
        """Draft the one-time script the customer's admin runs. Nothing is stored; the redirect and Compass's addresses are the
        deployment's, never the caller's. The returned suffix is echoed back at registration."""
        if not runtime.redirect_uri or not runtime.egress_networks:
            raise HTTPException(503, "registration_not_configured")
        try:
            ceiling = ConnectScope(body.database, tuple((t.schema_name, t.table) for t in body.tables))
            suffix = secrets.token_hex(4)
            script = generate_registration(ceiling, suffix=suffix, redirect_uri=runtime.redirect_uri, egress_networks=runtime.egress_networks,
                                           setup_users=body.setup_users, credit_quota=body.credit_quota)
        except (ScopeError, ValueError) as exc:
            raise HTTPException(422, "invalid_registration") from exc
        return ScriptView(reg_suffix=suffix, summary=list(script.summary), statements=list(script.statements),
                          teardown=list(script.teardown), digest=script.digest)

    @router.post("/registrations", response_model=RegistrationView, status_code=201)
    def register(body: RegisterBody, who: AnalyticsIdentity = Depends(manager)) -> RegistrationView:
        if not runtime.redirect_uri:
            raise HTTPException(503, "redirect_not_configured")
        try:
            ceiling = ConnectScope(body.database, tuple((t.schema_name, t.table) for t in body.tables))
            registration = runtime.service.register(
                tenant_id=who.tenant_id, registration_id=body.registration_id, reg_suffix=body.reg_suffix, host=body.host, client_id=body.client_id,
                account_locator=body.account_locator, ceiling=ceiling, setup_users=body.setup_users, redirect_uri=runtime.redirect_uri,
                resolver=runtime.dns,
            )
        except TargetRejected as exc:
            raise HTTPException(422, exc.code) from exc
        except RegistrationConflict as exc:
            raise HTTPException(409, exc.code) from exc
        except (ScopeError, ValueError) as exc:
            raise HTTPException(422, "invalid_registration") from exc
        return _registration_view(registration)

    @router.get("/registrations", response_model=list[RegistrationView])
    def registrations(who: AnalyticsIdentity = Depends(identity)) -> list[RegistrationView]:
        return [_registration_view(r) for r in runtime.service.registrations(who.tenant_id)]

    @router.post("/setups", response_model=ProductSetupView, status_code=201)
    def plan(body: SetupBody, who: AnalyticsIdentity = Depends(manager)) -> ProductSetupView:
        try:
            scope = ConnectScope(
                runtime.service.registration(who.tenant_id, body.registration_id).ceiling.database,
                tuple((t.schema_name, t.table) for t in body.tables),
            )
            pending = runtime.service.begin(tenant_id=who.tenant_id, user_id=who.user_id, registration_id=body.registration_id, scope=scope)
        except RegistrationNotFound:
            raise HTTPException(404, "registration_not_found") from None
        except OutsideCeiling:
            raise HTTPException(422, "outside_ceiling") from None
        except ScopeError:
            raise HTTPException(422, "invalid_scope") from None
        except ConnectionConflict as exc:
            raise HTTPException(409, exc.code) from exc
        return view(pending)

    @router.get("/setups/{setup_id}", response_model=ProductSetupView)
    def get_setup(setup_id: str, who: AnalyticsIdentity = Depends(identity)) -> ProductSetupView:
        return view(pending_for(who, setup_id))

    @router.post("/setups/{setup_id}/approve", response_model=ProductSetupView)
    def approve(setup_id: str, body: DigestBody, who: AnalyticsIdentity = Depends(manager)) -> ProductSetupView:
        pending = pending_for(who, setup_id)
        try:
            runtime.service.approve(pending, user_id=who.user_id, digest=body.digest)
        except ApprovalError as exc:
            raise HTTPException(403 if exc.code == "wrong_admin" else 409, exc.code) from exc
        return view(pending)

    @router.post("/setups/{setup_id}/run", response_model=RunView)
    def run(setup_id: str, who: AnalyticsIdentity = Depends(manager)) -> RunView:
        pending = pending_for(who, setup_id)
        try:
            test = grants_connection_test(runtime.session_factory, pending) if runtime.session_factory else None
            result = runtime.service.run(pending, connection_test=test)
        except RegistrationNotFound:
            raise HTTPException(404, "registration_not_found") from None
        except ApprovalError as exc:
            raise HTTPException(409, exc.code) from exc
        except TokenHandoffError as exc:
            raise HTTPException(409, "sign_in_required" if exc.code == "no_token" else "sign_in_expired") from exc
        status = ("active" if test else "pending") if result.ok else ("suspended" if result.code == "connection_test_failed" else "removed")
        return RunView(ok=result.ok, code=result.code, created=list(result.created), leftovers=list(result.leftovers),
                       connection_id=result.connection_id, connection_status=status, failed_checks=list(result.failed_checks))

    @router.post("/setups/{setup_id}/cancel", status_code=204, response_model=None)
    def cancel(setup_id: str, who: AnalyticsIdentity = Depends(manager)) -> None:
        runtime.coordinator.cancel(pending_for(who, setup_id))

    return router
