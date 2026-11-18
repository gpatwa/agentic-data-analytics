"""A local harness: the real app, the real routers and the real sign-in code, on this machine, for the live end-to-end check.

It builds the same pieces production would (stores, approval gate, sign-in flow, product-run service, setup worker, connection
test) with in-memory stores, a throwaway static-token identity verifier, the built web UI served from the same origin (so the
callback, its cookie and its redirect behave as behind the product's proxy), and real Snowflake sessions. It binds to `127.0.0.1`
only, refuses to start without `--allow-live` and a matching `--expect-account`, and refuses a registration for any other
account. A leak scanner watches every log line, response and request address for the secrets as they come into existence and
fails the check if one appears.

Nothing here is deployed or reachable from another machine. The identity verifier accepts one random token printed at start.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.connect.admin_sql import new_key_pair
from app.connect.api import ConnectRuntime, build_connect_router
from app.connect.approval import ApprovalGate
from app.connect.e2e import statements as st
from app.connect.e2e.leak import LeakScanner, ScanningLogHandler
from app.connect.gateway import snowflake_session_for
from app.connect.oauth import ConfigStore, OAuthFlow, StateStore, TokenHandoff, TokenResult, exchange_code
from app.connect.oauth_api import MANAGER_GROUP, OAuthRuntime, build_oauth_router, install_log_redaction
from app.connect.product_run import ProductRunService, RegistrationStore, snowflake_oauth_connect
from app.connect.product_run_api import ProductRunRuntime, build_product_run_router
from app.connect.store import ConnectionStore, TenantSecretStore
from app.connect.targets import Resolver, system_resolver
from app.connect.worker import SetupCoordinator
from app.security import AuthenticationError
from app.security.audit import AuditChain
from packages.platform_contracts.security import AnalyticsIdentity

TENANT = "tenant-e2e"
LOCAL_CLIENTS = frozenset({"127.0.0.1", "::1"})  # a request with no client address is not local either
_LOCATOR = re.compile(r"^[A-Za-z0-9_]{3,40}$")


@dataclass(frozen=True)
class HarnessConfig:
    suffix: str
    port: int
    ui_token: str
    expect_account: str
    egress: tuple[str, ...]
    static_dir: Path | None = None


class _Verifier:
    def __init__(self, token: str) -> None:
        self._token = token

    def verify(self, bearer: str) -> AnalyticsIdentity:
        if not secrets.compare_digest(bearer.encode(), self._token.encode()):
            raise AuthenticationError("bad token")
        return AnalyticsIdentity(tenant_id=TENANT, user_id="e2e-admin", groups=[MANAGER_GROUP])


class _ScanningMiddleware(BaseHTTPMiddleware):
    """Looks at every request address and every response (headers and body) for a secret."""

    def __init__(self, app: Any, scanner: LeakScanner) -> None:
        super().__init__(app)
        self._scanner = scanner

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        self._scanner.scan("request-url", str(request.url), expected=("authorization_code",))  # it arrives there by design
        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        self._scanner.scan("response-body", body.decode("utf-8", "replace"))
        self._scanner.scan("response-headers", " ".join(f"{k}: {v}" for k, v in response.headers.items() if k.lower() != "set-cookie"))
        return Response(content=body, status_code=response.status_code, headers=dict(response.headers), media_type=response.media_type)


@dataclass
class Harness:
    app: FastAPI
    scanner: LeakScanner
    chain: AuditChain
    store: ConnectionStore
    secrets: TenantSecretStore
    handoff: TokenHandoff
    service: ProductRunService
    config: HarnessConfig

    def attach_logging(self) -> None:
        """Redact the callback's code from the access log, then watch every log record. Call this **after** the server has
        configured its own logging: uvicorn's setup removes handlers from its loggers, which would blind the scanner."""
        install_log_redaction(("uvicorn.access",))
        logging.disable(logging.NOTSET)  # a scanner that is told nothing reports "clean": never let a global switch mute it
        for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
            target = logging.getLogger(name)
            target.disabled = False
            if not any(isinstance(h, ScanningLogHandler) and h.scanner is self.scanner for h in target.handlers):
                target.addHandler(ScanningLogHandler(self.scanner))
            target.setLevel(logging.DEBUG)

    def report(self) -> dict[str, Any]:
        records = self.store.list(TENANT)
        return {
            "leaks": self.scanner.summary(),
            "audit_chain_verifies": self.chain.verify(),
            "audit_events": [{"type": e.event_type, "actor": e.actor_id} for e in self.chain.events],
            "connections": [{"id": r.connection_id, "status": r.status, "tables": list(r.allowed_tables)} for r in records],
        }

    def audit_text(self) -> str:
        return json.dumps([e.model_dump(mode="json") for e in self.chain.events])


