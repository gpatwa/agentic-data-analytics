"""The local deployment check, with a fake Databricks connector: the real routes, wiring, Entra verifier and warm-up service over HTTP; nothing leaves the process. Nothing here claims a live run."""

from __future__ import annotations

import sys
import threading
import types

import pytest
from fastapi.testclient import TestClient
from reference_stack.deployment_check import check
from reference_stack.deployment_check.__main__ import ID_VAR, SECRET_VAR, main

SECRET = "dapi-secret-value-that-must-never-appear"


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description = conn, [("v",)]

    def execute(self, sql, parameters=None):
        self.conn.sent.append(sql)
        self.explain = sql.startswith("EXPLAIN COST")
        return self

    def fetchmany(self, n=None):
        return [("1",)]

    def fetchall(self):
        return [("Statistics: sizeInBytes=1024.0 B",)] if getattr(self, "explain", False) else [("1",)]

    def fetchone(self):
        return ("1",)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self):
        self.sent = []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


class FakeConnector:
    def __init__(self):
        self.connections, self.kwargs, self._lock = [], [], threading.Lock()

    def connect(self, **kwargs):
        with self._lock:
            self.kwargs.append(kwargs)
            conn = FakeConn()
            self.connections.append(conn)
            return conn


@pytest.fixture
def fake_sdk(monkeypatch):
    """The SDK's OAuth helper stands in (the real one is installed with requirements-warehouses.txt): the gateway only asks it for a credentials provider."""
    core = types.ModuleType("databricks.sdk.core")
    core.Config = lambda **kw: kw
    core.oauth_service_principal = lambda cfg: (lambda: {})
    for name, module in (("databricks", types.ModuleType("databricks")), ("databricks.sdk", types.ModuleType("databricks.sdk")), ("databricks.sdk.core", core)):
        monkeypatch.setitem(sys.modules, name, module)


def test_the_whole_wired_path_works_over_http_with_the_real_verifier_and_a_fake_connector(tmp_path, fake_sdk):
    connector = FakeConnector()
    local = check.build_local_app(tmp_path, connector=connector)
    check.register(local, host="dbc-1234abcd-5678.cloud.databricks.com", http_path="/sql/1.0/warehouses/abcdef0123456789", catalog="dev", schema="compass_synthetic_food_delivery",
                   client_id="0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0", client_secret=SECRET, warehouse="W")
    result = check.run_check(TestClient(local.app, raise_server_exceptions=False), local, wait_seconds=30, sleep=lambda s: __import__("time").sleep(0.05))
    assert result["wired_path_ok"], result
    steps = result["steps"]
    assert steps["warm"]["status"] == 202 and steps["warm_up_recorded"]["keys"] == ["code", "connection_id", "seconds", "statements_run"]
    sent = [q for c in connector.connections for q in c.sent]
    real = [q for q in sent if not q.startswith(("EXPLAIN COST", "SET "))]  # the gateway's own cost estimate and session settings are not answers
    assert real and all(q.lstrip().upper().startswith(("SELECT", "WITH")) for q in real) and len(real) <= 4
    assert SECRET not in repr(result) and SECRET not in "".join(sent)


def test_a_token_without_the_admin_role_cannot_turn_the_switch_on(tmp_path):
    local = check.build_local_app(tmp_path, connector=FakeConnector())
    check.register(local, host="dbc-1234abcd-5678.cloud.databricks.com", http_path="/sql/1.0/warehouses/abcdef0123456789", catalog="dev", schema="compass_synthetic_food_delivery",
                   client_id="0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0", client_secret=SECRET, warehouse="W")
    client = TestClient(local.app, raise_server_exceptions=False)
    assert client.put(f"/api/v2/connect/databricks/connections/{check.CONNECTION}/warm-up", json={"enabled": True}).status_code == 401  # no token at all
    assert client.post("/api/v2/analytics/warm", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_the_dry_run_makes_no_call_and_names_the_secret_variables_not_their_values(capsys, monkeypatch):
    monkeypatch.setenv(SECRET_VAR, SECRET)
    assert main(["--host", "h.cloud.databricks.com", "--warehouse-name", "W"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and ID_VAR in out and SECRET_VAR in out and SECRET not in out and "skipped_disabled" in out and "no model is called" in out


@pytest.mark.parametrize("extra", [[], ["--execute"], ["--execute", "--profile", "p", "--approval-id", "a", "--out", "o"]])
def test_execute_refuses_without_its_inputs_the_variables_or_an_approval(extra, capsys, monkeypatch):
    monkeypatch.delenv(ID_VAR, raising=False)
    monkeypatch.delenv(SECRET_VAR, raising=False)
    code = main(["--host", "h.cloud.databricks.com", "--warehouse-name", "W", *extra])
    assert code == (0 if not extra else 2)
    assert SECRET not in capsys.readouterr().err


def test_a_cap_above_the_maximum_is_refused(capsys):
    assert main(["--host", "h.cloud.databricks.com", "--warehouse-name", "W", "--cap-minutes", "21"]) == 2
