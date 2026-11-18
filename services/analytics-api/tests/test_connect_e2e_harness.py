"""The live end-to-end tooling: generated statements, the lockout stop rule, the leak scanner and the local harness, on fakes.

Nothing here contacts an account. The harness runs the real app and the real sign-in code against the same fake Snowflake that
the product-run tests use. These prove the tooling's own logic (guards, scanning, ordering); the live check proves Snowflake's."""

import logging
import re
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.test_connect_product_run import Account, ServiceSession, grant_rows

from app.connect.e2e import statements as st
from app.connect.e2e.harness import TENANT, HarnessConfig, build_app, main
from app.connect.e2e.leak import LeakScanner, ScanningLogHandler
from app.connect.oauth import TokenResult
from app.connect.registration import RegNames
from app.connect.token import AdminToken

ROOT = Path(__file__).resolve().parents[3]
SUFFIX = "a1b2c3d4"
OWNER = "OWNER_USER"
EGRESS = ["35.238.115.103/32"]
LOCATOR = "EI45621"
UI = "ui-token-for-this-run-0123"
TOKEN = "oauth-access-token-SECRET-0123456789"
CODE = "authorization-code-SECRET-9876543210"
HOST = "acme.snowflakecomputing.com"
P = "/api/v2/connect/product-run"


# ---- the generated statements --------------------------------------------------------------------------------------------


def test_every_name_carries_the_suffix_and_the_prefixes_cover_what_the_sweep_looks_for():
    db = st.fixture_db(SUFFIX)
    assert db == "COMPASS_E2E_DB_A1B2C3D4"
    owned = [db, *RegNames.for_registration(SUFFIX).owned]
    assert all(SUFFIX.upper() in n for n in owned) and all(n.startswith(st.PREFIXES) for n in owned)
    sweep = "\n".join(st.SWEEP)
    for prefix in ("COMPASS_E2E", "COMPASS_REG", "COMPASS_RO", "COMPASS_SVC"):
        assert prefix in sweep


@pytest.mark.parametrize("bad", ["", "XYZ", "A1B2C3D4", "a1b2c3d", "a1b2c3d;"])
def test_a_bad_suffix_is_refused(bad):
    with pytest.raises(ValueError):
        st.fixture_db(bad)


def test_the_registration_is_the_products_own_script_with_only_the_declared_local_redirect_deviation():
    from app.connect.registration import generate_registration

    mine = st.registration(SUFFIX, OWNER, 8765, EGRESS)
    theirs = generate_registration(st.ceiling(SUFFIX), suffix=SUFFIX, redirect_uri=st.redirect(8765), egress_networks=EGRESS, setup_users=[OWNER], credit_quota=1,
                                   allow_local_dev_redirect=True)
    assert mine.statements == theirs.statements and mine.digest == theirs.digest
    (integration,) = [s for s in mine.statements if s.startswith("CREATE SECURITY INTEGRATION")]
    assert "OAUTH_REDIRECT_URI = 'http://localhost:8765/api/v2/connect/oauth/callback' OAUTH_ALLOW_NON_TLS_REDIRECT_URI = TRUE" in integration
    production = generate_registration(st.ceiling(SUFFIX), suffix=SUFFIX, redirect_uri="https://connect.example.com/cb", egress_networks=EGRESS, setup_users=[OWNER])
    assert [s for s in mine.statements if "NON_TLS" not in s and "OAUTH_REDIRECT_URI" not in s and "http" not in s] == \
           [s for s in production.statements if "NON_TLS" not in s and "OAUTH_REDIRECT_URI" not in s and "https" not in s]


