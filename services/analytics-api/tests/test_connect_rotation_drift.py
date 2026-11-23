"""S4 of docs/design/SELF_SERVE_CONNECT_DESIGN.md, against fakes: rotation with one file (the rotate file's own code runs against a fake admin client and the real endpoint over HTTP)
and the drift monitor (the free secret-expiry sweep and the optional read check). Nothing here claims a run against a workspace."""

from __future__ import annotations

import io
import json
import runpy
import sys
import threading
import types
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_connect_databricks_api import Session, Verifier, auth
from test_connect_enrol import ENROL_PATH, URL, Admin, Clock, begin, issue
from test_deployment_check_provision import SECRET

from app.connect.databricks_api import DatabricksRuntime, DatabricksSetups, build_databricks_router, build_enrol_router
from app.connect.drift import ROTATE_WITHIN_DAYS, DriftMonitor
from app.connect.enrol import AttemptLimiter, EnrolmentTokens
from app.connect.store import ConnectionStore, TenantSecretStore
from app.security.audit import AuditChain

ROTATE_PATH = ENROL_PATH + "/rotate"


class RotatingAdmin(Admin):
    """A fake admin whose every secret is different and that can list a principal's secrets, as the real API can."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.counter = 0
        self.owner: dict[str, str] = {}
        self.service_principal_secrets_proxy = SimpleNamespace(create=self._new_secret, delete=lambda pid, sid: self.secrets.pop(sid, None),
                                                               list=lambda service_principal_id: [SimpleNamespace(id=sid) for sid in list(self.secrets)])

    def _delete(self, pid):
        super()._delete(pid)
        for sid in [sid for sid, owner in self.owner.items() if owner == pid]:  # the real API deletes a principal's secrets with it
            self.secrets.pop(sid, None)
            del self.owner[sid]

    def _new_secret(self, service_principal_id, lifetime):
        self.counter += 1
        sid, value = f"sec-{self.counter}", f"{SECRET}-{self.counter}"
        self.secrets[sid] = value
        self.owner[sid] = service_principal_id
        return SimpleNamespace(id=sid, secret=value)


def build(*, bad_secret_suffix=None, clock=None, enrol_url=URL):
    clock = clock or Clock()
    store, secrets, chain = ConnectionStore(), TenantSecretStore(), AuditChain()
    setups = DatabricksSetups(store, secrets, chain, tokens=EnrolmentTokens(clock), limiter=AttemptLimiter(clock))
    calls: list[str] = []

    def factory(record):
        value = secrets.read(record.tenant_id, record.secret_name)
        assert value.startswith(SECRET)
        calls.append(value)
        return Session("SELECT" if bad_secret_suffix and value.endswith(bad_secret_suffix) else None)

    runtime = DatabricksRuntime(setups, Verifier(), None, factory, dns=lambda h: ["34.1.2.3"], enrol_url=enrol_url)
    app = FastAPI()
    app.include_router(build_databricks_router(runtime, lambda: None))
    app.include_router(build_enrol_router(runtime))
    return TestClient(app), setups, store, secrets, chain, clock, factory, calls


def run_any(tmp_path, monkeypatch, capsys, file, admin, client, *, args=()):
    path = tmp_path / file["filename"]
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
        r = client.post(urllib.parse.urlparse(request.full_url).path, content=request.data, headers={k: v for k, v in request.header_items()})
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


def connect(tmp_path, monkeypatch, capsys, *, bad=None):
    client, setups, store, secrets, chain, clock, factory, calls = build(bad_secret_suffix=bad)
    view = begin(client)
    admin = RotatingAdmin()
    code, out = run_any(tmp_path, monkeypatch, capsys, issue(client, view), admin, client)
    assert code == 0, out
    return client, setups, store, secrets, chain, clock, factory, calls, admin, view["connection_id"]


def rotate_file(client, connection_id, token="mgr-a"):
    return client.post(f"/api/v2/connect/databricks/connections/{connection_id}/rotate-file", headers=auth(token))


# ---- rotation with one file -------------------------------------------------------------------------------------------------------------------


def test_the_rotate_file_gives_the_connection_a_new_secret_and_revokes_the_old_ones(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys)
    record = store.get("ta", cid)
    before = secrets.read("ta", record.secret_name)
    r = rotate_file(client, cid)
    assert r.status_code == 200 and r.json()["filename"] == "compass-rotate.py" and SECRET not in json.dumps(r.json())
    code, out = run_any(tmp_path, monkeypatch, capsys, r.json(), admin, client)
    assert code == 0 and "rotated" in out and "1 older secret(s) revoked" in out and SECRET not in out
    after = secrets.read("ta", record.secret_name)
    assert after != before and after.startswith(SECRET) and list(admin.secrets.values()) == [after]  # only the new secret exists on the workspace
    assert store.get("ta", cid).status == "active"
    kinds = [e.event_type for e in chain.events]
    assert "connect.rotate_file_issued" in kinds and "connect.rotated_by_file" in kinds and "connect.credential_rotated" in kinds
    assert SECRET not in repr(chain.events)
    status = client.get(f"/api/v2/connect/databricks/connections/{cid}/rotate-status", headers=auth()).json()
    assert status["state"] == "connected" and all(c["passed"] for c in status["checks"])


def test_a_new_secret_that_cannot_read_the_tables_is_deleted_and_the_old_one_stays(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys, bad="-2")
    record = store.get("ta", cid)
    before = secrets.read("ta", record.secret_name)
    code, out = run_any(tmp_path, monkeypatch, capsys, rotate_file(client, cid).json(), admin, client)
    assert code == 1 and "not rotated" in out and "the connection is as it was" in out
    assert secrets.read("ta", record.secret_name) == before and list(admin.secrets.values()) == [before] and store.get("ta", cid).status == "active"
    assert client.get(f"/api/v2/connect/databricks/connections/{cid}/rotate-status", headers=auth()).json()["state"] == "refused"


def test_a_token_works_only_for_its_own_purpose_and_a_misdirected_call_does_not_spend_it(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys)
    token = rotate_file(client, cid).json()["content"].split('TOKEN = "')[1].split('"')[0]
    wrong = client.post(ENROL_PATH, json={"application_id": "x", "client_secret": SECRET}, headers={"Authorization": f"Bearer {token}"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "token_wrong_purpose"
    ok = client.post(ROTATE_PATH, json={"client_secret": f"{SECRET}-9"}, headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200  # still unspent (its own purpose; it reads the tables with the fake session)
    enrol_token = issue(client, begin(client))["content"].split('TOKEN = "')[1].split('"')[0]
    assert client.post(ROTATE_PATH, json={"client_secret": SECRET}, headers={"Authorization": f"Bearer {enrol_token}"}).json()["detail"] == "token_wrong_purpose"


def test_a_rotate_file_is_single_use_expires_and_is_replaced_by_a_new_one(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys)
    first, second = rotate_file(client, cid).json(), rotate_file(client, cid).json()
    old = first["content"].split('TOKEN = "')[1].split('"')[0]
    assert client.post(ROTATE_PATH, json={"client_secret": f"{SECRET}-9"}, headers={"Authorization": f"Bearer {old}"}).json()["detail"] == "token_invalid"  # revoked by the newer file
    assert run_any(tmp_path, monkeypatch, capsys, second, admin, client)[0] == 0
    code, out = run_any(tmp_path, monkeypatch, capsys, second, admin, client)
    assert code == 1 and "token_used" in out
    third = rotate_file(client, cid).json()
    clock.now += 1801
    code, out = run_any(tmp_path, monkeypatch, capsys, third, admin, client)
    assert code == 1 and "token_expired" in out and len(admin.secrets) == 1


def test_only_a_manager_of_the_same_tenant_can_get_a_rotate_file_for_a_live_connection(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys)
    assert rotate_file(client, cid, "usr-a").status_code == 403 and rotate_file(client, cid, "mgr-b").status_code == 404 and rotate_file(client, "nope").status_code == 404
    client.delete(f"/api/v2/connect/databricks/connections/{cid}", headers=auth())
    assert rotate_file(client, cid).status_code == 404  # removed: nothing to rotate
    unconfigured, *_ = build(enrol_url="")
    assert unconfigured.post("/api/v2/connect/databricks/connections/x/rotate-file", headers=auth()).status_code in (404, 503)


def test_a_rotate_file_for_a_connection_whose_principal_changed_is_refused(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys)
    token = rotate_file(client, cid).json()["content"].split('TOKEN = "')[1].split('"')[0]
    store.update(replace(store.get("ta", cid), user="00000000-0000-0000-0000-000000000000"))
    r = client.post(ROTATE_PATH, json={"client_secret": f"{SECRET}-9"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 409 and r.json()["detail"] == "connection_changed"


# ---- drift: the expiry sweep and the read check --------------------------------------------------------------------------------


def _with_expiry(store, cid, when):
    store.update(replace(store.get("ta", cid), secret_expires_at=when))


def test_the_expiry_sweep_warns_once_pauses_an_expired_connection_and_never_touches_the_warehouse(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys)
    monitor = DriftMonitor(setups, factory)
    used = len(calls)
    now = datetime.now(timezone.utc)
    assert monitor.tick(now) == {"expired": 0, "warned": 0, "read_checked": 0, "paused": 0}  # a fresh secret: nothing to say
    _with_expiry(store, cid, now + timedelta(days=ROTATE_WITHIN_DAYS - 1))
    assert monitor.tick(now)["warned"] == 1 and monitor.tick(now)["warned"] == 0  # once per expiry date
    assert [e.payload["days_left"] for e in chain.events if e.event_type == "connect.secret_expiring"] == [ROTATE_WITHIN_DAYS - 1]
    _with_expiry(store, cid, now - timedelta(seconds=1))
    assert monitor.tick(now)["expired"] == 1 and store.get("ta", cid).status == "suspended"
    assert monitor.tick(now)["expired"] == 0  # already paused
    assert any(e.event_type == "connect.secret_expired" for e in chain.events) and len(calls) == used  # the sweep never opened a session
    assert all(e.actor_id == "system:drift-monitor" for e in chain.events if e.event_type in ("connect.secret_expired", "connect.secret_expiring"))


def test_a_rotation_re_arms_the_warning_for_the_new_expiry(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys)
    monitor = DriftMonitor(setups, factory)
    now = datetime.now(timezone.utc)
    _with_expiry(store, cid, now + timedelta(days=10))
    assert monitor.tick(now)["warned"] == 1
    assert run_any(tmp_path, monkeypatch, capsys, rotate_file(client, cid).json(), admin, client)[0] == 0  # rotation sets a fresh 90-day expiry
    assert monitor.tick(now)["warned"] == 0 and store.get("ta", cid).secret_expires_at > now + timedelta(days=80)


def test_the_read_check_is_off_by_default_and_when_on_pauses_a_connection_that_cannot_read(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys)
    now = datetime.now(timezone.utc)
    used = len(calls)
    assert DriftMonitor(setups, factory).tick(now)["read_checked"] == 0 and len(calls) == used  # interval 0: never reads the customer's tables on a timer
    healthy = DriftMonitor(setups, factory, read_check_interval=3600)
    assert healthy.tick(now)["read_checked"] == 1 and store.get("ta", cid).status == "active" and len(calls) > used
    assert healthy.tick(now + timedelta(minutes=10))["read_checked"] == 0  # within the interval
    assert healthy.tick(now + timedelta(hours=2))["read_checked"] == 1
    broken_factory = lambda record: Session("SELECT")  # noqa: E731 - the principal can no longer read
    broken = DriftMonitor(setups, broken_factory, read_check_interval=60)
    assert broken.tick(now)["paused"] == 1 and store.get("ta", cid).status == "suspended"
    assert any(e.event_type == "connect.health_checked" and e.actor_id == "system:drift-monitor" and e.payload["ok"] is False for e in chain.events)


def test_the_monitor_swallows_its_own_failures_and_its_thread_starts_and_stops(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys)
    monitor = DriftMonitor(setups, factory, expiry_interval=0.01)
    monkeypatch.setattr(setups.store, "all", lambda: (_ for _ in ()).throw(RuntimeError("store down")))
    assert monitor.tick() == {"expired": 0, "warned": 0, "read_checked": 0, "paused": 0}
    ticked = threading.Event()
    monkeypatch.setattr(monitor, "tick", lambda now=None: ticked.set() or {})
    monitor.start()
    assert ticked.wait(2)
    monitor.stop()
    assert monitor._thread is None


def test_the_listing_flags_rotation_inside_the_warning_window(tmp_path, monkeypatch, capsys):
    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys)
    now = datetime.now(timezone.utc)
    flags = {}
    for days in (ROTATE_WITHIN_DAYS + 2, ROTATE_WITHIN_DAYS - 1):
        _with_expiry(store, cid, now + timedelta(days=days))
        flags[days] = client.get("/api/v2/connect/databricks/connections", headers=auth()).json()[0]["needs_rotation"]
    assert flags == {ROTATE_WITHIN_DAYS + 2: False, ROTATE_WITHIN_DAYS - 1: True}


def test_the_rotate_file_prints_the_digest_the_server_reports_and_refuses_a_plain_http_address(tmp_path, monkeypatch, capsys):
    import subprocess

    from app.connect.enrol import EnrolRefused, render_rotate_file

    client, setups, store, secrets, chain, clock, factory, calls, admin, cid = connect(tmp_path, monkeypatch, capsys)
    file = rotate_file(client, cid).json()
    path = tmp_path / "r.py"
    path.write_text(file["content"])
    assert subprocess.run([sys.executable, "-I", str(path), "--digest"], capture_output=True, text=True, check=True).stdout.strip() == file["digest"]
    for url in ("http://example.com/x", "", "ftp://x.com/y"):
        try:
            render_rotate_file(application_id=store.get("ta", cid).user, host="h.cloud.databricks.com", rotate_url=url, token="cenrol_" + "x" * 30)
        except EnrolRefused as refused:
            assert refused.code == "enrol_url_invalid"
        else:
            raise AssertionError(f"{url!r} must be refused")
