"""The product-run spike's OAuth flow and G2 runner, on fakes only: a local listener, a fake token endpoint, a fake Snowflake
that keeps state. No account is contacted. These prove the script's own logic (stop rules, scrubbing, cleanup), not Snowflake's
behaviour; that is what G2 itself is for."""

import base64
import hashlib
import json
import pickle
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from app.connect.spike import statements as st
from app.connect.spike.oauth_flow import (
    FlowError,
    HeldToken,
    authorize_url,
    exchange_code,
    new_pkce,
    new_state,
    wait_for_callback,
)
from app.connect.spike.run_g2 import G2Runner, main

TOKEN = "oauth-access-token-SECRET-0123456789"
CODE = "authorization-code-SECRET-9876543210"
SUFFIX = "a1b2c3d4"
STATE = {"suffix": SUFFIX, "port": 8765, "owner_user": "OWNER", "account_locator": "ACCT1"}
N = st.Names.for_run(SUFFIX)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---- PKCE, URL, token holder ---------------------------------------------------------------------------------------


def test_pkce_matches_the_rfc_7636_vector_and_is_fresh_each_time():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == expected
    (v1, c1), (v2, c2) = new_pkce(), new_pkce()
    assert v1 != v2 and c1 != c2 and 43 <= len(v1) <= 128
    assert base64.urlsafe_b64encode(hashlib.sha256(v1.encode()).digest()).rstrip(b"=").decode() == c1
    assert new_state() != new_state()


def test_the_authorize_url_asks_for_the_delegated_role_with_s256_and_no_secret():
    url = authorize_url("https://acct.snowflakecomputing.com/", "client-id", "http://localhost:1/callback", "st", "ch", N.setup_role)
    assert url.startswith("https://acct.snowflakecomputing.com/oauth/authorize?")
    for part in ("response_type=code", "client_id=client-id", "code_challenge=ch", "code_challenge_method=S256", "state=st",
                 f"scope=session%3Arole%3A{N.setup_role}"):
        assert part in url
    assert "secret" not in url.lower()
    with pytest.raises(FlowError):
        authorize_url("http://acct.example", "c", "r", "s", "c", "R")


def test_a_held_token_cannot_leak_and_expires_and_wipes():
    now = [0.0]
    token = HeldToken(TOKEN, 100, clock=lambda: now[0])
    for text in (repr(token), str(token), f"{token}", f"{token!s:>30}", "%s" % token):
        assert TOKEN not in text
    for action in (lambda: pickle.dumps(token), lambda: json.dumps(token)):
        with pytest.raises(TypeError):
            action()
    assert token.reveal() == TOKEN and token.seconds_left() == 100
    now[0] = 101
    with pytest.raises(FlowError):
        token.reveal()
    fresh = HeldToken(TOKEN, 100, clock=lambda: 0.0)
    fresh.wipe()
    with pytest.raises(FlowError):
        fresh.reveal()
    assert HeldToken(TOKEN, 9999, clock=lambda: 0.0).seconds_left() == 300  # capped


# ---- the callback listener -----------------------------------------------------------------------------------------


def _get(port, path):
    deadline = time.monotonic() + 3
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:  # noqa: S310
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except urllib.error.URLError:  # the listener thread may not be up yet
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def _serve(port, state, timeout):
    box = {}

    def target():
        try:
            box["code"] = wait_for_callback(port, state, timeout=timeout)
        except FlowError as exc:
            box["error"] = exc.code

    thread = threading.Thread(target=target)
    thread.start()
    return thread, box


def test_a_valid_callback_returns_the_code_and_logs_nothing(capsys):
    port = free_port()
    thread, box = _serve(port, "good-state", 10)
    assert _get(port, f"/callback?code={CODE}&state=good-state") == 200
    thread.join(5)
    assert box == {"code": CODE}
    captured = capsys.readouterr()
    assert CODE not in captured.out + captured.err


def test_a_wrong_state_a_wrong_path_and_a_missing_code_are_refused_and_do_not_end_the_wait():
    port = free_port()
    thread, box = _serve(port, "good-state", 3)
    assert _get(port, f"/callback?code={CODE}&state=forged") == 400
    assert _get(port, "/other?state=good-state") == 404
    assert _get(port, "/callback?state=good-state") == 400
    thread.join(8)
    assert box == {"error": "callback_timeout"}


def test_an_error_redirect_is_reported_not_swallowed():
    port = free_port()
    thread, box = _serve(port, "s", 10)
    assert _get(port, "/callback?error=access_denied&state=s") == 200
    thread.join(5)
    assert box == {"error": "authorization_refused"}


# ---- the code exchange ---------------------------------------------------------------------------------------------