def test_nothing_can_lock_the_owner_out_or_read_a_secret():
    everything = st.everything(SUFFIX, OWNER, 8765, EGRESS)
    st.assert_no_lockout(everything)
    text = "\n".join(everything).upper()
    for forbidden in ("ALTER ACCOUNT", "ALTER USER", "SET NETWORK_POLICY", "MANAGE GRANTS", "SHOW_OAUTH_CLIENT_SECRETS", "CONFIDENTIAL"):
        assert forbidden not in text
    assert [s for s in everything if "NETWORK_POLICY" in s.upper()] == [s for s in everything if s.startswith("CREATE SECURITY INTEGRATION")]
    assert [s for s in everything if s.upper().startswith("ALTER NETWORK POLICY")] == [st.wrong_address(SUFFIX, EGRESS)[0], st.wrong_address(SUFFIX, EGRESS)[1]]


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER ACCOUNT SET NETWORK_POLICY = COMPASS_REG_NET_A1B2C3D4", "ALTER USER SOMEONE SET NETWORK_POLICY = X", "ALTER USER U UNSET NETWORK_POLICY",
        "alter user u set rsa_public_key = 'x'", "ALTER NETWORK POLICY SOMEONES_POLICY SET ALLOWED_IP_LIST = ('1.2.3.4/32')",
        "ALTER NETWORK POLICY COMPASS_REG_NET_A1B2C3D4 SET BLOCKED_IP_LIST = ('1.2.3.4/32')", "GRANT MANAGE GRANTS ON ACCOUNT TO ROLE X",
        "SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('X')", "ALTER ACCOUNT SET ANYTHING = 1",
    ],
)
def test_the_stop_rule_refuses_anything_that_changes_a_network_policy_it_does_not_own_or_reads_a_secret(statement):
    with pytest.raises(st.LockoutRisk):
        st.assert_no_lockout(["SELECT 1", statement])


def test_the_only_things_the_owners_user_is_ever_given_or_asked_to_lose_is_the_setup_role():
    touching = [s for s in st.everything(SUFFIX, OWNER, 8765, EGRESS) if f'"{OWNER}"' in s]
    role = RegNames.for_registration(SUFFIX).setup_role
    assert touching == [f'GRANT ROLE {role} TO USER "{OWNER}"', f'REVOKE ROLE {role} FROM USER "{OWNER}"']


def test_the_widening_check_gives_the_setup_role_exactly_one_outside_table_and_undoes_it():
    do, undo = st.widen(SUFFIX)
    role = RegNames.for_registration(SUFFIX).setup_role
    assert do == f'GRANT SELECT ON TABLE COMPASS_E2E_DB_A1B2C3D4.DATA."payroll_outside" TO ROLE {role}' and undo.startswith("REVOKE SELECT ON TABLE") and undo.endswith(f"FROM ROLE {role}")
    assert "WITH GRANT OPTION" not in do


def test_the_fixture_is_synthetic_and_the_teardown_drops_only_this_runs_names():
    assert all("INSERT" not in s or "GENERATOR" in s for s in st.fixture(SUFFIX))
    drops = [s for s in st.teardown(SUFFIX, OWNER, EGRESS, 8765) if s.startswith("DROP")]
    owned = {st.fixture_db(SUFFIX), *RegNames.for_registration(SUFFIX).owned}
    assert drops and all(d.split()[-1] in owned for d in drops)
    assert st.teardown(SUFFIX, OWNER, EGRESS, 8765)[-len(st.SWEEP):] == st.SWEEP


def test_the_preflight_asks_for_the_account_user_role_and_client_address_and_is_read_only():
    first = st.preflight()[0]
    assert "CURRENT_ACCOUNT()" in first and "CURRENT_IP_ADDRESS()" in first and all(s.startswith(("SELECT", "SHOW")) for s in st.preflight())


def test_the_reviewable_document_equals_the_generator():
    path = ROOT / "docs/design/CONNECT_PRODUCT_RUN_E2E_STATEMENTS.md"
    assert path.read_text() == st.render_markdown(), "regenerate: python3 -c 'from app.connect.e2e.statements import render_markdown; print(render_markdown(), end=\"\")'"


# ---- the leak scanner ------------------------------------------------------------------------------------------------------


def test_the_scanner_finds_a_secret_and_names_it_by_label_only():
    scanner = LeakScanner()
    scanner.watch("token", TOKEN)
    scanner.watch("short", "tiny")  # too short to watch
    scanner.scan("somewhere", f"a log line mentioning {TOKEN} by accident")
    scanner.scan("elsewhere", "nothing here, and tiny does not count")
    assert not scanner.clean and [(f.secret, f.where) for f in scanner.findings] == [("token", "somewhere")]
    assert TOKEN not in repr(scanner) and TOKEN not in str(scanner.summary())


def test_the_scanner_flags_a_pem_block_and_reports_labels_without_recording():
    scanner = LeakScanner()
    scanner.watch("key", b"binary-key-material-0123456789")
    assert scanner.labels_in("-----BEGIN PRIVATE KEY----- abc") == ["pem_private_key"]
    assert scanner.labels_in("has binary-key-material-0123456789 in it") == ["key"] and scanner.clean


