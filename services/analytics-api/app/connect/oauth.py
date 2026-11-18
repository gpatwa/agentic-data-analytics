"""The hosted OAuth sign-in for product-run setup (CM5b): authorization code with PKCE, a public client, no client secret.

The admin signs in on Snowflake's own page; Compass never sees a password. This module builds the authorize URL, remembers
one short-lived, single-use `state` per sign-in (with the PKCE verifier kept **server-side only**), checks the callback, and
exchanges the code for an access token that goes straight into an `AdminToken` held in a single-use hand-off. Nothing here
puts a code, verifier or token in a URL, a response body, a cookie value, a record or a log line.

Threats answered (docs/design/CONNECT_PRODUCT_RUN_DESIGN.md): T24 callback CSRF and code injection (unguessable single-use
state, a browser binding checked in constant time, PKCE S256) and T25 redirect hijack (one fixed https redirect; the
post-callback redirect is built from a validated id, never from request input).

Limits: sessions and the hand-off are in memory, so a restart loses a sign-in in progress (the admin starts again). The
callback URL carries the single-use code in its query string, which a web server's access log may record; the deployment
must not log query strings for the callback path (the code is useless without the verifier, but do not log it). Whether the
token belongs to the account the setup targets (T27) is checked by the setup worker (CM5c), not here. Not verified against
Snowflake: an `https` redirect (the spike used a local one) and the `NETWORK_POLICY` on the integration.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.connect.targets import VENDOR_SUFFIXES
from app.connect.token import MAX_TTL_SECONDS, AdminToken

STATE_TTL_SECONDS = 600.0
MAX_OUTSTANDING = 5
_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_ROLE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_SAFE_DETAIL = re.compile(r"[^A-Za-z0-9_. -]")
MAX_PARAM_CHARS = 2048
CALLBACK_ERROR_CODES = frozenset({
    "state_invalid", "state_expired", "state_used", "binding_mismatch", "authorization_refused", "missing_code",
    "token_exchange_failed", "callback_malformed",
})


class FlowError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code, self.detail = code, detail


class ConfigNotFound(KeyError):
    pass


class TooManySignIns(Exception):
    pass


class TokenHandoffError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---- PKCE and the code exchange ------------------------------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge); the verifier is 64 characters of the unreserved set (RFC 7636)."""
    verifier = _b64url(secrets.token_bytes(48))
    return verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _tls_context() -> ssl.SSLContext:
    """Some Python builds have no usable default CA bundle (found live in the spike); prefer the certifi bundle."""
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


@dataclass(frozen=True)
class TokenResult:
    token: AdminToken
    expires_in: float
    has_refresh_token: bool


def exchange_code(
    token_endpoint: str, client_id: str, code: str, redirect_uri: str, verifier: str,
    *, post: Callable[[str, dict[str, str]], tuple[int, bytes]] = _post, clock: Callable[[], float] = time.monotonic,
) -> TokenResult:
    """Public client: the client id and the PKCE verifier only. A refusal becomes a code; a token is never in an error."""
    if not token_endpoint.startswith(("https://", "http://127.0.0.1")):
        raise FlowError("token_endpoint_must_be_https")
    status, body = post(token_endpoint, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                                         "client_id": client_id, "code_verifier": verifier})
    try:
        data = json.loads(body.decode() or "{}")
    except ValueError:
        raise FlowError("token_response_not_json", f"http {status}") from None
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
        vendor = _SAFE_DETAIL.sub("", str(data.get("error", "")))[:64] if isinstance(data, dict) else ""
        raise FlowError("token_request_refused", f"http {status} {vendor}".strip())
    expires = float(data.get("expires_in", 0) or 0)
    ttl = min(expires, MAX_TTL_SECONDS) if expires > 0 else MAX_TTL_SECONDS
    return TokenResult(AdminToken(data["access_token"], ttl_seconds=ttl, clock=clock), expires, "refresh_token" in data)


# ---- what a customer's registration tells us ----------------------------------------------------------------------


