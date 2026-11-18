"""The hosted OAuth sign-in (CM5b): every row of the T24 and T25 threats, the token hand-off, and the two endpoints.

Offline only: a fake token endpoint (an injected function, plus one real local socket for the exchange itself) and a FastAPI
test client. Nothing contacts Snowflake. These prove the flow's own checks, not Snowflake's behaviour; the spike showed the
code exchange works with a public client and PKCE on one account."""

import hashlib
import http.server
import json
import logging
import threading
import urllib.error
import urllib.parse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.connect import oauth
from app.connect.oauth import (
    CALLBACK_ERROR_CODES,
    ConfigNotFound,
    ConfigStore,
    FlowError,
    OAuthClientConfig,
    OAuthFlow,
    StateStore,
    TokenHandoff,
    TokenHandoffError,
    TokenResult,
    TooManySignIns,
    exchange_code,
    new_pkce,
)
from app.connect.oauth_api import COOKIE_NAME, MANAGER_GROUP, OAuthRuntime, build_oauth_router
from app.connect.token import AdminToken
from app.security import AuthenticationError
from packages.platform_contracts.security import AnalyticsIdentity

TOKEN = "snowflake-access-token-SECRET-0123456789"
CODE = "authorization-code-SECRET-9876543210"
HOST = "ei45621.us-east-2.aws.snowflakecomputing.com"
REDIRECT = "https://connect.example.com/api/v2/connect/oauth/callback"
ROLE = "COMPASS_REG_SETUP_A1B2C3D4"
REG = "reg-00000001"
SETUP = "setup-0000000001"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def config(tenant="ta", **over):
    args = dict(tenant_id=tenant, registration_id=REG, host=HOST, client_id="client-id-12345678=", setup_role=ROLE, redirect_uri=REDIRECT)
    args.update(over)
    return OAuthClientConfig(**args)


class Recorder:
    def __init__(self, clock, fail=None):
        self.calls, self.clock, self.fail = [], clock, fail

    def __call__(self, endpoint, client_id, code, redirect_uri, verifier):
        self.calls.append({"endpoint": endpoint, "client_id": client_id, "code": code, "redirect_uri": redirect_uri, "verifier": verifier})
        if self.fail:
            raise FlowError(*self.fail)
        return TokenResult(AdminToken(TOKEN, ttl_seconds=300, clock=self.clock), 600.0, False)


def build(clock=None, fail=None, max_outstanding=5, ttl=600.0):
    clock = clock or Clock()
    configs, states, handoff = ConfigStore(), StateStore(clock), TokenHandoff()
    configs.add(config())
    exchange = Recorder(clock, fail)
    flow = OAuthFlow(configs, states, handoff, exchange=exchange, clock=clock, ttl=ttl, max_outstanding=max_outstanding)
    return flow, states, handoff, exchange, clock, configs


def begin(flow, user="lead", tenant="ta"):
    result = flow.start(tenant_id=tenant, user_id=user, registration_id=REG, setup_id=SETUP)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(result.authorize_url).query)
    return result, {k: v[0] for k, v in query.items()}


# ---- registration config -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "over",
    [
        {"host": "evil.example"}, {"host": "snowflakecomputing.com"}, {"host": ".snowflakecomputing.com"}, {"host": "EI45621.snowflakecomputing.com"},
        {"host": "10.0.0.1"}, {"host": "acme.snowflakecomputing.com."}, {"host": "acme.snowflakecomputing.com.evil.example"},
        {"host": "ac me.snowflakecomputing.com"}, {"host": "acme.snowflakecomputing.com:443"}, {"client_id": "short"}, {"client_id": "has a space 123"},
        {"setup_role": "lowercase"}, {"setup_role": 'QUOTE"D'}, {"setup_role": ""}, {"registration_id": "x"}, {"tenant_id": ""},
        {"redirect_uri": "http://connect.example.com/cb"}, {"redirect_uri": "https://10.0.0.1/cb"}, {"redirect_uri": "https://connect.example.com:8443/cb"},
        {"redirect_uri": "https://connect.example.com/cb?next=evil"}, {"redirect_uri": "https://connect.example.com/cb#x"},
        {"redirect_uri": "https://u:p@connect.example.com/cb"}, {"redirect_uri": "https://connect.example.com"}, {"redirect_uri": "https://localhost/cb"},
    ],
)
def test_a_bad_registration_is_refused(over):
    with pytest.raises(ValueError):
        config(**over)