def test_the_log_handler_feeds_the_scanner(caplog):
    scanner = LeakScanner()
    scanner.watch("token", TOKEN)
    logger = logging.getLogger("e2e-test")
    logger.addHandler(ScanningLogHandler(scanner))
    logger.setLevel(logging.DEBUG)
    logger.info("harmless")
    logger.warning("signed in with %s", TOKEN)
    assert [f.secret for f in scanner.findings] == ["token"]


# ---- the harness, end to end on the fake Snowflake ---------------------------------------------------------------------------


class Driver:
    """The oauth connect call the product makes: returns a session over the fake account."""

    def __init__(self, account):
        self.account = account
        self.calls = []

    def connect(self, **kwargs):
        from tests.test_connect_product_run import FakeConn

        self.calls.append(kwargs)
        return FakeConn(self.account)


class WithClient:
    """Presents a chosen client address to the app, as a real connection would (this test client supplies none)."""

    def __init__(self, app, client):
        self.app, self.client = app, client

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope["client"] = self.client
        await self.app(scope, receive, send)


def harness(tmp_path=None, **over):
    account = Account(locator=LOCATOR, setup_grants=grant_rows(st.registration(SUFFIX, OWNER, 8765, EGRESS).statements, LOCATOR))
    account.allowed = set(st.ceiling(SUFFIX).allowed_tables)
    driver = Driver(account)
    config = HarnessConfig(SUFFIX, 8765, UI, LOCATOR, tuple(EGRESS), tmp_path)
    exchange = lambda endpoint, cid, code, redirect, verifier: TokenResult(AdminToken(TOKEN, ttl_seconds=300), 600.0, False)  # noqa: E731
    built = build_app(config, connector=driver, exchange=exchange, session_factory=lambda record: ServiceSession(account), resolver=lambda host: ["54.230.10.4"], **over)
    return built, TestClient(WithClient(built.app, ("127.0.0.1", 50000))), account, driver


def auth(token=UI):
    return {"Authorization": f"Bearer {token}"}


def drive(client, tables=None):
    script = client.post(P + "/registration-script", json={"database": st.fixture_db(SUFFIX), "tables": [{"schema": "DATA", "table": t} for t in st.INSIDE],
                                                           "setup_users": [OWNER]}, headers=auth()).json()
    registered = client.post(P + "/registrations", json={
        "registration_id": f"reg-{script['reg_suffix']}", "reg_suffix": script["reg_suffix"], "host": HOST, "client_id": "client-id-12345678=", "account_locator": LOCATOR,
        "database": st.fixture_db(SUFFIX), "tables": [{"schema": "DATA", "table": t} for t in st.INSIDE], "setup_users": [OWNER]}, headers=auth())
    plan = client.post(P + "/setups", json={"registration_id": f"reg-{script['reg_suffix']}", "tables": tables or [{"schema": "DATA", "table": "orders"}]}, headers=auth())
    return script, registered, plan


def sign_in(client, plan):
    sid = plan["setup_id"]
    client.post(f"{P}/setups/{sid}/approve", json={"digest": plan["digest"]}, headers=auth())
    start = client.post("/api/v2/connect/oauth/start", json={"registration_id": plan["registration_id"], "setup_id": sid}, headers=auth())
    state = urllib.parse.parse_qs(urllib.parse.urlparse(start.json()["authorize_url"]).query)["state"][0]
    return client.get(f"/api/v2/connect/oauth/callback?code={CODE}&state={state}", follow_redirects=False)


def test_the_whole_flow_runs_through_the_real_app_and_the_scanner_stays_clean(caplog):
    caplog.set_level(logging.DEBUG)
    built, client, account, driver = harness()
    script, registered, plan = drive(client)
    assert script["reg_suffix"] == SUFFIX and registered.status_code == 201 and plan.status_code == 201
    callback = sign_in(client, plan.json())
    assert callback.status_code == 303 and callback.headers["location"] == f"/connect/review/{plan.json()['setup_id']}"
    run = client.post(f"{P}/setups/{plan.json()['setup_id']}/run", headers=auth()).json()
    assert run["ok"] is True and run["connection_status"] == "active" and run["created"] == ["role", "user"]
    assert driver.calls and driver.calls[0]["authenticator"] == "oauth" and driver.calls[0]["token"] == TOKEN
    report = client.get("/__harness/report").json()
    assert report["leaks"]["clean"] is True and report["audit_chain_verifies"] is True
    assert {"oauth_access_token", "authorization_code", "pkce_verifier", "service_private_key_b64"} <= set(report["leaks"]["watched"])
    assert [e["type"] for e in report["audit_events"]] == ["connect.consent_shown", "connect.setup_approved", "connect.setup_executed", "connect.verified"]
    assert report["connections"] == [{"id": run["connection_id"], "status": "active", "tables": ["compass_e2e_db_a1b2c3d4.data.orders"]}]
    app_logs = " ".join(r.getMessage() for r in caplog.records if not r.name.startswith(("httpx", "httpcore")))
    assert not built.scanner.labels_in(built.audit_text() + app_logs)