def test_the_exchange_sends_the_client_id_and_verifier_and_no_secret():
    seen = {}

    def post(url, form):
        seen.update(url=url, form=form)
        return 200, json.dumps({"access_token": TOKEN, "token_type": "Bearer", "expires_in": 600}).encode()

    result = exchange_code("https://a.snowflakecomputing.com/oauth/token-request", "cid", CODE, "http://localhost:1/callback", "ver", post=post)
    assert set(seen["form"]) == {"grant_type", "code", "redirect_uri", "client_id", "code_verifier"}
    assert result.expires_in == 600 and result.has_refresh_token is False and result.token.reveal() == TOKEN
    assert TOKEN not in repr(result)


def test_a_refresh_token_is_noticed():
    post = lambda url, form: (200, json.dumps({"access_token": TOKEN, "refresh_token": "r", "expires_in": 600}).encode())  # noqa: E731
    assert exchange_code("https://a.example/t", "c", "x", "r", "v", post=post).has_refresh_token is True


@pytest.mark.parametrize("status,body", [(400, {"error": "invalid_client", "error_description": "client authentication failed"}), (401, {}), (200, {"nope": 1})])
def test_a_refused_exchange_is_a_flow_error_with_the_vendors_code_and_no_token(status, body):
    with pytest.raises(FlowError) as caught:
        exchange_code("https://a.example/t", "c", "x", "r", "v", post=lambda u, f: (status, json.dumps(body).encode()))
    assert caught.value.code == "token_request_refused" and str(status) in caught.value.detail
    assert TOKEN not in caught.value.detail


def test_the_exchange_refuses_plain_http_and_non_json():
    with pytest.raises(FlowError):
        exchange_code("http://evil.example/t", "c", "x", "r", "v", post=lambda u, f: (200, b"{}"))
    with pytest.raises(FlowError) as caught:
        exchange_code("https://a.example/t", "c", "x", "r", "v", post=lambda u, f: (502, b"<html>"))
    assert caught.value.code == "token_response_not_json"


# ---- the runner, against a fake Snowflake that keeps state ----------------------------------------------------------


class Refusal(Exception):
    errno = 3001
    msg = "SQL access control error: Insufficient privileges"


class Account:
    def __init__(self, existing=(), permissive=(), role=None, fail_drop=None):
        self.objects, self.grants, self.user_roles = set(existing), [], []
        self.permissive, self.role, self.fail_drop = set(permissive), role or N.setup_role, fail_drop
        self.executed, self.closed = [], []


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    @property
    def description(self):
        return self.conn.description

    def execute(self, sql):
        return self.conn.execute(sql)

    def fetchall(self):
        return self.conn._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, account, kind):
        self.account, self.kind = account, kind
        self.description, self._rows = None, []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.account.closed.append(self.kind)

    def _table(self, rows):
        if rows:
            self.description = [(k,) for k in rows[0]]
            self._rows = [tuple(r.values()) for r in rows]

    def execute(self, sql):
        a = self.account
        a.executed.append((self.kind, sql))
        self.description, self._rows = None, []
        if self.kind == "setup":
            return self._setup(sql)
        return self._service(sql)

    def _setup(self, sql):
        a = self.account
        if sql.startswith("SELECT CURRENT_ROLE()"):
            return self._table([{"role": a.role, "usr": "OWNER"}])
        if sql.startswith(("SHOW ROLES", "SHOW USERS")):
            name = sql.split("'")[1]
            return self._table([{"name": name}] if name in a.objects else [])
        if sql.startswith("DROP "):
            if a.fail_drop and a.fail_drop in sql:
                raise Refusal()
            a.objects.discard(sql.split()[-1])
            return None
        if sql.startswith(("CREATE ROLE", "CREATE USER")):
            a.objects.add(sql.split()[2])
            return None
        if sql.startswith("ALTER USER") or sql.startswith("GRANT ROLE") and "TO USER" in sql:
            return None
        if sql.startswith(("GRANT USAGE", "GRANT SELECT")) and "payroll_outside" not in sql and "OWNERSHIP" not in sql:
            import re
            m = re.match(r"GRANT (USAGE|SELECT) ON (WAREHOUSE|DATABASE|SCHEMA|TABLE) (.+) TO ROLE", sql)
            a.grants.append({"privilege": m[1], "granted_on": m[2], "name": m[3], "grant_option": "false"})
            return None
        for key in a.permissive:
            if key in sql:
                return None
        raise Refusal()

    def _service(self, sql):
        a = self.account
        if sql.startswith("SHOW GRANTS TO ROLE"):
            return self._table(a.grants)
        if sql.startswith("DESCRIBE TABLE") or (sql.startswith("SELECT COUNT") and "payroll_outside" not in sql):
            return self._table([{"x": 1}])
        raise Refusal()


