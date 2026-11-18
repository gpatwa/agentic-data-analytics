"""The sandbox-only local redirect switch: off by default, strict when on, and not reachable from a request.

A developer running the sign-in on their own machine against a throwaway integration needs `http://localhost:<port>/...`. The
product refuses that everywhere unless a caller in code says `allow_local_dev_redirect=True`; these tests pin both halves."""

import inspect
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.test_connect_product_run import (
    CEILING,
    HOST,
    LOCATOR,
    REGISTER,
    Verifier,
    auth,
    build,
    register,
)

from app.connect import oauth_api, product_run, product_run_api, redirects, registration
from app.connect.grants import ConnectScope, ScopeError
from app.connect.oauth import OAuthClientConfig
from app.connect.product_run_api import ProductRunRuntime, build_product_run_router
from app.connect.redirects import is_local_dev_redirect, validate_redirect_uri
from app.connect.registration import RegNames, generate_registration, validate_redirect, verify_registration

LOCAL = "http://localhost:8765/api/v2/connect/oauth/callback"
PROD = "https://connect.example.com/api/v2/connect/oauth/callback"
NAMES = RegNames.for_registration("a1b2c3d4")
P = "/api/v2/connect/product-run"


def script(redirect=LOCAL, allow=True):
    return generate_registration(CEILING, suffix="a1b2c3d4", redirect_uri=redirect, egress_networks=["35.238.115.103/32"], setup_users=["ADMIN_USER"],
                                 allow_local_dev_redirect=allow)


# ---- off by default, everywhere ------------------------------------------------------------------------------------------


def test_a_local_http_redirect_is_refused_by_default_in_every_layer():
    with pytest.raises(ValueError):
        validate_redirect_uri(LOCAL)
    with pytest.raises(ScopeError):
        validate_redirect(LOCAL)
    with pytest.raises(ScopeError):
        script(allow=False)
    with pytest.raises(ValueError):
        OAuthClientConfig("ta", "reg-00000001", HOST, "client-id-12345678=", NAMES.setup_role, LOCAL)
    service, *_ = build()
    with pytest.raises(ValueError):
        register(service, redirect_uri=LOCAL)


def test_the_switch_is_a_keyword_argument_that_defaults_to_false_everywhere():
    for fn, name in ((validate_redirect, "allow_local_dev"), (generate_registration, "allow_local_dev_redirect"), (verify_registration, "allow_local_dev_redirect"),
                     (product_run.ProductRunService.register, "allow_local_dev_redirect")):
        parameter = inspect.signature(fn).parameters[name]
        assert parameter.default is False and parameter.kind is inspect.Parameter.KEYWORD_ONLY or name == "allow_local_dev_redirect", fn
    assert OAuthClientConfig.__dataclass_fields__["allow_local_dev_redirect"].default is False
    assert ProductRunRuntime().allow_local_dev_redirect is False


def test_nothing_reads_the_switch_from_the_environment_or_a_request():
    for module in (redirects, registration, product_run, product_run_api, oauth_api):
        source = inspect.getsource(module)
        assert "os.environ" not in source and "getenv" not in source and "import os" not in source, module.__name__
    assert "allow_local_dev_redirect" not in inspect.getsource(product_run_api.RegisterBody) + inspect.getsource(product_run_api.ScriptBody)


# ---- strict when on ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("uri", [LOCAL, "http://127.0.0.1:8765/cb", "http://localhost:1024/a/b", "http://localhost:65535/cb"])
def test_a_local_redirect_with_a_port_and_a_plain_path_is_accepted_when_allowed(uri):
    assert validate_redirect_uri(uri, allow_local_dev=True) == uri and is_local_dev_redirect(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "http://evil.example:8765/cb", "http://localhost.evil.example:8765/cb", "http://127.0.0.1.evil.example:8765/cb", "http://[::1]:8765/cb",
        "http://10.0.0.1:8765/cb", "http://0.0.0.0:8765/cb", "http://localhost/cb", "http://localhost:80/cb", "http://localhost:1023/cb",
        "http://localhost:65536/cb", "http://localhost:8765", "http://localhost:8765/cb?next=evil", "http://localhost:8765/cb#x",
        "http://user:pw@localhost:8765/cb", "http://localhost:8765/../cb", "http://localhost:8765/c b", "ftp://localhost:8765/cb", "http://localhost:8765/cb'",
    ],
)
def test_anything_else_over_http_is_still_refused_when_allowed(uri):
    with pytest.raises(ValueError):
        validate_redirect_uri(uri, allow_local_dev=True)


def test_a_production_https_redirect_is_unchanged_whether_or_not_the_switch_is_on():
    for allow in (False, True):
        assert validate_redirect_uri(PROD, allow_local_dev=allow) == PROD and not is_local_dev_redirect(PROD)
    for bad in ("https://connect.example.com:8443/cb", "https://10.0.0.1/cb", "https://localhost/cb", "https://connect.example.com/cb?x=1"):
        with pytest.raises(ValueError):
            validate_redirect_uri(bad, allow_local_dev=True)


# ---- what the script says and the verifier expects ---------------------------------------------------------------------------


def test_a_local_script_adds_the_non_tls_flag_and_says_it_is_sandbox_only_and_a_production_script_never_does():
    local = script()
    (integration,) = [s for s in local.statements if s.startswith("CREATE SECURITY INTEGRATION")]
    assert f"OAUTH_REDIRECT_URI = '{LOCAL}' OAUTH_ALLOW_NON_TLS_REDIRECT_URI = TRUE OAUTH_ENFORCE_PKCE = TRUE" in integration
    assert any("Sandbox only" in line and "plain http" in line for line in local.summary)
    prod = script(PROD, allow=True)
    assert "NON_TLS" not in "\n".join(prod.statements) and not any("Sandbox only" in line for line in prod.summary)
    assert "NON_TLS" not in "\n".join(registration.generate_registration(CEILING, suffix="a1b2c3d4", redirect_uri=PROD, egress_networks=["35.238.115.103/32"],
                                                                          setup_users=["U"]).statements)