def test_a_good_registration_gives_the_two_endpoints_on_the_account_host():
    c = config()
    assert c.authorize_endpoint == f"https://{HOST}/oauth/authorize" and c.token_endpoint == f"https://{HOST}/oauth/token-request"


def test_registrations_are_per_tenant_and_not_duplicated():
    store = ConfigStore()
    store.add(config("ta"))
    with pytest.raises(ValueError):
        store.add(config("ta"))
    store.add(config("tb"))
    with pytest.raises(ConfigNotFound):
        store.get("tc", REG)
    assert store.get("ta", REG).tenant_id == "ta"


# ---- starting a sign-in --------------------------------------------------------------------------------------------


def test_the_authorize_url_is_a_public_pkce_request_for_the_delegated_role_with_the_fixed_redirect():
    flow, *_ = build()
    result, q = begin(flow)
    assert result.authorize_url.startswith(f"https://{HOST}/oauth/authorize?")
    assert q["response_type"] == "code" and q["client_id"] == "client-id-12345678=" and q["redirect_uri"] == REDIRECT
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43 and q["scope"] == f"session:role:{ROLE}"
    assert len(q["state"]) >= 40 and set(q) == {"response_type", "client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "scope"}
    assert "secret" not in result.authorize_url.lower() and result.binding not in result.authorize_url and result.max_age == 600


def test_every_start_is_fresh():
    flow, *_ = build()
    (r1, q1), (r2, q2) = begin(flow), begin(flow)
    assert len({q1["state"], q2["state"], r1.binding, r2.binding, q1["code_challenge"], q2["code_challenge"]}) == 6


@pytest.mark.parametrize("setup_id", ["", "short", "has space in id", "x" * 65, "semi;colon-id1", "slash/in/id-1"])
def test_a_malformed_setup_id_is_refused_before_anything_is_stored(setup_id):
    flow, states, *_ = build()
    with pytest.raises(ValueError):
        flow.start(tenant_id="ta", user_id="u", registration_id=REG, setup_id=setup_id)
    assert repr(states) == "StateStore(sessions=0)"


def test_another_tenants_registration_cannot_be_used():
    flow, *_ = build()
    with pytest.raises(ConfigNotFound):
        flow.start(tenant_id="tb", user_id="u", registration_id=REG, setup_id=SETUP)
    with pytest.raises(ConfigNotFound):
        flow.start(tenant_id="ta", user_id="u", registration_id="reg-unknown00", setup_id=SETUP)


def test_too_many_sign_ins_in_progress_are_refused_per_person_and_finished_or_expired_ones_do_not_count():
    clock = Clock()
    flow, states, *_ = build(clock=clock, max_outstanding=2)
    (r1, q1), _second = begin(flow), begin(flow)
    with pytest.raises(TooManySignIns):
        begin(flow)
    begin(flow, user="someone-else")  # the cap is per person
    refused = flow.callback({"code": CODE, "state": q1["state"]}, None)  # no binding: refused, and the slot is not freed
    assert refused.error == "binding_mismatch" and not states.lookup(q1["state"]).used
    with pytest.raises(TooManySignIns):
        begin(flow)
    assert flow.callback({"code": CODE, "state": q1["state"]}, r1.binding).ok  # a finished sign-in frees its slot
    begin(flow)
    clock.now += 601
    begin(flow)
    begin(flow)  # the rest expired


# ---- the callback: the happy path and what it hands on -----------------------------------------------------------------


def test_a_valid_callback_exchanges_once_with_the_servers_verifier_and_hands_the_token_on_once():
    flow, states, handoff, exchange, clock, _ = build()
    result, q = begin(flow)
    out = flow.callback({"code": CODE, "state": q["state"]}, result.binding)
    assert out.ok and out.redirect_path == f"/connect/review/{SETUP}" and out.error is None
    (call,) = exchange.calls
    assert call["endpoint"] == f"https://{HOST}/oauth/token-request" and call["client_id"] == "client-id-12345678=" and call["code"] == CODE
    assert call["redirect_uri"] == REDIRECT
    assert oauth._b64url(hashlib.sha256(call["verifier"].encode()).digest()) == q["code_challenge"]  # the verifier matches the challenge sent
    token = handoff.take("ta", SETUP)
    assert token.reveal() == TOKEN
    with pytest.raises(TokenHandoffError) as again:
        handoff.take("ta", SETUP)
    assert again.value.code == "no_token"


def test_the_verifier_is_wiped_from_the_session_after_its_one_use():
    flow, states, *_ = build()
    result, q = begin(flow)
    session = states.lookup(q["state"])
    assert session.verifier
    flow.callback({"code": CODE, "state": q["state"]}, result.binding)
    assert session.verifier == "" and session.used


def test_request_input_cannot_change_where_the_exchange_goes_or_where_the_browser_is_sent():
    flow, _, _, exchange, _, _ = build()
    result, q = begin(flow)
    out = flow.callback({"code": CODE, "state": q["state"], "redirect_uri": "https://evil.example/cb", "next": "https://evil.example",
                         "setup_id": "setup-evil-0000", "client_id": "other-client-0001"}, result.binding)
    assert out.redirect_path == f"/connect/review/{SETUP}"
    assert exchange.calls[0]["redirect_uri"] == REDIRECT and exchange.calls[0]["client_id"] == "client-id-12345678="


# ---- T24: forged, replayed, expired and cross-site callbacks ----------------------------------------------------------


@pytest.mark.parametrize("state", ["", "short", "x" * 201, "A" * 43, "forged-state-" + "z" * 30])
def test_a_state_we_never_issued_is_refused_and_nothing_is_exchanged(state):
    flow, _, handoff, exchange, _, _ = build()
    begin(flow)
    out = flow.callback({"code": CODE, "state": state}, "any-binding")
    assert not out.ok and out.error == "state_invalid" and exchange.calls == [] and repr(handoff) == "TokenHandoff(held=0)"


def test_a_missing_state_is_refused():
    flow, _, _, exchange, _, _ = build()
    assert flow.callback({"code": CODE}, "b").error == "state_invalid" and exchange.calls == []


def test_a_replayed_callback_is_refused_and_the_code_is_exchanged_only_once():
    flow, _, _, exchange, _, _ = build()
    result, q = begin(flow)
    assert flow.callback({"code": CODE, "state": q["state"]}, result.binding).ok
    again = flow.callback({"code": CODE, "state": q["state"]}, result.binding)
    assert not again.ok and again.error == "state_used" and len(exchange.calls) == 1


def test_an_expired_state_is_refused_and_forgotten():
    clock = Clock()
    flow, states, _, exchange, _, _ = build(clock=clock)
    result, q = begin(flow)
    clock.now += 601
    assert flow.callback({"code": CODE, "state": q["state"]}, result.binding).error == "state_expired"
    assert states.lookup(q["state"]) is None and exchange.calls == []


@pytest.mark.parametrize("binding", [None, "", "not-the-binding"])
def test_a_callback_without_the_browsers_binding_is_refused_and_does_not_burn_the_real_sign_in(binding):
    flow, states, _, exchange, _, _ = build()
    result, q = begin(flow)
    out = flow.callback({"code": CODE, "state": q["state"]}, binding)
    assert not out.ok and out.error == "binding_mismatch" and exchange.calls == []
    assert states.lookup(q["state"]).used is False
    assert flow.callback({"code": CODE, "state": q["state"]}, result.binding).ok  # the legitimate admin can still finish


def test_login_csrf_an_attackers_state_in_the_victims_browser_is_refused():
    """The attacker starts their own sign-in and tricks the victim's browser into delivering the callback."""
    flow, _, handoff, exchange, _, _ = build()
    attacker, attacker_q = begin(flow, user="attacker")
    victim, _ = begin(flow, user="victim")
    out = flow.callback({"code": "attackers-code-0000", "state": attacker_q["state"]}, victim.binding)
    assert out.error == "binding_mismatch" and exchange.calls == [] and repr(handoff) == "TokenHandoff(held=0)"


def test_a_refusal_from_the_vendor_is_recorded_without_an_exchange():
    flow, states, _, exchange, _, _ = build()
    result, q = begin(flow)
    out = flow.callback({"error": "access_denied", "state": q["state"]}, result.binding)
    assert out.error == "authorization_refused" and out.detail == "access_denied" and exchange.calls == []
    assert states.lookup(q["state"]).used and states.lookup(q["state"]).verifier == ""


def test_a_hostile_error_value_is_sanitised_in_the_detail():
    flow, *_ = build()
    result, q = begin(flow)
    out = flow.callback({"error": "<script>alert(1)</script>\n" + "x" * 200, "state": q["state"]}, result.binding)
    assert out.error == "authorization_refused" and set(out.detail) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_. -") and len(out.detail) <= 64


def test_a_callback_with_no_code_is_refused():
    flow, _, _, exchange, _, _ = build()
    result, q = begin(flow)
    assert flow.callback({"state": q["state"]}, result.binding).error == "missing_code" and exchange.calls == []


@pytest.mark.parametrize("params", [{"state": "s" * 40, "code": "c" * 2049}, {"state": 5, "code": "c"}, {"state": "s" * 40, "extra": ["a"]}])
def test_malformed_parameters_are_refused(params):
    flow, _, _, exchange, _, _ = build()
    assert flow.callback(params, "b").error == "callback_malformed" and exchange.calls == []


def test_a_failed_exchange_is_a_code_with_no_token_and_the_state_is_spent():
    flow, _, handoff, exchange, _, _ = build(fail=("token_request_refused", "http 400 invalid_client"))
    result, q = begin(flow)
    out = flow.callback({"code": CODE, "state": q["state"]}, result.binding)
    assert out.error == "token_exchange_failed" and "invalid_client" in out.detail and repr(handoff) == "TokenHandoff(held=0)"
    assert flow.callback({"code": CODE, "state": q["state"]}, result.binding).error == "state_used"


# ---- the code exchange itself ---------------------------------------------------------------------------------------


def test_the_exchange_sends_the_client_id_and_verifier_only_and_caps_the_token_lifetime():
    seen = {}
    clock = Clock()

    def post(url, form):
        seen.update(url=url, form=form)
        return 200, json.dumps({"access_token": TOKEN, "expires_in": 3600}).encode()

    result = exchange_code("https://a.snowflakecomputing.com/oauth/token-request", "cid-0000000", CODE, REDIRECT, "ver", post=post, clock=clock)
    assert set(seen["form"]) == {"grant_type", "code", "redirect_uri", "client_id", "code_verifier"}
    assert result.has_refresh_token is False and result.token.reveal() == TOKEN
    clock.now += 301
    assert not result.token.usable  # capped at the token class's own maximum, whatever the vendor said


def test_a_refresh_token_is_noticed():
    post = lambda url, form: (200, json.dumps({"access_token": TOKEN, "refresh_token": "r", "expires_in": 600}).encode())  # noqa: E731
    assert exchange_code("https://a.snowflakecomputing.com/t", "c", "x", "r", "v", post=post).has_refresh_token is True


@pytest.mark.parametrize(
    "status,body",
    [(400, {"error": "invalid_client", "error_description": "details with " + TOKEN}), (401, {}), (200, {"nope": 1}), (200, ["list"]), (200, {"access_token": 5})],
)
def test_a_refused_exchange_names_the_status_and_vendor_code_and_never_the_description_or_a_token(status, body):
    with pytest.raises(FlowError) as caught:
        exchange_code("https://a.snowflakecomputing.com/t", "c", "x", "r", "v", post=lambda u, f: (status, json.dumps(body).encode()))
    assert caught.value.code == "token_request_refused" and TOKEN not in caught.value.detail and "details" not in caught.value.detail
    assert str(status) in caught.value.detail


def test_the_exchange_refuses_plain_http_off_localhost_and_non_json():
    with pytest.raises(FlowError):
        exchange_code("http://evil.example/t", "c", "x", "r", "v", post=lambda u, f: (200, b"{}"))
    with pytest.raises(FlowError) as caught:
        exchange_code("https://a.snowflakecomputing.com/t", "c", "x", "r", "v", post=lambda u, f: (502, b"<html>"))
    assert caught.value.code == "token_response_not_json"


def test_the_real_post_uses_a_verifying_tls_context_and_reports_a_network_failure_as_data(monkeypatch):
    import ssl

    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def ok(request, timeout, context):
        seen["context"] = context
        return Response()

    monkeypatch.setattr(oauth.urllib.request, "urlopen", ok)
    assert oauth._post("https://a.example/t", {"a": "b"})[0] == 200
    assert isinstance(seen["context"], ssl.SSLContext) and seen["context"].verify_mode == ssl.CERT_REQUIRED

    def broken(request, timeout, context):
        raise urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] detail that must not leak")

    monkeypatch.setattr(oauth.urllib.request, "urlopen", broken)
    with pytest.raises(FlowError) as caught:
        oauth._post("https://a.example/t", {})
    assert caught.value.code == "token_endpoint_unreachable" and caught.value.detail == "URLError"