def build(account=None, *, exchange=None, wait=None, ttl=300, clock=None, connect_key=None, tick=0.0, secret_in_errors=False):
    account = account or Account()
    now = [0.0]
    clock = clock or (lambda: now[0])
    sunk = []
    calls = {"oauth": 0}

    def default_exchange(url, client_id, code, redirect, verifier):
        from app.connect.spike.oauth_flow import TokenResult
        return TokenResult(HeldToken(TOKEN, ttl, clock=clock), 600.0, False, "Bearer")

    def connect_oauth(token):
        calls["oauth"] += 1
        assert token == TOKEN
        conn = FakeConn(account, "setup")
        if tick:
            real = conn.execute

            def execute(sql):
                now[0] += tick
                return real(sql)

            conn.execute = execute
        return conn

    runner = G2Runner(
        STATE, account_base="https://acct.snowflakecomputing.com", client_id="cid", connect_oauth=connect_oauth,
        connect_key=connect_key or (lambda user, der, role, wh: FakeConn(account, "service")), authorize_sink=sunk.append,
        key_pair=lambda: (b"fake-der", "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA" + "B" * 120),
        exchange=exchange or default_exchange, wait=wait or (lambda port, state, timeout: CODE), callback_timeout=5,
    )
    return runner, account, sunk, calls, now


def test_a_clean_run_passes_every_step_and_leaves_nothing_behind():
    runner, account, sunk, calls, _ = build()
    report = runner.run()
    assert report["all_as_expected"] and report["stopped"] == "" and report["left_for_g3"] == [] and report["unexpected"] == []
    questions = {s["question"] for s in report["steps"]}
    assert {"Q1/Q2/Q3", "Q5", "Q6", "Q8", "Q9", "Q10", "Q10/Q11"} <= questions
    assert sum(s["expected"] == "refuse" and s["ok"] for s in report["steps"]) == 8 + 2  # 8 negatives, 2 service-user refusals
    assert account.objects == set() and account.closed.count("setup") == 1 and account.closed.count("service") == 1
    assert len(sunk) == 1 and f"session%3Arole%3A{N.setup_role}" in sunk[0]
    grants_step = [s for s in report["steps"] if "Connect-mode verifier" in s["name"]]
    assert grants_step and grants_step[0]["ok"] and "verified" in grants_step[0]["detail"]


def test_the_token_the_code_and_the_key_appear_nowhere_in_the_report():
    runner, *_ = build()
    text = json.dumps(runner.run())
    assert TOKEN not in text and CODE not in text and "fake-der" not in text and "MIIBIjAN" not in text


def test_a_negative_test_that_succeeds_stops_the_run_and_cleans_up_through_the_same_session():
    account = Account(permissive={"MANAGE GRANTS"})
    runner, account, *_ = build(account)
    report = runner.run()
    assert report["stopped"] == "negative_test_succeeded:grant MANAGE GRANTS" and not report["all_as_expected"]
    assert any(not s["ok"] and s["name"] == "negative: grant MANAGE GRANTS" for s in report["steps"])
    assert account.objects == set() and report["left_for_g3"] == []
    assert not any(s["name"].startswith("service user") for s in report["steps"])  # it did not carry on


def test_a_drop_that_fails_during_cleanup_is_left_for_g3():
    runner, account, *_ = build(Account(permissive={"MANAGE GRANTS"}, fail_drop="DROP ROLE"))
    report = runner.run()
    assert report["left_for_g3"] == [f"role {N.ro_role}"]
    assert N.ro_role in account.objects


def test_an_object_that_already_exists_is_never_adopted_or_dropped():
    runner, account, *_ = build(Account(existing={N.ro_role}))
    report = runner.run()
    assert report["stopped"] == "object_exists_that_is_not_ours"
    assert account.objects == {N.ro_role} and not any(sql.startswith(("CREATE", "DROP")) for _, sql in account.executed)


def test_the_wrong_role_stops_before_anything_is_created():
    runner, account, *_ = build(Account(role="ACCOUNTADMIN"))
    report = runner.run()
    assert report["stopped"] == "wrong_role" and not any(sql.startswith(("CREATE", "GRANT")) for _, sql in account.executed)


def test_a_refused_code_exchange_is_recorded_for_q6_and_nothing_connects():
    def exchange(*args):
        raise FlowError("token_request_refused", "http 400 invalid_client client authentication failed")

    runner, account, _, calls, _ = build(exchange=exchange)
    report = runner.run()
    assert report["stopped"] == "token_exchange_failed" and calls["oauth"] == 0 and account.executed == []
    step = report["steps"][0]
    assert step["question"] == "Q6" and not step["ok"] and "invalid_client" in step["detail"]


