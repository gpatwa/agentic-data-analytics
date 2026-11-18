"""The two endpoints of the hosted sign-in (CM5b): start it, and receive Snowflake's redirect.

`start` is an ordinary authenticated call (a connection manager, the API key if one is set). `callback` is a browser redirect
from Snowflake, so it cannot carry a bearer token or an API key; it is protected by the single-use `state`, the browser
binding cookie and PKCE instead. The callback never returns a code, verifier or token: it redirects (303) to a fixed path
built from a validated id, or to a fixed error path carrying one short allow-listed code, and says `no-store` and
`no-referrer` so the code in its own URL does not travel on.

Inert (503) until a flow and an identity verifier are configured. Not mounted in `app/main.py` yet: wiring is CM5c.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.connect.oauth import CALLBACK_ERROR_CODES, ConfigNotFound, OAuthFlow, TooManySignIns
from app.security import AuthenticationError, IdentityResolver, OIDCVerifier
from packages.platform_contracts.security import AnalyticsIdentity

MANAGER_GROUP = "connection-manager"  # the same group as app/connect/api.py (a test keeps them equal once both exist)
COOKIE_NAME = "compass_oauth_binding"
COOKIE_PATH = "/api/v2/connect/oauth"
ERROR_PATH = "/connect/oauth-error"


@dataclass
class OAuthRuntime:
    flow: OAuthFlow | None = None
    verifier: OIDCVerifier | None = None
    resolver: IdentityResolver | None = None
    cookie_secure: bool = True  # only a local test over plain http turns this off


class StartBody(BaseModel):
    registration_id: str = Field(min_length=8, max_length=64)
    setup_id: str = Field(min_length=8, max_length=64)


def build_oauth_router(runtime: OAuthRuntime, api_key_dependency) -> APIRouter:
    router = APIRouter(prefix="/api/v2/connect/oauth", tags=["connect"])

    def identity(authorization: str | None = Header(default=None)) -> AnalyticsIdentity:
        if runtime.flow is None or (runtime.verifier is None and runtime.resolver is None):
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

    @router.post("/start", dependencies=[Depends(api_key_dependency)])
    def start(body: StartBody, who: AnalyticsIdentity = Depends(manager)) -> JSONResponse:
        try:
            result = runtime.flow.start(tenant_id=who.tenant_id, user_id=who.user_id, registration_id=body.registration_id, setup_id=body.setup_id)
        except ConfigNotFound:
            raise HTTPException(404, "registration_not_found") from None
        except TooManySignIns:
            raise HTTPException(429, "too_many_sign_ins") from None
        except ValueError:
            raise HTTPException(422, "invalid_setup_id") from None
        response = JSONResponse({"authorize_url": result.authorize_url}, headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE_NAME, result.binding, max_age=result.max_age, path=COOKIE_PATH, secure=runtime.cookie_secure,
                            httponly=True, samesite="lax")
        return response

    @router.get("/callback")
    def callback(request: Request) -> RedirectResponse:
        if runtime.flow is None:
            raise HTTPException(503, "connect_not_configured")
        params = {k: v for k, v in request.query_params.items()}
        outcome = runtime.flow.callback(params, request.cookies.get(COOKIE_NAME))
        if outcome.ok:
            target = outcome.redirect_path
        else:
            code = outcome.error if outcome.error in CALLBACK_ERROR_CODES else "callback_malformed"
            target = f"{ERROR_PATH}?code={code}"
        response = RedirectResponse(target, status_code=303)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)
        return response

    return router