def test_a_real_socket_exchange_sends_a_public_client_request():
    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            received.update(urllib.parse.parse_qs(body))
            payload = json.dumps({"access_token": TOKEN, "expires_in": 600}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        verifier, challenge = new_pkce()
        result = exchange_code(f"http://127.0.0.1:{server.server_port}/oauth/token-request", "cid-0000000", CODE, REDIRECT, verifier)
    finally:
        server.shutdown()
        server.server_close()
    assert result.token.reveal() == TOKEN
    assert set(received) == {"grant_type", "code", "redirect_uri", "client_id", "code_verifier"} and received["code_verifier"] == [verifier]
    assert "client_secret" not in received and oauth._b64url(hashlib.sha256(verifier.encode()).digest()) == challenge


# ---- the token hand-off --------------------------------------------------------------------------------------------


def test_the_handoff_is_tenant_scoped_single_use_and_expires():
    clock = Clock()
    handoff = TokenHandoff()
    handoff.put("ta", SETUP, AdminToken(TOKEN, ttl_seconds=60, clock=clock))
    with pytest.raises(TokenHandoffError):
        handoff.take("tb", SETUP)
    clock.now += 61
    with pytest.raises(TokenHandoffError) as expired:
        handoff.take("ta", SETUP)
    assert expired.value.code == "token_expired" and repr(handoff) == "TokenHandoff(held=0)"


def test_a_second_put_wipes_the_first_token():
    handoff = TokenHandoff()
    first = AdminToken(TOKEN, ttl_seconds=60)
    handoff.put("ta", SETUP, first)
    handoff.put("ta", SETUP, AdminToken("second-token-0123456789", ttl_seconds=60))
    assert not first.usable and handoff.take("ta", SETUP).reveal() == "second-token-0123456789"


# ---- nothing leaks ---------------------------------------------------------------------------------------------------


def test_the_code_verifier_binding_and_token_appear_nowhere_they_should_not(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    flow, states, handoff, exchange, clock, configs = build()
    result, q = begin(flow)
    out = flow.callback({"code": CODE, "state": q["state"]}, result.binding)
    verifier = exchange.calls[0]["verifier"]
    visible = [result.authorize_url, repr(result), repr(out), repr(flow.__dict__.keys()), repr(states), repr(handoff), repr(configs.get("ta", REG)),
               repr(states.lookup(q["state"])), out.redirect_path, caplog.text, *capsys.readouterr()]
    haystack = " ".join(visible)
    for secret in (TOKEN, CODE, verifier):
        assert secret not in haystack
    assert result.binding not in result.authorize_url and result.binding not in repr(result.authorize_url)
    assert handoff.take("ta", SETUP).reveal() == TOKEN


def test_the_failure_paths_leak_nothing_either():
    flow, *_ = build(fail=("token_request_refused", "http 400 invalid_client"))
    result, q = begin(flow)
    out = flow.callback({"code": CODE, "state": q["state"]}, result.binding)
    assert CODE not in repr(out) and TOKEN not in repr(out)


# ---- the endpoints ---------------------------------------------------------------------------------------------------

IDENTITIES = {
    "mgr": AnalyticsIdentity(tenant_id="ta", user_id="lead", groups=[MANAGER_GROUP]),
    "usr": AnalyticsIdentity(tenant_id="ta", user_id="analyst", groups=[]),
    "mgr-b": AnalyticsIdentity(tenant_id="tb", user_id="lead-b", groups=[MANAGER_GROUP]),
}


class Verifier:
    def verify(self, token):
        try:
            return IDENTITIES[token]
        except KeyError as exc:
            raise AuthenticationError("bad") from exc


def make_app(configured=True, secure=False, api_key_ok=True, **flow_kwargs):
    flow, states, handoff, exchange, clock, configs = build(**flow_kwargs)
    runtime = OAuthRuntime(flow=flow if configured else None, verifier=Verifier() if configured else None, cookie_secure=secure)

    async def key():
        if not api_key_ok:
            raise HTTPException(401, "Invalid analytics API key")

    app = FastAPI()
    app.include_router(build_oauth_router(runtime, key))
    return TestClient(app), flow, handoff, exchange


def auth(token="mgr"):
    return {"Authorization": f"Bearer {token}"}


def start_api(client, token="mgr", **body):
    return client.post("/api/v2/connect/oauth/start", json={"registration_id": REG, "setup_id": SETUP, **body}, headers=auth(token))


def test_both_endpoints_are_503_until_configured():
    client, *_ = make_app(configured=False)
    assert start_api(client).status_code == 503
    assert client.get("/api/v2/connect/oauth/callback?state=x", follow_redirects=False).status_code == 503


def test_start_needs_a_bearer_a_manager_and_a_known_registration():
    client, *_ = make_app()
    assert client.post("/api/v2/connect/oauth/start", json={"registration_id": REG, "setup_id": SETUP}).status_code == 401
    assert start_api(client, "nope").status_code == 401
    assert start_api(client, "usr").status_code == 403
    assert start_api(client, registration_id="reg-unknown00").status_code == 404
    assert start_api(client, "mgr-b").status_code == 404  # another tenant's registration is simply not there
    assert start_api(client, setup_id="bad id").status_code == 422


def test_start_returns_only_the_url_and_sets_a_locked_down_cookie_for_the_binding():
    client, *_ = make_app(secure=True)
    response = start_api(client)
    assert response.status_code == 200 and set(response.json()) == {"authorize_url"} and response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    binding = response.cookies.get(COOKIE_NAME)
    assert binding and binding not in response.text and binding not in response.json()["authorize_url"]
    for part in ("HttpOnly", "SameSite=lax", "Path=/api/v2/connect/oauth", "Max-Age=600", "Secure"):
        assert part in cookie


def test_start_is_rate_limited_per_person():
    client, *_ = make_app(max_outstanding=2)
    assert start_api(client).status_code == 200 and start_api(client).status_code == 200
    third = start_api(client)
    assert third.status_code == 429 and third.json()["detail"] == "too_many_sign_ins"


def _signed_in(client):
    start = start_api(client)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(start.json()["authorize_url"]).query)
    return query["state"][0], start.cookies.get(COOKIE_NAME)


def callback(client, state, binding, **extra):
    client.cookies.clear()  # the test client keeps cookies like a browser; each case says exactly which cookie it sends
    headers = {"Cookie": f"{COOKIE_NAME}={binding}"} if binding else {}
    query = urllib.parse.urlencode({"code": CODE, "state": state, **extra})
    return client.get(f"/api/v2/connect/oauth/callback?{query}", headers=headers, follow_redirects=False)


def test_a_good_callback_redirects_to_a_fixed_path_and_leaks_nothing_to_the_browser():
    client, _, handoff, exchange = make_app()
    state, binding = _signed_in(client)
    response = callback(client, state, binding, next="https://evil.example", redirect_uri="https://evil.example/cb")
    assert response.status_code == 303 and response.headers["location"] == f"/connect/review/{SETUP}"
    assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"
    assert COOKIE_NAME in response.headers["set-cookie"] and "Max-Age=0" in response.headers["set-cookie"]
    everything = response.text + str(dict(response.headers))
    for secret in (CODE, TOKEN, exchange.calls[0]["verifier"], binding):
        assert secret not in everything.replace(f"{COOKIE_NAME}=", "") or secret == binding and binding not in response.text
    assert "evil.example" not in everything and handoff.take("ta", SETUP).reveal() == TOKEN


@pytest.mark.parametrize("variant", ["forged", "no_cookie", "wrong_cookie", "replay"])
def test_a_bad_callback_redirects_to_the_error_path_with_one_short_code(variant):
    client, _, handoff, exchange = make_app()
    state, binding = _signed_in(client)
    if variant == "forged":
        response, code = callback(client, "forged-state-" + "z" * 30, binding), "state_invalid"
    elif variant == "no_cookie":
        response, code = callback(client, state, None), "binding_mismatch"
    elif variant == "wrong_cookie":
        response, code = callback(client, state, "another-browsers-binding"), "binding_mismatch"
    else:
        assert callback(client, state, binding).status_code == 303
        response, code = callback(client, state, binding), "state_used"
    assert response.status_code == 303 and response.headers["location"] == f"/connect/oauth-error?code={code}" and code in CALLBACK_ERROR_CODES
    assert CODE not in response.text + str(dict(response.headers)) and (variant == "replay" or exchange.calls == [])


def test_the_callback_needs_no_api_key_but_start_does():
    client, *_ = make_app(api_key_ok=False)
    assert start_api(client).status_code == 401  # start sits behind the API key dependency
    assert client.get("/api/v2/connect/oauth/callback?state=" + "z" * 40, follow_redirects=False).status_code == 303  # the browser redirect cannot send one


def test_a_stub_error_outside_the_allow_list_is_shown_as_malformed():
    from app.connect.oauth import CallbackResult

    client, flow, *_ = make_app()
    flow.callback = lambda params, binding: CallbackResult(False, "", "something_surprising", "internal detail " + TOKEN)
    response = client.get("/api/v2/connect/oauth/callback?state=x", follow_redirects=False)
    assert response.headers["location"] == "/connect/oauth-error?code=callback_malformed" and TOKEN not in str(dict(response.headers))


def test_the_default_runtime_marks_the_cookie_secure():
    assert OAuthRuntime().cookie_secure is True


def test_the_manager_group_matches_the_connect_api_once_both_exist():
    api = pytest.importorskip("app.connect.api")
    assert api.MANAGER_GROUP == MANAGER_GROUP