def _https_url(uri: str) -> str:
    """One plain https URL: a hostname (not an IP), the default port, a path, no query, fragment or credentials."""
    if not isinstance(uri, str) or not uri or len(uri) > 200 or any(c in uri for c in " '\"`\\\n\r\t;"):
        raise ValueError("invalid redirect")
    parsed = urllib.parse.urlparse(uri)
    host = parsed.hostname or ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        is_ip = False
    else:
        is_ip = True
    if (is_ip or parsed.scheme != "https" or "." not in host or parsed.port is not None or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not parsed.path.startswith("/") or ".." in parsed.path):
        raise ValueError("the redirect must be a plain https URL with a hostname and a path, no port, query or fragment")
    return uri


@dataclass(frozen=True)
class OAuthClientConfig:
    """One customer's registration: where to sign in, which client, which role. The client id is not a secret."""

    tenant_id: str
    registration_id: str
    host: str
    client_id: str
    setup_role: str
    redirect_uri: str

    def __post_init__(self) -> None:
        if not self.tenant_id or not _ID.match(self.registration_id):
            raise ValueError("tenant_id and a well-formed registration_id are required")
        host = self.host
        suffix = next((s for s in VENDOR_SUFFIXES["snowflake"] if host.endswith(s)), None)
        labels = host.split(".")
        if host != host.lower() or suffix is None or len(host) == len(suffix) or any(not _LABEL.match(x) for x in labels):
            raise ValueError("the host must be a Snowflake account hostname")
        if not 8 <= len(self.client_id) <= 200 or re.search(r"\s", self.client_id):
            raise ValueError("invalid client id")
        if not _ROLE.match(self.setup_role):
            raise ValueError("invalid setup role")
        _https_url(self.redirect_uri)

    @property
    def authorize_endpoint(self) -> str:
        return f"https://{self.host}/oauth/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"https://{self.host}/oauth/token-request"


class ConfigStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._configs: dict[tuple[str, str], OAuthClientConfig] = {}

    def add(self, config: OAuthClientConfig) -> None:
        with self._lock:
            if (config.tenant_id, config.registration_id) in self._configs:
                raise ValueError("registration_id_taken")
            self._configs[(config.tenant_id, config.registration_id)] = config

    def get(self, tenant_id: str, registration_id: str) -> OAuthClientConfig:
        try:
            return self._configs[(tenant_id, registration_id)]
        except KeyError:
            raise ConfigNotFound("registration not found") from None


# ---- one sign-in in progress ---------------------------------------------------------------------------------------


@dataclass
class AuthSession:
    tenant_id: str
    user_id: str
    registration_id: str
    setup_id: str
    verifier: str = field(repr=False)
    binding_hash: str = field(repr=False)
    expires_at: float
    used: bool = False


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class StateStore:
    """Sessions keyed by the hash of an unguessable state; a state is single use and expires."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, AuthSession] = {}

    def __repr__(self) -> str:
        return f"StateStore(sessions={len(self._sessions)})"

    def _purge(self) -> None:
        now = self._clock()
        for key in [k for k, s in self._sessions.items() if s.expires_at <= now]:
            del self._sessions[key]

    def create(self, state: str, session: AuthSession, *, max_outstanding: int) -> None:
        with self._lock:
            self._purge()
            active = sum(1 for s in self._sessions.values() if not s.used and (s.tenant_id, s.user_id) == (session.tenant_id, session.user_id))
            if active >= max_outstanding:
                raise TooManySignIns("too many sign-ins in progress")
            self._sessions[_digest(state)] = session

    def lookup(self, state: str) -> AuthSession | None:
        with self._lock:
            return self._sessions.get(_digest(state))

    def consume(self, state: str) -> None:
        with self._lock:
            session = self._sessions.get(_digest(state))
            if session is not None:
                session.used = True

    def discard(self, state: str) -> None:
        with self._lock:
            self._sessions.pop(_digest(state), None)


class TokenHandoff:
    """The admin token between the callback and the setup run: single use, tenant-scoped, never printable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[tuple[str, str], AdminToken] = {}

    def __repr__(self) -> str:
        return f"TokenHandoff(held={len(self._tokens)})"

    def put(self, tenant_id: str, setup_id: str, token: AdminToken) -> None:
        with self._lock:
            old = self._tokens.pop((tenant_id, setup_id), None)
            if old is not None:
                old.wipe()
            self._tokens[(tenant_id, setup_id)] = token

    def take(self, tenant_id: str, setup_id: str) -> AdminToken:
        with self._lock:
            token = self._tokens.pop((tenant_id, setup_id), None)
        if token is None:
            raise TokenHandoffError("no_token")
        if not token.usable:
            token.wipe()
            raise TokenHandoffError("token_expired")
        return token