def test_a_planned_table_outside_the_ceiling_is_refused_by_the_real_app():
    _, client, *_ = harness()
    _, _, plan = drive(client, tables=[{"schema": "DATA", "table": "payroll_outside"}])
    assert plan.status_code == 422 and plan.json()["detail"] == "outside_ceiling"


def test_widening_the_setup_role_after_registration_stops_the_run_and_creates_nothing():
    built, client, account, driver = harness()
    script, registered, plan = drive(client)
    account.setup_grants = [*account.setup_grants, {"privilege": "SELECT", "granted_on": "TABLE", "name": 'COMPASS_E2E_DB_A1B2C3D4.DATA."payroll_outside"', "grant_option": "false"}]
    sign_in(client, plan.json())
    run = client.post(f"{P}/setups/{plan.json()['setup_id']}/run", headers=auth()).json()
    assert run["ok"] is False and run["code"] == "registration_changed" and run["connection_status"] == "removed"
    assert not any(s.startswith(("CREATE", "GRANT", "ALTER")) for s in account.executed)


def test_a_registration_for_a_different_account_is_refused():
    _, client, *_ = harness()
    body = {"registration_id": "reg-a1b2c3d4", "reg_suffix": SUFFIX, "host": HOST, "client_id": "client-id-12345678=", "account_locator": "ZZ99999",
            "database": st.fixture_db(SUFFIX), "tables": [{"schema": "DATA", "table": "orders"}], "setup_users": [OWNER]}
    assert client.post(P + "/registrations", json=body, headers=auth()).status_code == 422


def test_only_the_run_token_opens_the_api():
    _, client, *_ = harness()
    assert client.post(P + "/registration-script", json={}, headers=auth("not-the-token")).status_code == 401
    assert client.get(P + "/registrations").status_code == 401


# ---- the scanner really would catch a leak in the running app -----------------------------------------------------------


def test_a_secret_in_a_response_is_caught():
    built, client, *_ = harness()
    built.scanner.watch("planted", "planted-secret-value-0123456789")

    @built.app.get("/leaky")
    def leaky():
        return {"oops": "planted-secret-value-0123456789"}

    client.get("/leaky")
    assert not built.scanner.clean and built.scanner.findings[0].secret == "planted"


def test_a_secret_in_a_log_or_an_address_is_caught():
    built, client, *_ = harness()
    built.scanner.watch("planted", "planted-secret-value-0123456789")
    logging.getLogger("some.module").warning("connecting with planted-secret-value-0123456789")
    client.get("/__harness/report?x=planted-secret-value-0123456789")
    assert {f.where for f in built.scanner.findings} >= {"log:some.module", "request-url"}


def test_the_report_is_local_only():
    built, client, *_ = harness()
    assert client.get("/__harness/report").status_code == 200
    assert TestClient(WithClient(built.app, ("203.0.113.9", 5555))).get("/__harness/report").status_code == 403
    assert TestClient(built.app).get("/__harness/report").status_code == 403  # no client address at all: not local


# ---- the single-page app is served from the same origin ----------------------------------------------------------------------


def test_the_built_app_is_served_with_a_fallback_for_the_return_address_but_not_for_the_api_or_files_outside_it(tmp_path):
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>app</html>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "outside.txt").write_text("not for the web")
    _, client, *_ = harness(static)
    assert client.get("/connect/review/setup-0000000001").text == "<html>app</html>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/v2/connect/unknown").status_code == 404 and "app" not in client.get("/api/v2/connect/unknown").text
    assert client.get("/__harness/other").status_code == 404
    for sneaky in ("/..%2foutside.txt", "/%2e%2e/outside.txt", "/assets/../../outside.txt"):
        assert "not for the web" not in client.get(sneaky).text, sneaky  # never a file outside the built folder


# ---- the command line guards ----------------------------------------------------------------------------------------------


