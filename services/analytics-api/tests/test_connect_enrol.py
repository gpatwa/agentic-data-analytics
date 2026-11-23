"""The enrol file (S2 of docs/design/SELF_SERVE_CONNECT_DESIGN.md), against fakes: the single-use token, the endpoint, the generated file, and the whole path with the file's code
running against a fake Databricks admin client and the real endpoint over HTTP. Nothing here claims a run against a workspace."""

from __future__ import annotations

import io
import json
import runpy
import subprocess
import sys
import types
import urllib.error
import urllib.request
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_connect_databricks_api import BODY as SETUP_BODY
from test_connect_databricks_api import HOST, Session, Verifier, auth
from test_deployment_check_provision import SECRET, FakeAdmin

from app.connect.databricks_api import (
    ENROL_PATH,
    DatabricksRuntime,
    DatabricksSetups,
    build_databricks_router,
    build_enrol_router,
)
from app.connect.enrol import AttemptLimiter, EnrolmentTokens, EnrolRefused, normalized_digest, render_enrol_file
from app.connect.grants import ConnectScope
from app.connect.store import ConnectionStore, TenantSecretStore
from app.security.audit import AuditChain

URL = f"http://localhost:8000{ENROL_PATH}"
BODY = {**SETUP_BODY, "warehouse_name": "Serverless Starter Warehouse"}
WAREHOUSE_ID = "abcdef0123456789"


class Admin(FakeAdmin):
    """The fake admin with UUID application ids and a hex warehouse id, as the real API has."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.warehouses.list = lambda: [SimpleNamespace(name="Serverless Starter Warehouse", id=WAREHOUSE_ID)]

    def _create(self, display_name):
        if self.fail_on == "create_principal":
            raise RuntimeError("boom")
        p = SimpleNamespace(id=f"id-{len(self.principals)}", application_id=str(uuid.uuid4()), display_name=display_name)
        self.principals[display_name] = p
        return p


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def build(*, fail_test=False, enrol_url=URL, clock=None):
    clock = clock or Clock()
    store, secrets, chain = ConnectionStore(), TenantSecretStore(), AuditChain()
    setups = DatabricksSetups(store, secrets, chain, tokens=EnrolmentTokens(clock), limiter=AttemptLimiter(clock))

    def factory(record):
        assert secrets.read(record.tenant_id, record.secret_name) == SECRET
        return Session("SELECT" if fail_test else None)

    runtime = DatabricksRuntime(setups, Verifier(), None, factory, dns=lambda h: ["34.1.2.3"], enrol_url=enrol_url)
    app = FastAPI()
    app.include_router(build_databricks_router(runtime, lambda: None))
    app.include_router(build_enrol_router(runtime))
    return TestClient(app), setups, store, secrets, chain, clock


def begin(client, token="mgr-a"):
    r = client.post("/api/v2/connect/databricks/setups", json=BODY, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()


def issue(client, view, token="mgr-a"):
    r = client.post(f"/api/v2/connect/databricks/setups/{view['setup_id']}/enrol-file", headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()


def run_file(tmp_path, monkeypatch, capsys, file, admin, client, *, args=()):
    """Execute the generated file's own code with a fake SDK and a network that goes to the real endpoint over HTTP."""
    path = tmp_path / "compass-enrol.py"
    path.write_text(file["content"])
    sdk = types.ModuleType("databricks.sdk")
    sdk.WorkspaceClient = lambda **kw: admin
    for name, module in (("databricks", types.ModuleType("databricks")), ("databricks.sdk", sdk)):
        monkeypatch.setitem(sys.modules, name, module)

    class Response:
        def __init__(self, r):
            self.r = r

        def read(self):
            return self.r.content

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout=0):
        r = client.post(ENROL_PATH, content=request.data, headers={k: v for k, v in request.header_items()})
        if r.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, r.status_code, "x", {}, io.BytesIO(r.content))
        return Response(r)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sys, "argv", [str(path), *args])
    code = 0
    try:
        runpy.run_path(str(path), run_name="__main__")
    except SystemExit as exit_:
        code = exit_.code or 0
    return code, capsys.readouterr().out


EMPTY = {"principals": [], "secrets": [], "grants": [], "acl": []}

# ---- tokens and limiter -------------------------------------------------------------------------------------------------------------