@dataclass(frozen=True)
class StartResult:
    authorize_url: str
    binding: str  # goes only into an HttpOnly cookie, never the body
    max_age: int


@dataclass(frozen=True)
class CallbackResult:
    ok: bool
    redirect_path: str
    error: str | None = None
    detail: str = ""  # for the server's audit trail only; never sent to the browser


class OAuthFlow:
    def __init__(
        self,
        configs: ConfigStore,
        states: StateStore,
        handoff: TokenHandoff,
        *,
        exchange: Callable[..., TokenResult] = exchange_code,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = STATE_TTL_SECONDS,
        max_outstanding: int = MAX_OUTSTANDING,
    ) -> None:
        self._configs, self._states, self._handoff = configs, states, handoff
        self._exchange, self._clock, self._ttl, self._max = exchange, clock, ttl, max_outstanding

    def start(self, *, tenant_id: str, user_id: str, registration_id: str, setup_id: str) -> StartResult:
        if not _ID.match(setup_id or ""):
            raise ValueError("invalid setup id")
        config = self._configs.get(tenant_id, registration_id)
        state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier, challenge = new_pkce()
        self._states.create(
            state,
            AuthSession(tenant_id, user_id, registration_id, setup_id, verifier, _digest(binding), self._clock() + self._ttl),
            max_outstanding=self._max,
        )
        query = urllib.parse.urlencode({
            "response_type": "code", "client_id": config.client_id, "redirect_uri": config.redirect_uri, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256", "scope": f"session:role:{config.setup_role}",
        })
        return StartResult(f"{config.authorize_endpoint}?{query}", binding, int(self._ttl))

    def _fail(self, code: str, detail: str = "") -> CallbackResult:
        return CallbackResult(False, "", code, detail)

    def callback(self, params: Mapping[str, str], binding: str | None) -> CallbackResult:
        if not isinstance(params, Mapping) or any(not isinstance(v, str) or len(v) > MAX_PARAM_CHARS for v in params.values()):
            return self._fail("callback_malformed")
        state = params.get("state", "")
        if not 20 <= len(state) <= 200:
            return self._fail("state_invalid")
        session = self._states.lookup(state)
        if session is None:
            return self._fail("state_invalid")
        if session.expires_at <= self._clock():
            self._states.discard(state)
            return self._fail("state_expired")
        if session.used:
            return self._fail("state_used")
        # Checked before the state is consumed, so a forged callback cannot burn a real sign-in.
        if not binding or not hmac.compare_digest(_digest(binding).encode(), session.binding_hash.encode()):
            return self._fail("binding_mismatch")
        self._states.consume(state)
        verifier, session.verifier = session.verifier, ""  # the verifier lives only until this one use
        try:
            if params.get("error"):
                return self._fail("authorization_refused", _SAFE_DETAIL.sub("", params["error"])[:64])
            code = params.get("code", "")
            if not code:
                return self._fail("missing_code")
            config = self._configs.get(session.tenant_id, session.registration_id)
            try:
                result = self._exchange(config.token_endpoint, config.client_id, code, config.redirect_uri, verifier)
            except FlowError as exc:
                return self._fail("token_exchange_failed", f"{exc.code} {exc.detail}".strip())
            self._handoff.put(session.tenant_id, session.setup_id, result.token)
            return CallbackResult(True, f"/connect/review/{session.setup_id}")
        finally:
            del verifier
