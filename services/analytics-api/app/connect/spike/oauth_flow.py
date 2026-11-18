"""The authorization-code-with-PKCE flow for the product-run spike, with no client secret (docs/design/CONNECT_PRODUCT_RUN_SPIKE_PLAN.md).

Local and throwaway: a one-request listener on 127.0.0.1 receives the redirect, the code is exchanged at the token endpoint
with the PKCE verifier and the client id only, and the access token lives in an object that cannot be printed. Nothing is
logged. The product's own flow (CM5b) is designed separately; this exists to learn what Snowflake actually does.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

MAX_TOKEN_SECONDS = 300.0


class FlowError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code, self.detail = code, detail


class HeldToken:
    """An access token that cannot be printed, formatted, pickled or copied, and is wiped on demand."""

    __slots__ = ("_value", "_expires_at", "_clock")

    def __init__(self, value: str, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        if not value:
            raise ValueError("a token is required")
        self._value: str | None = value
        self._clock = clock
        self._expires_at = clock() + min(float(ttl_seconds), MAX_TOKEN_SECONDS)

    def reveal(self) -> str:
        if self._value is None or self._clock() >= self._expires_at:
            self.wipe()
            raise FlowError("token_unavailable")
        return self._value

    def seconds_left(self) -> float:
        return max(0.0, self._expires_at - self._clock()) if self._value is not None else 0.0

    def wipe(self) -> None:
        self._value = None

    def __repr__(self) -> str:
        return "HeldToken(redacted)"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return "HeldToken(redacted)"

    def __reduce__(self):
        raise TypeError("a token cannot be serialised")

    def __copy__(self):
        raise TypeError("a token cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("a token cannot be copied")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge). The verifier is 43 to 128 characters of the unreserved set (RFC 7636)."""
    verifier = _b64url(secrets.token_bytes(48))
    return verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(account_base: str, client_id: str, redirect_uri: str, state: str, challenge: str, role: str) -> str:
    if not account_base.startswith("https://"):
        raise FlowError("account_base_must_be_https")
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": f"session:role:{role}",
        }
    )
    return f"{account_base.rstrip('/')}/oauth/authorize?{query}"


def wait_for_callback(port: int, expected_state: str, *, timeout: float = 300.0, host: str = "127.0.0.1") -> str:
    """Serve one valid callback on `host:port` and return its code. A wrong state or a missing code is refused."""
    outcome: dict[str, Any] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # nothing is logged: a URL here carries a code
            pass

        def _reply(self, status: int, text: str) -> None:
            body = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                return self._reply(404, "not found")
            params = urllib.parse.parse_qs(parsed.query)
            state = (params.get("state") or [""])[0]
            if not hmac.compare_digest(state.encode(), expected_state.encode()):
                return self._reply(400, "state mismatch")
            if "error" in params:
                outcome["error"] = (params["error"] or [""])[0]
                self._reply(200, "The sign-in was refused. You can close this tab.")
            elif params.get("code"):
                outcome["code"] = params["code"][0]
                self._reply(200, "Signed in. You can close this tab.")
            else:
                return self._reply(400, "missing code")
            done.set()

    server = HTTPServer((host, port), Handler)
    server.timeout = 0.5
    deadline = time.monotonic() + timeout
    try:
        while not done.is_set() and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "code" in outcome:
        return outcome["code"]
    if "error" in outcome:
        raise FlowError("authorization_refused", outcome["error"])
    raise FlowError("callback_timeout")


@dataclass(frozen=True)
class TokenResult:
    token: HeldToken
    expires_in: float
    has_refresh_token: bool
    token_type: str


def _tls_context() -> ssl.SSLContext:
    """Some Python builds have no usable default CA bundle (found live: the default context rejected Snowflake's chain)."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _post(url: str, form: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30, context=_tls_context()) as response:  # noqa: S310 - the account's https endpoint
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:  # a network or certificate failure is data, never an unrecorded crash
        raise FlowError("token_endpoint_unreachable", type(exc).__name__) from None


def exchange_code(
    token_endpoint: str, client_id: str, code: str, redirect_uri: str, verifier: str,
    *, post: Callable[[str, dict[str, str]], tuple[int, bytes]] = _post,
) -> TokenResult:
    """Public client: the client id and the PKCE verifier only, no secret. The error body of a refusal is returned as a code."""
    if not token_endpoint.startswith(("https://", "http://127.0.0.1")):
        raise FlowError("token_endpoint_must_be_https")
    status, body = post(
        token_endpoint,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "client_id": client_id, "code_verifier": verifier},
    )
    try:
        data = json.loads(body.decode() or "{}")
    except ValueError:
        raise FlowError("token_response_not_json", f"http {status}") from None
    if status != 200 or "access_token" not in data:
        raise FlowError("token_request_refused", f"http {status} {data.get('error', '')} {str(data.get('error_description', ''))[:160]}".strip())
    expires = float(data.get("expires_in", 0) or 0)
    return TokenResult(HeldToken(data["access_token"], expires or MAX_TOKEN_SECONDS), expires, "refresh_token" in data, str(data.get("token_type", "")))