def test_a_token_is_single_use_expires_after_thirty_minutes_and_is_stored_only_as_a_hash():
    clock = Clock()
    tokens = EnrolmentTokens(clock)
    token, expires = tokens.mint(tenant_id="ta", setup_id="s1", digest="d")
    assert expires - clock.now == 1800 and token not in repr(tokens._by_hash) and token not in "".join(tokens._by_hash)
    assert tokens.redeem(token).setup_id == "s1"
    with pytest.raises(EnrolRefused) as used:
        tokens.redeem(token)
    assert used.value.code == "token_used"
    late, _ = tokens.mint(tenant_id="ta", setup_id="s2", digest="d")
    clock.now += 1801
    with pytest.raises(EnrolRefused) as expired:
        tokens.redeem(late)
    assert expired.value.code == "token_expired"


@pytest.mark.parametrize("bad", ["", "x", "cenrol_nope", "Bearer cenrol_x", None, 5, "cenrol_" + "a" * 300])
def test_a_malformed_or_unknown_token_is_refused(bad):
    with pytest.raises(EnrolRefused) as refused:
        EnrolmentTokens().redeem(bad)
    assert refused.value.code == "token_invalid"


def test_a_new_token_for_the_same_setup_revokes_the_old_one():
    tokens = EnrolmentTokens()
    old, _ = tokens.mint(tenant_id="ta", setup_id="s1", digest="d")
    new, _ = tokens.mint(tenant_id="ta", setup_id="s1", digest="d")
    with pytest.raises(EnrolRefused):
        tokens.redeem(old)
    assert tokens.redeem(new).tenant_id == "ta"


def test_failed_attempts_are_limited_per_caller_and_forgotten_after_the_window():
    clock = Clock()
    limiter = AttemptLimiter(clock, maximum=3, window=60)
    for _ in range(3):
        limiter.failed("a")
    with pytest.raises(EnrolRefused):
        limiter.check("a")
    limiter.check("b")
    clock.now += 61
    limiter.check("a")


# ---- the generated file -----------------------------------------------------------------------------------------------------------------


def _render(**kw):
    args = {"suffix": "a1b2c3d4", "warehouse_name": "W", "host": HOST, "enrol_url": URL, "token": "cenrol_" + "x" * 30}
    return render_enrol_file(ConnectScope("sales", (("data", "orders"), ("data", "customers"))), **{**args, **kw})


def test_the_file_is_readable_python_that_prints_the_digest_the_server_reports(tmp_path):
    file = _render()
    path = tmp_path / "f.py"
    path.write_text(file.content)
    out = subprocess.run([sys.executable, "-I", str(path), "--digest"], capture_output=True, text=True, check=True).stdout.strip()
    assert out == file.digest == normalized_digest(file.content)
    other = _render(token="cenrol_" + "y" * 30, enrol_url="https://other.example.com/enrol")
    assert other.digest == file.digest and other.content != file.content  # the address and the token are the only differences
    assert _render(warehouse_name="Other").digest != file.digest  # anything else changes it


@pytest.mark.parametrize("url", ["http://example.com/enrol", "ftp://example.com/x", "https://exa mple.com", "", "https://x.com/enrol?x=1"])
def test_only_an_https_address_or_localhost_goes_into_the_file(url):
    with pytest.raises(EnrolRefused) as refused:
        _render(enrol_url=url)
    assert refused.value.code == "enrol_url_invalid"


def test_the_plan_inside_the_file_grants_only_reads_on_the_named_tables():
    plan = json.loads(_render().content.split('json.loads(r"""')[1].split('""")')[0])
    assert plan["tables"] == ["data.customers", "data.orders"] and plan["principal"] == "compass-conn-a1b2c3d4"
    joined = "\n".join(plan["grant"] + plan["revoke"] + plan["show"])
    assert all(w not in joined for w in ("ALL PRIVILEGES", "MANAGE", "MODIFY", "GRANT OPTION", "OWNERSHIP"))
    assert len(plan["grant"]) == 4 and all("@APP@" in s for s in plan["grant"])


# ---- the whole path: the file's own code, a fake admin, the real endpoint over HTTP ----------------------------------------