def _rows(redirect, non_tls):
    props = {"ENABLED": "true", "OAUTH_REDIRECT_URI": redirect, "OAUTH_ALTERNATE_REDIRECT_URIS": "", "OAUTH_CLIENT_TYPE": "PUBLIC",
             "OAUTH_ISSUE_REFRESH_TOKENS": "false", "OAUTH_ACCESS_TOKEN_VALIDITY": "600", "OAUTH_ENFORCE_PKCE": "true", "OAUTH_USE_SECONDARY_ROLES": "NONE",
             "PRE_AUTHORIZED_ROLES_LIST": "", "BLOCKED_ROLES_LIST": "ACCOUNTADMIN,ORGADMIN,SECURITYADMIN", "ALLOWED_ROLES_LIST": "",
             "OAUTH_ALLOW_NON_TLS_REDIRECT_URI": non_tls, "NETWORK_POLICY": NAMES.network_policy}
    return [{"property": k, "property_value": v} for k, v in props.items()]


def _grants():
    rows = []
    for x in script().statements:
        m = re.match(r"GRANT (CREATE ROLE|CREATE USER|USAGE|SELECT) ON (ACCOUNT|WAREHOUSE|DATABASE|SCHEMA|TABLE)(?: (.+?))? TO ROLE \w+( WITH GRANT OPTION)?$", x)
        if m:
            rows.append({"privilege": m[1], "granted_on": m[2], "name": LOCATOR if m[2] == "ACCOUNT" else m[3], "grant_option": "true" if m[4] else "false"})
    return rows


def test_the_verifier_expects_the_non_tls_flag_only_for_a_declared_local_redirect():
    check = lambda rows, redirect, allow: verify_registration(_grants(), rows, CEILING, NAMES, account=LOCATOR, redirect_uri=redirect, allow_local_dev_redirect=allow)  # noqa: E731
    assert check(_rows(LOCAL, "true"), LOCAL, True).ok
    assert "oauth_allow_non_tls_redirect_uri" in check(_rows(LOCAL, "true"), LOCAL, False).integration_problems  # not declared: the flag is a finding
    assert "oauth_allow_non_tls_redirect_uri" in check(_rows(LOCAL, "false"), LOCAL, True).integration_problems  # declared but not set: also a finding
    assert check(_rows(PROD, "false"), PROD, True).ok  # an https redirect still expects the flag off, switch or not
    assert "oauth_allow_non_tls_redirect_uri" in check(_rows(PROD, "true"), PROD, True).integration_problems


# ---- through the service and the endpoints -----------------------------------------------------------------------------------


def test_registering_with_a_local_redirect_works_only_when_the_service_is_told_to_allow_it():
    service, *_ = build()
    registered = register(service, redirect_uri=LOCAL, allow_local_dev_redirect=True)
    assert registered.config.redirect_uri == LOCAL and registered.config.allow_local_dev_redirect is True


def _client(redirect, allow, suffix=None):
    service, account, coordinator, handoff, *_ = build()

    async def no_key():
        return None

    runtime = ProductRunRuntime(service=service, coordinator=coordinator, verifier=Verifier(), redirect_uri=redirect, egress_networks=("35.238.115.103/32",),
                                dns=lambda host: ["54.230.10.4"], allow_local_dev_redirect=allow, **({"suffix_factory": suffix} if suffix else {}))
    app = FastAPI()
    app.include_router(build_product_run_router(runtime, no_key))
    return TestClient(app)


BODY = {"database": "A", "tables": [{"schema": "S", "table": "T"}], "setup_users": ["U"]}


def test_the_script_endpoint_follows_the_runtime_switch_and_never_the_request():
    off = _client(LOCAL, allow=False).post(P + "/registration-script", json=BODY, headers=auth())
    assert off.status_code == 422 and off.json()["detail"] == "invalid_registration"
    on = _client(LOCAL, allow=True).post(P + "/registration-script", json={**BODY, "allow_local_dev_redirect": True}, headers=auth())
    assert on.status_code == 200 and "OAUTH_ALLOW_NON_TLS_REDIRECT_URI = TRUE" in "\n".join(on.json()["statements"])
    still_off = _client(PROD, allow=False).post(P + "/registration-script", json={**BODY, "allow_local_dev_redirect": True}, headers=auth())
    assert "NON_TLS" not in "\n".join(still_off.json()["statements"])  # a request field cannot turn it on


def test_the_registration_endpoint_follows_the_runtime_switch():
    assert _client(LOCAL, allow=False).post(P + "/registrations", json=REGISTER, headers=auth()).status_code == 422
    assert _client(LOCAL, allow=True).post(P + "/registrations", json=REGISTER, headers=auth()).status_code == 201


def test_a_pinned_suffix_makes_the_drafted_script_the_one_that_was_reviewed():
    client = _client(LOCAL, allow=True, suffix=lambda: "a1b2c3d4")
    first = client.post(P + "/registration-script", json=BODY, headers=auth()).json()
    second = client.post(P + "/registration-script", json=BODY, headers=auth()).json()
    assert first["reg_suffix"] == second["reg_suffix"] == "a1b2c3d4" and first["digest"] == second["digest"]
    assert ConnectScope("A", (("S", "T"),)).tables  # (the ceiling used above is valid)