def test_the_harness_refuses_to_start_without_allow_live_and_with_bad_arguments(capsys):
    base = ["--expect-account", LOCATOR, "--suffix", SUFFIX, "--egress", "35.238.115.103/32"]
    assert main(base) == 2 and "--allow-live" in capsys.readouterr().err
    assert main([*base, "--allow-live", "--port", "80"]) == 2
    assert main(["--allow-live", "--expect-account", "bad account!", "--suffix", SUFFIX, "--egress", "1.2.3.4/32"]) == 2
    assert main(["--allow-live", "--expect-account", LOCATOR, "--suffix", "XYZ", "--egress", "1.2.3.4/32"]) == 2
    with pytest.raises(SystemExit):
        main(["--allow-live", "--suffix", SUFFIX, "--egress", "1.2.3.4/32"])  # --expect-account is required


def test_the_harness_binds_only_to_the_loopback_address_in_code():
    source = (Path(__file__).resolve().parents[1] / "app/connect/e2e/harness.py").read_text()
    assert 'host="127.0.0.1"' in source and "0.0.0.0" not in source and re.search(r"TENANT = ", source) and TENANT == "tenant-e2e"


# ---- the access log never carries the sign-in code -----------------------------------------------------------------------------


def test_the_redaction_blanks_the_callbacks_secret_values_and_nothing_else():
    from app.connect.oauth_api import redact_callback_query

    line = '127.0.0.1:5 - "GET /api/v2/connect/oauth/callback?code=abc123&state=XYZ789&scope=session:role:R HTTP/1.1" 303'
    out = redact_callback_query(line)
    assert "abc123" not in out and "XYZ789" not in out and "code=[redacted]" in out and "state=[redacted]" in out and "scope=session:role:R" in out
    assert redact_callback_query("/api/v2/connect/oauth/callback?error=access_denied&error_description=secret+words&state=Q") == \
        "/api/v2/connect/oauth/callback?error=access_denied&error_description=[redacted]&state=[redacted]"
    other = '/api/v2/analytics/analyze?code=keepme&state=keepme2 and a free-text code=nothing'
    assert redact_callback_query(other) == other  # only the callback's own address


def test_the_access_log_filter_is_installed_once_and_the_scanner_stays_clean_through_a_real_log_record():
    from app.connect.oauth_api import CallbackQueryRedactor, install_log_redaction

    built, client, account, driver = harness()
    access = logging.getLogger("uvicorn.access")
    install_log_redaction()
    install_log_redaction()
    assert sum(isinstance(f, CallbackQueryRedactor) for f in access.filters) == 1
    built.scanner.watch("authorization_code", CODE)
    access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5", "GET", f"/api/v2/connect/oauth/callback?code={CODE}&state=S", "1.1", 303)
    assert built.scanner.clean


def test_without_the_filter_the_scanner_would_catch_the_code_in_the_access_log():
    built, client, account, driver = harness()
    access = logging.getLogger("uvicorn.access")
    for f in [f for f in access.filters if f.__class__.__name__ == "CallbackQueryRedactor"]:
        access.removeFilter(f)
    built.scanner.watch("authorization_code", CODE)
    access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5", "GET", f"/api/v2/connect/oauth/callback?code={CODE}&state=S", "1.1", 303)
    assert [f.secret for f in built.scanner.findings] == ["authorization_code"]


def test_the_real_app_installs_the_redaction():
    from app import main  # noqa: F401 - importing the app is what installs it

    assert any(f.__class__.__name__ == "CallbackQueryRedactor" for f in logging.getLogger("uvicorn.access").filters)


# ---- the session helper the connection test uses ---------------------------------------------------------------------------------


def _record(**over):
    from app.connect.store import ConnectionRecord

    args = dict(tenant_id="ta", connection_id="conn-1", engine="snowflake", host=HOST, account="acme", user="COMPASS_SVC_00000000", role="COMPASS_RO_00000000",
                warehouse="COMPASS_REG_WH_A1B2C3D4", database="DB", default_schema="S", allowed_tables=("db.s.t",), secret_name="snowflake-key-conn-1", status="pending")
    args.update(over)
    return ConnectionRecord(**args)


def test_the_session_helper_opens_the_service_user_session_from_the_stored_key_and_does_not_need_an_active_connection():
    import base64

    from app.connect.gateway import snowflake_session_for
    from app.connect.store import TenantSecretStore

    secrets = TenantSecretStore()
    secrets.put("ta", "snowflake-key-conn-1", base64.b64encode(b"fake-der-bytes").decode())
    calls = []

    class Driver:
        @staticmethod
        def connect(**kwargs):
            calls.append(kwargs)
            return object()

    snowflake_session_for(_record(), secrets, connector=Driver)
    (call,) = calls
    assert call["account"] == "acme" and call["user"] == "COMPASS_SVC_00000000" and call["role"] == "COMPASS_RO_00000000" and call["private_key"] == b"fake-der-bytes"
    assert call["warehouse"] == "COMPASS_REG_WH_A1B2C3D4" and "password" not in call