def test_running_the_file_connects_the_workspace_with_nothing_copied_and_no_secret_anywhere_else(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, _ = build()
    view = begin(client)
    file = issue(client, view)
    admin = Admin()
    code, out = run_file(tmp_path, monkeypatch, capsys, file, admin, client)
    assert code == 0 and "enrolled" in out, out
    [record] = [r for r in store.list("ta") if r.connection_id == view["connection_id"]]
    assert record.status == "active" and record.user in {p.application_id for p in admin.principals.values()}
    assert secrets.read("ta", record.secret_name) == SECRET  # in custody only
    assert SECRET not in out and SECRET not in json.dumps(file) and SECRET not in repr(chain.events) and SECRET not in file["content"]
    kinds = [e.event_type for e in chain.events]
    assert "connect.enrol_file_issued" in kinds and "connect.enrolled" in kinds
    assert len(admin.grants) == 4 and len(admin.principals) == 1 and len(admin.secrets) == 1  # the connection's principal stays; it is the connection


def test_the_file_removes_everything_it_created_when_compass_refuses(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, _ = build(fail_test=True)  # the connection test fails: the principal cannot read the tables
    file = issue(client, begin(client))
    admin = Admin()
    code, out = run_file(tmp_path, monkeypatch, capsys, file, admin, client)
    assert code == 1 and "not enrolled" in out and "nothing left behind" in out and admin.left_behind() == EMPTY
    assert [r for r in store.list("ta") if r.status == "active"] == [] and SECRET not in out


def test_a_replayed_file_is_refused_and_removes_what_it_created(tmp_path, monkeypatch, capsys):
    client, *_ = build()
    file = issue(client, begin(client))
    first = Admin()
    assert run_file(tmp_path, monkeypatch, capsys, file, first, client)[0] == 0
    second = Admin()
    code, out = run_file(tmp_path, monkeypatch, capsys, file, second, client)
    assert code == 1 and "token_used" in out and second.left_behind() == EMPTY


def test_an_expired_file_is_refused_and_leaves_nothing(tmp_path, monkeypatch, capsys):
    client, _, _, _, _, clock = build()
    file = issue(client, begin(client))
    clock.now += 1801
    admin = Admin()
    code, out = run_file(tmp_path, monkeypatch, capsys, file, admin, client)
    assert code == 1 and "token_expired" in out and admin.left_behind() == EMPTY


def test_the_file_refuses_to_run_against_a_workspace_other_than_the_one_named(tmp_path, monkeypatch, capsys):
    client, *_ = build()
    file = issue(client, begin(client))
    admin = Admin()
    admin.config.host = "https://dbc-other.cloud.databricks.com"
    code, out = run_file(tmp_path, monkeypatch, capsys, file, admin, client)
    assert code == 1 and "workspace_is_not_the_one_named" in out and admin.statements == [] and admin.principals == {}


def test_remove_revokes_the_grants_and_deletes_the_principal(tmp_path, monkeypatch, capsys):
    client, *_ = build()
    file = issue(client, begin(client))
    admin = Admin()
    assert run_file(tmp_path, monkeypatch, capsys, file, admin, client)[0] == 0
    app_id = next(iter(admin.principals.values())).application_id
    code, out = run_file(tmp_path, monkeypatch, capsys, file, admin, client, args=("--remove", app_id))
    assert code == 0 and "removed" in out and admin.principals == {} and admin.grants == set()


# ---- the endpoint ---------------------------------------------------------------------------------------------------------------------


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _payload(**over):
    return {"application_id": str(uuid.uuid4()), "client_secret": SECRET, "http_path": f"/sql/1.0/warehouses/{WAREHOUSE_ID}", "grants": [], "warehouse_acl": [], **over}


def test_the_endpoint_needs_a_valid_token_and_says_only_a_code(tmp_path):
    client, *_ = build()
    for headers in ({}, _bearer("cenrol_" + "z" * 40), {"Authorization": "Basic abc"}):
        r = client.post(ENROL_PATH, json=_payload(), headers=headers)
        assert r.status_code == 401 and r.json()["detail"] == "token_invalid" and SECRET not in r.text


def test_repeated_bad_tokens_are_rate_limited():
    client, *_ = build()
    codes = [client.post(ENROL_PATH, json=_payload(), headers=_bearer("cenrol_" + "z" * 40)).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]


def test_a_readback_with_extra_or_dangerous_grants_is_refused_and_nothing_is_stored():
    client, setups, store, secrets, chain, _ = build()
    view = begin(client)
    token = issue(client, view)["content"].split('TOKEN = "')[1].split('"')[0]
    app_id = str(uuid.uuid4())
    grants = [["MODIFY", "CATALOG", "sales"], ["USE CATALOG", "CATALOG", "sales"]]
    r = client.post(ENROL_PATH, json=_payload(application_id=app_id, grants=grants, warehouse_acl=[[app_id, "CAN_USE"]]), headers=_bearer(token))
    assert r.status_code == 422 and r.json()["detail"] == "registration_dangerous" and SECRET not in r.text
    assert store.list("ta") == []  # the connection and its secret were never stored
    assert any(e.event_type == "connect.enrol_refused" for e in chain.events)


def test_changing_the_setup_after_the_file_was_issued_invalidates_it():
    client, *_ = build()
    view = begin(client)
    token = issue(client, view)["content"].split('TOKEN = "')[1].split('"')[0]
    client.post(f"/api/v2/connect/databricks/setups/{view['setup_id']}/script", json={"application_id": str(uuid.uuid4())}, headers=auth())
    r = client.post(ENROL_PATH, json=_payload(), headers=_bearer(token))
    assert r.status_code == 409 and r.json()["detail"] == "setup_changed"


def test_a_malformed_body_never_echoes_the_secret():
    client, *_ = build()
    token = issue(client, begin(client))["content"].split('TOKEN = "')[1].split('"')[0]
    r = client.post(ENROL_PATH, json={"application_id": 5, "client_secret": SECRET}, headers=_bearer(token))
    assert r.status_code == 422 and SECRET not in r.text


# ---- issuing the file --------------------------------------------------------------------------------------------------------------


def test_only_a_connection_manager_of_the_same_tenant_can_issue_the_file():
    client, *_ = build()
    view = begin(client)
    url = f"/api/v2/connect/databricks/setups/{view['setup_id']}/enrol-file"
    assert client.post(url, headers=auth("usr-a")).status_code == 403
    assert client.post(url, headers=auth("mgr-b")).status_code == 404
    assert client.post(url).status_code == 401


def test_the_file_is_not_offered_until_a_public_address_is_configured():
    client, *_ = build(enrol_url="")
    r = client.post(f"/api/v2/connect/databricks/setups/{begin(client)['setup_id']}/enrol-file", headers=auth())
    assert r.status_code == 503 and r.json()["detail"] == "enrol_not_configured"


def test_issuing_a_second_file_revokes_the_first_and_the_audit_records_digests_only():
    client, _, _, _, chain, _ = build()
    view = begin(client)
    first, second = issue(client, view), issue(client, view)
    old = first["content"].split('TOKEN = "')[1].split('"')[0]
    r = client.post(ENROL_PATH, json=_payload(), headers=_bearer(old))
    assert r.status_code == 401 and r.json()["detail"] == "token_invalid"  # revoked: not found
    issued = [e for e in chain.events if e.event_type == "connect.enrol_file_issued"]
    assert len(issued) == 2 and all(set(e.payload) == {"connection_id", "file_digest", "script_digest"} for e in issued)
    assert second["expires_in_minutes"] in (29, 30)


# ---- what the screen shows while the admin runs the file ----------------------------------------------------------------------------


def _status(client, view, token="mgr-a"):
    return client.get(f"/api/v2/connect/databricks/setups/{view['setup_id']}/status", headers=auth(token))


def test_the_status_goes_from_waiting_to_connected_with_the_per_table_checks(tmp_path, monkeypatch, capsys):
    client, *_ = build()
    view = begin(client)
    assert _status(client, view).status_code == 404  # no file issued yet
    file = issue(client, view)
    waiting = _status(client, view).json()
    assert waiting["state"] == "waiting" and waiting["expires_in_minutes"] in (29, 30) and waiting["checks"] == []
    assert run_file(tmp_path, monkeypatch, capsys, file, Admin(), client)[0] == 0
    done = _status(client, view).json()
    assert done["state"] == "connected" and done["code"] is None and {c["table"] for c in done["checks"]} == {"sales.data.customers", "sales.data.orders"}
    assert all(c["passed"] for c in done["checks"]) and SECRET not in json.dumps(done)


def test_the_status_shows_a_refusal_with_its_code_and_an_expired_file_as_expired(tmp_path, monkeypatch, capsys):
    client, _, _, _, _, clock = build(fail_test=True)
    view = begin(client)
    file = issue(client, view)
    run_file(tmp_path, monkeypatch, capsys, file, Admin(), client)
    refused = _status(client, view).json()
    assert refused["state"] == "refused" and refused["code"] == "connection_test_failed" and not any(c["passed"] for c in refused["checks"])
    other = begin(client)
    issue(client, other)
    clock.now += 1801
    assert _status(client, other).json()["state"] == "expired"


def test_the_status_is_for_managers_of_the_same_tenant_only():
    client, *_ = build()
    view = begin(client)
    issue(client, view)
    assert _status(client, view, "usr-a").status_code == 403 and _status(client, view, "mgr-b").status_code == 404


def test_the_connection_list_carries_the_principals_application_id_for_the_remove_command(tmp_path, monkeypatch, capsys):
    client, *_ = build()
    view = begin(client)
    admin = Admin()
    run_file(tmp_path, monkeypatch, capsys, issue(client, view), admin, client)
    [row] = client.get("/api/v2/connect/databricks/connections", headers=auth()).json()
    assert row["application_id"] == next(iter(admin.principals.values())).application_id and SECRET not in json.dumps(row)
