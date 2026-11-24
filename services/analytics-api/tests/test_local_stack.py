"""The local stack's start-up, with a fake driver: it serves the real Databricks routes on localhost with a test identity, writes its token file and removes it afterwards, and refuses without drivers."""

from __future__ import annotations

import sys

from reference_stack.local_stack.__main__ import banner, main
from test_deployment_check import FakeConnector, fake_sdk  # noqa: F401  (the fixture is used by name)


def test_the_selftest_serves_the_databricks_routes_with_the_test_identity(tmp_path, fake_sdk, capsys):  # noqa: F811
    token_file = tmp_path / "t" / "token.txt"
    assert main(["--selftest", "--token-file", str(token_file)], connector=FakeConnector()) == 0
    assert "capabilities 200, can_manage=True" in capsys.readouterr().out
    assert token_file.exists() and token_file.read_text().count(".") == 2 and oct(token_file.stat().st_mode)[-3:] == "600"  # a JWT, readable by the owner only


def test_the_stack_refuses_without_the_drivers(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "databricks.sdk.core", None)  # an import of it now fails
    assert main(["--selftest", "--token-file", str(tmp_path / "t.txt")]) == 2
    assert "not installed" in capsys.readouterr().err


def test_the_banner_says_what_to_do_and_what_it_does_not_cover(tmp_path):
    text = banner(tmp_path / "token.txt", web=False, port=8090)
    assert "127.0.0.1:8090" in text and "npm run dev" in text and "Get the file" in text and "--remove" in text and "Nothing is deployed" in text
    assert "(started for you)" in banner(tmp_path / "token.txt", web=True, port=8090)


def test_the_stack_binds_localhost_only():
    import inspect

    from reference_stack.local_stack import __main__ as stack

    assert 'host="127.0.0.1"' in inspect.getsource(stack.main) and "0.0.0.0" not in inspect.getsource(stack)


def test_the_test_token_lasts_a_working_day_not_an_hour(tmp_path, fake_sdk):  # noqa: F811
    import base64
    import json
    import time

    token_file = tmp_path / "token.txt"
    assert main(["--selftest", "--token-file", str(token_file)], connector=FakeConnector()) == 0
    payload = json.loads(base64.urlsafe_b64decode(token_file.read_text().split(".")[1] + "=="))
    assert 7.5 * 3600 < payload["exp"] - time.time() <= 8 * 3600


def test_the_local_stack_offers_a_sign_in_that_returns_its_own_token_and_only_to_this_computer(tmp_path, fake_sdk):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from reference_stack.local_stack.__main__ import dev_sign_in_router

    app = FastAPI()
    app.include_router(dev_sign_in_router("tok.en.value"))

    class Peer:  # sets the address the request appears to come from, as the server would
        def __init__(self, asgi, host):
            self.asgi, self.host = asgi, host

        async def __call__(self, scope, receive, send):
            scope["client"] = (self.host, 5555)
            await self.asgi(scope, receive, send)

    here = TestClient(Peer(app, "127.0.0.1"))
    assert here.get("/api/v2/auth/options").json()["dev_login"] is True
    assert here.post("/api/v2/auth/dev-login").json() == {"token": "tok.en.value"}
    for host in ("203.0.113.9", "10.0.0.5", "192.168.1.20"):
        away = TestClient(Peer(app, host))
        assert away.get("/api/v2/auth/options").status_code == 403 and away.post("/api/v2/auth/dev-login").status_code == 403, host


def test_the_real_app_has_no_dev_sign_in_routes(tmp_path):
    from app.main import app

    assert not [r for r in app.routes if getattr(r, "path", "").startswith("/api/v2/auth/")]