def build_app(
    config: HarnessConfig,
    *,
    connector: Any | None = None,
    exchange: Callable[..., TokenResult] | None = None,
    session_factory: Callable[[Any], Any] | None = None,
    resolver: Resolver = system_resolver,
    attach_logging: bool = True,
) -> Harness:
    scanner = LeakScanner()
    store, vault, chain, gate = ConnectionStore(), TenantSecretStore(), AuditChain(), ApprovalGate()
    handoff, configs, registrations, states = TokenHandoff(), ConfigStore(), RegistrationStore(), StateStore()

    def tracked_key_pair() -> tuple[bytes, str]:
        der, public = new_key_pair()
        scanner.watch("service_private_key_der", der)
        scanner.watch("service_private_key_b64", base64.b64encode(der).decode("ascii"))
        return der, public

    real_exchange = exchange or exchange_code

    def tracked_exchange(endpoint: str, client_id: str, code: str, redirect_uri: str, verifier: str) -> TokenResult:
        scanner.watch("authorization_code", code)
        scanner.watch("pkce_verifier", verifier)
        result = real_exchange(endpoint, client_id, code, redirect_uri, verifier)
        scanner.watch("oauth_access_token", result.token.reveal())
        return result

    coordinator = SetupCoordinator(store, vault, chain, gate, key_pair=tracked_key_pair)
    service = ProductRunService(
        registrations, configs, coordinator, gate, handoff,
        connect_factory=lambda host: snowflake_oauth_connect(host, connector=connector), key_pair=tracked_key_pair,
    )
    real_register = service.register

    def guarded_register(**kwargs: Any) -> Any:
        if str(kwargs.get("account_locator", "")).upper() != config.expect_account.upper():
            raise ValueError("this check was started for a different account")
        return real_register(**kwargs)

    service.register = guarded_register  # type: ignore[method-assign]

    local = st.redirect(config.port)
    flow = OAuthFlow(configs, states, handoff, exchange=tracked_exchange)
    verifier = _Verifier(config.ui_token)

    async def no_key() -> None:
        return None

    app = FastAPI(title="product-run end-to-end harness", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(_ScanningMiddleware, scanner=scanner)
    app.include_router(build_oauth_router(OAuthRuntime(flow=flow, verifier=verifier, cookie_secure=False), no_key))
    # The screens ask the script-path router for capabilities and the connection list before anything else; the live run found
    # the harness did not mount it (404), so the product-run screens could not open.
    app.include_router(build_connect_router(
        ConnectRuntime(coordinator=coordinator, store=store, verifier=verifier, allowed_networks=config.egress,
                       session_factory=session_factory or (lambda record: snowflake_session_for(record, vault, connector=connector))),
        no_key,
    ))
    app.include_router(build_product_run_router(
        ProductRunRuntime(
            service=service, coordinator=coordinator, verifier=verifier, redirect_uri=local, egress_networks=config.egress, dns=resolver,
            session_factory=session_factory or (lambda record: snowflake_session_for(record, vault, connector=connector)),
            allow_local_dev_redirect=True, suffix_factory=lambda: config.suffix,
        ),
        no_key,
    ))

    harness = Harness(app, scanner, chain, store, vault, handoff, service, config)
    if attach_logging:
        harness.attach_logging()

    @app.get("/__harness/report")
    def report(request: Request) -> dict[str, Any]:
        if (request.client.host if request.client else "") not in LOCAL_CLIENTS:
            raise HTTPException(403, "local_only")
        return harness.report()

    static = config.static_dir
    if static is not None and (static / "index.html").exists():
        if (static / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> Response:
            if path.startswith(("api/", "__harness")):
                return JSONResponse({"detail": "not_found"}, status_code=404)
            candidate = (static / path).resolve()
            if path and candidate.is_file() and static.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(static / "index.html")  # the single-page app handles /connect/review/<id> itself

    return harness


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--expect-account", required=True, help="the account locator (CURRENT_ACCOUNT()) this check is for")
    parser.add_argument("--suffix", required=True, help="the run suffix the reviewed statements were generated with")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--egress", action="append", required=True, help="Compass's own public address, CIDR (repeatable)")
    parser.add_argument("--static-dir", default=None)
    parser.add_argument("--report", default=None, help="write the final report here (names and outcomes only)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.allow_live:
        print("refused: pass --allow-live (only after the owner approved the gate)", file=sys.stderr)
        return 2
    if not _LOCATOR.match(args.expect_account) or not re.fullmatch(r"[0-9a-f]{8}", args.suffix) or not 1024 <= args.port <= 65535:
        print("refused: check --expect-account, --suffix and --port", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[5]
    static = Path(args.static_dir) if args.static_dir else root / "apps/analytics-web/dist"
    config = HarnessConfig(args.suffix, args.port, secrets.token_urlsafe(18), args.expect_account, tuple(args.egress), static if static.is_dir() else None)
    harness = build_app(config, attach_logging=False)
    import uvicorn  # imported late: the guards above run without it

    server_config = uvicorn.Config(harness.app, host="127.0.0.1", port=config.port, log_level="info")  # configures uvicorn's logging
    harness.attach_logging()  # only now: uvicorn's setup would have removed the handlers

    print(f"product-run harness on http://localhost:{config.port} (127.0.0.1 only); UI access token for this run: {config.ui_token}", flush=True)
    try:
        uvicorn.Server(server_config).run()
    finally:
        summary = harness.report()
        summary["audit_text_clean"] = not harness.scanner.labels_in(harness.audit_text())
        if args.report:
            Path(args.report).write_text(json.dumps(summary, indent=2))
        print(json.dumps({"leaks_clean": summary["leaks"]["clean"], "audit_chain_verifies": summary["audit_chain_verifies"]}))
    return 0 if harness.scanner.clean else 1


if __name__ == "__main__":
    sys.exit(main())