@pytest.mark.parametrize("record,store", [("engine", "databricks"), ("missing", None), ("corrupt", "not base64 !!")])
def test_the_session_helper_refuses_a_wrong_engine_and_a_missing_or_corrupt_key_with_a_stable_code(record, store):
    import base64

    from app.connect.gateway import ConnectionUnavailable, snowflake_session_for
    from app.connect.store import TenantSecretStore

    secrets = TenantSecretStore()
    if record == "corrupt":
        secrets.put("ta", "snowflake-key-conn-1", store)
    elif record == "engine":
        secrets.put("ta", "snowflake-key-conn-1", base64.b64encode(b"x").decode())
    with pytest.raises(ConnectionUnavailable) as caught:
        snowflake_session_for(_record(engine="databricks") if record == "engine" else _record(), secrets, connector=object())
    assert caught.value.code == ("engine_not_supported" if record == "engine" else "credential_unavailable")


# ---- real uvicorn: the access log is redacted and the scanner still sees it ----------------------------------------------------------


def test_with_real_uvicorn_the_callback_code_never_reaches_the_access_log_and_the_scanner_is_still_attached():
    import socket
    import threading
    import time
    import urllib.request

    import uvicorn

    built, _, account, driver = harness()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(built.app, host="127.0.0.1", port=port, log_level="info"))  # configures uvicorn's own logging
    built.attach_logging()  # after it, as the harness does
    seen = []

    class Capture(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    capture = Capture()
    logging.getLogger("uvicorn.access").addHandler(capture)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        built.scanner.watch("authorization_code", CODE)
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/v2/connect/oauth/callback?code={CODE}&state=" + "z" * 40), timeout=10)  # noqa: S310
        except urllib.error.HTTPError:
            pass
        except urllib.error.URLError:
            pass
        time.sleep(0.3)
    finally:
        server.should_exit = True
        thread.join(10)
        logging.getLogger("uvicorn.access").removeHandler(capture)
    access = [m for m in seen if "oauth/callback" in m]
    assert access and all(CODE not in m for m in access) and any("[redacted]" in m for m in access)
    assert built.scanner.clean
    assert any(isinstance(h, ScanningLogHandler) for h in logging.getLogger("uvicorn.access").handlers)  # still attached after uvicorn's setup


def test_the_sign_in_code_is_expected_in_the_callbacks_address_but_every_other_secret_is_not():
    scanner = LeakScanner()
    scanner.watch("authorization_code", CODE)
    scanner.watch("oauth_access_token", TOKEN)
    scanner.scan("request-url", f"http://localhost/cb?code={CODE}", expected=("authorization_code",))
    assert scanner.clean
    scanner.scan("request-url", f"http://localhost/x?t={TOKEN}", expected=("authorization_code",))
    scanner.scan("response-body", f"the code {CODE}", expected=())
    assert {(f.secret, f.where) for f in scanner.findings} == {("oauth_access_token", "request-url"), ("authorization_code", "response-body")}


def test_main_attaches_logging_after_the_server_has_configured_its_own_and_writes_a_report(tmp_path, monkeypatch, capsys):
    import json

    import uvicorn

    seen = {}

    def fake_run(self):  # the server would block here; look at what main set up
        access = logging.getLogger("uvicorn.access")
        seen["scanner_attached"] = any(isinstance(h, ScanningLogHandler) for h in access.handlers)
        seen["redactor_installed"] = any(f.__class__.__name__ == "CallbackQueryRedactor" for f in access.filters)

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    report = tmp_path / "report.json"
    code = main(["--allow-live", "--expect-account", LOCATOR, "--suffix", SUFFIX, "--egress", "35.238.115.103/32", "--port", "8799", "--report", str(report)])
    out = capsys.readouterr().out
    assert code == 0 and seen == {"scanner_attached": True, "redactor_installed": True}
    assert "UI access token for this run:" in out and "127.0.0.1 only" in out
    written = json.loads(report.read_text())
    assert written["leaks"]["clean"] is True and written["audit_text_clean"] is True and written["audit_chain_verifies"] is True