def test_no_callback_is_a_stop_not_a_hang():
    def wait(*args, **kwargs):
        raise FlowError("callback_timeout")

    runner, *_ = build(wait=wait)
    assert runner.run()["stopped"] == "no_authorization_code"


def test_a_token_about_to_expire_stops_the_run_and_still_tries_to_clean_up():
    runner, account, *_ = build(ttl=60, tick=20)
    report = runner.run()
    assert report["stopped"] == "token_expiring" and account.objects == set()


def test_a_service_user_that_cannot_connect_stops_and_cleans_up():
    def broken(user, der, role, wh):
        raise RuntimeError(f"could not connect {TOKEN}")

    runner, account, *_ = build(connect_key=broken)
    report = runner.run()
    assert report["stopped"] == "service_user_connect_failed" and account.objects == set()
    assert TOKEN not in json.dumps(report)


def test_errors_from_the_warehouse_are_scrubbed_of_the_token():
    class Leaky(FakeConn):
        def _setup(self, sql):
            if sql.startswith("CREATE USER"):
                err = Refusal()
                err.msg = f"failed with {TOKEN}"
                raise err
            return super()._setup(sql)

    account = Account()
    runner, *_ = build(account)
    runner.connect_oauth = lambda token: Leaky(account, "setup")
    report = runner.run()
    assert report["stopped"].startswith("per_setup_refused") and TOKEN not in json.dumps(report)


def test_the_token_is_wiped_when_the_run_ends():
    holder = {}

    def exchange(url, client_id, code, redirect, verifier):
        from app.connect.spike.oauth_flow import TokenResult
        holder["token"] = HeldToken(TOKEN, 300)
        return TokenResult(holder["token"], 600.0, False, "Bearer")

    runner, *_ = build(exchange=exchange)
    runner.run()
    with pytest.raises(FlowError):
        holder["token"].reveal()


def test_the_exchange_uses_the_certifi_bundle_and_reports_a_network_failure_as_data(monkeypatch):
    import ssl

    from app.connect.spike import oauth_flow

    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"access_token": TOKEN, "expires_in": 600}).encode()

    def fake_urlopen(request, timeout, context):
        seen["context"] = context
        return Response()

    monkeypatch.setattr(oauth_flow.urllib.request, "urlopen", fake_urlopen)
    assert oauth_flow._post("https://a.example/t", {"a": "b"})[0] == 200
    assert isinstance(seen["context"], ssl.SSLContext) and seen["context"].verify_mode == ssl.CERT_REQUIRED

    def broken(request, timeout, context):
        raise urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] with detail that must not leak")

    monkeypatch.setattr(oauth_flow.urllib.request, "urlopen", broken)
    with pytest.raises(FlowError) as caught:
        oauth_flow._post("https://a.example/t", {})
    assert caught.value.code == "token_endpoint_unreachable" and caught.value.detail == "URLError"


def test_a_network_failure_in_the_exchange_is_a_recorded_q6_step_not_an_unexpected_stop():
    def exchange(*args):
        raise FlowError("token_endpoint_unreachable", "URLError")

    runner, account, _, calls, _ = build(exchange=exchange)
    report = runner.run()
    assert report["stopped"] == "token_exchange_failed" and calls["oauth"] == 0 and account.executed == []
    assert report["steps"][0]["question"] == "Q6" and "token_endpoint_unreachable" in report["steps"][0]["detail"]


def test_an_unexpected_exception_still_records_what_happened_before_it():
    def exchange(*args):
        raise ValueError("surprise")

    runner, *_ = build(exchange=exchange)
    assert runner.run()["stopped"] == "unexpected:ValueError"


# ---- the command line guards ---------------------------------------------------------------------------------------


def _argv(tmp_path, **over):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(STATE))
    base = {"--state": str(state), "--client-id": "cid", "--account-base": "https://acct1.us-east-2.aws.snowflakecomputing.com",
            "--account": "acct1.us-east-2.aws", "--expect-account": "ACCT1", "--url-file": str(tmp_path / "u"), "--out": str(tmp_path / "o")}
    base.update(over)
    return [x for pair in base.items() for x in pair]


def test_the_command_refuses_without_allow_live(tmp_path, capsys):
    assert main(_argv(tmp_path)) == 2
    assert "--allow-live" in capsys.readouterr().err


@pytest.mark.parametrize(
    "over",
    [
        {"--account-base": "http://acct1.snowflakecomputing.com"},
        {"--account-base": "https://acct1.evil.example"},
        {"--account-base": "https://other.snowflakecomputing.com"},
        {"--expect-account": "NOTTHIS"},
    ],
)
def test_the_command_refuses_a_wrong_account_or_host(tmp_path, over, capsys):
    assert main([*_argv(tmp_path, **over), "--allow-live"]) == 2
    assert "refused" in capsys.readouterr().err
