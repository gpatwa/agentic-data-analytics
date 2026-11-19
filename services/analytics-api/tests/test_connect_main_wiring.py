"""Connect on Databricks wired into the app's start-up (`app/main.py`): off by default, refuses unsafe settings, per-tenant registry
folders that cannot escape their root, and the setup routes sharing the governed runtime's identity check. Fakes only."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.connect.gateway import ConnectionUnavailable
from app.connect.runtime_wiring import build_databricks_wiring, registry_for_tenant
from app.connect.store import ConnectionRecord, TenantSecretStore
from app.connect.vault import KeyVaultSecretStore
from app.execution import DatabricksGateway
from app.runtime.pilot_wiring import TenantAssets

SANDBOX = Path(__file__).resolve().parent.parent / "semantic_registry_sandbox"
CATALOG = "compass_connect_spike_889cfc3a"
HOST = "dbc-1234abcd-5678.cloud.databricks.com"


def settings(root="", **over):
    base = dict(ENV="dev", ANALYTICS_V2_ENABLED=True, ANALYTICS_CONNECT_DATABRICKS_ENABLED=True, ANALYTICS_CONNECT_REGISTRY_ROOT=str(root),
                ANALYTICS_CONNECT_KEY_VAULT_URL=None)
    return SimpleNamespace(**{**base, **over})


@pytest.fixture
def root(tmp_path):
    shutil.copytree(SANDBOX, tmp_path / "sandbox")  # the owner's certified contract, for tenant `sandbox`
    return tmp_path


def test_off_by_default():
    assert build_databricks_wiring(settings(ANALYTICS_CONNECT_DATABRICKS_ENABLED=False)) is None


@pytest.mark.parametrize(("over", "text"), [
    ({"ANALYTICS_V2_ENABLED": False}, "ANALYTICS_V2_ENABLED"),
    ({"ENV": "prod", "ANALYTICS_CONNECT_KEY_VAULT_URL": "https://v.vault.azure.net"}, "not available in production"),
    ({"ANALYTICS_CONNECT_REGISTRY_ROOT": ""}, "ANALYTICS_CONNECT_REGISTRY_ROOT"),
    ({"ANALYTICS_CONNECT_REGISTRY_ROOT": "/no/such/folder"}, "ANALYTICS_CONNECT_REGISTRY_ROOT"),
])
def test_unsafe_settings_refuse_at_start_up(root, over, text):
    with pytest.raises(RuntimeError, match=text):
        build_databricks_wiring(settings(**({"ANALYTICS_CONNECT_REGISTRY_ROOT": str(root)} | over)))


def test_custody_is_a_vault_when_configured_and_the_stand_in_otherwise(root):
    assert isinstance(build_databricks_wiring(settings(root)).setups.secrets, TenantSecretStore)
    wiring = build_databricks_wiring(settings(root, ANALYTICS_CONNECT_KEY_VAULT_URL="https://v.vault.azure.net"), vault_client=object())
    assert isinstance(wiring.setups.secrets, KeyVaultSecretStore)


def test_a_tenants_registry_is_its_own_folder_and_cannot_escape_the_root(root, tmp_path):
    resolve = registry_for_tenant(root)
    assert resolve("sandbox") == (root / "sandbox").resolve()
    for bad in ("", ".", "..", "../sandbox", "sandbox/../sandbox", "/etc", "a/b", "nobody", "sand\x00box"):
        assert resolve(bad) is None
    (root.parent / "outside").mkdir(exist_ok=True)
    (root / "link").symlink_to(root.parent / "outside")
    assert resolve("link") is None  # a link out of the root does not count


def test_the_resolver_serves_the_certified_sandbox_contract_through_the_connection(root, monkeypatch):
    seen = {}
    monkeypatch.setattr("app.connect.gateway.databricks_gateway",
                        lambda config, *, allowed_tables, connector=None, **o: seen.setdefault("g", DatabricksGateway(lambda: None, allowed_tables=allowed_tables)))
    wiring = build_databricks_wiring(settings(root))
    assert wiring.assets_for_tenant("sandbox") is None  # no connection yet: the pilot's assets
    record = ConnectionRecord(tenant_id="sandbox", connection_id="dbx-889cfc3a", engine="databricks", host=HOST, account=HOST,
                              user="0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0", role="", warehouse="w", database=CATALOG, default_schema="data",
                              allowed_tables=(f"{CATALOG}.data.orders",), auth_method="oauth_m2m", secret_name="databricks/sandbox",
                              http_path="/sql/1.0/warehouses/abcdef0123456789", status="active")
    wiring.setups.store.add(record)
    wiring.setups.secrets.put("sandbox", "databricks/sandbox", "dose-OAUTH-secret-value-0123456789")
    assets = wiring.assets_for_tenant("sandbox")
    assert isinstance(assets, TenantAssets) and assets.compiler.adapter.dialect == "databricks"
    wiring.setups.store.set_status("sandbox", "dbx-889cfc3a", "suspended")
    with pytest.raises(ConnectionUnavailable):
        wiring.assets_for_tenant("sandbox")


def test_a_connection_with_no_certified_contract_for_its_tenant_fails_closed(root):
    wiring = build_databricks_wiring(settings(root))
    record = ConnectionRecord(tenant_id="other", connection_id="c", engine="databricks", host=HOST, account=HOST, user="u", role="", warehouse="w",
                              database=CATALOG, default_schema="data", allowed_tables=(f"{CATALOG}.data.orders",), auth_method="oauth_m2m",
                              secret_name="databricks/other", http_path="/sql/1.0/warehouses/abcdef0123456789", status="active")
    wiring.setups.store.add(record)
    wiring.setups.secrets.put("other", "databricks/other", "dose-OAUTH-secret-value-0123456789")
    with pytest.raises(ConnectionUnavailable):
        wiring.assets_for_tenant("other")


def test_start_up_hands_the_connected_resolver_and_the_identity_check_to_the_routes(root, monkeypatch):
    import app.main as main
    import app.runtime.pilot_wiring as pw

    captured = {}
    verifier, resolver = object(), object()

    def fake_build(s, **kw):
        captured.update(kw)
        return SimpleNamespace(service=object(), verifier=verifier, feedback=object(), resolver=resolver, briefs=object(),
                               governed=SimpleNamespace(contexts={"tenant-demo": 1}))

    class Svc:
        async def start(self): pass
        async def close(self): pass

    for k, v in dict(ANALYTICS_V2_ENABLED=True, ANALYTICS_CONNECT_DATABRICKS_ENABLED=True, ANALYTICS_CONNECT_REGISTRY_ROOT=str(root),
                     ANALYTICS_DEMO_ENABLED=False).items():
        monkeypatch.setattr(main.settings, k, v)
    monkeypatch.setattr(pw, "build_pilot_runtime", fake_build)
    monkeypatch.setattr(main, "analytics_service", Svc())
    monkeypatch.setattr(main.v2_runtime, "service", None)

    async def run():
        async with main.lifespan(main.app):
            pass

    asyncio.run(run())
    assert callable(captured["assets_for_tenant"])
    rt = main.databricks_runtime
    assert rt.setups is not None and rt.session_factory is not None and rt.verifier is verifier and rt.resolver is resolver
    assert rt.setups.store is not None and captured["assets_for_tenant"]("sandbox") is None  # nothing connected yet
    rt.setups = rt.session_factory = rt.verifier = rt.resolver = None


def test_disabled_start_up_leaves_the_routes_inert_and_passes_no_resolver(monkeypatch):
    import app.main as main
    import app.runtime.pilot_wiring as pw

    captured = {}

    def fake_build(s, **kw):
        captured.update(kw)
        return SimpleNamespace(service=object(), verifier=object(), feedback=object(), resolver=None, briefs=object(),
                               governed=SimpleNamespace(contexts={}))

    class Svc:
        async def start(self): pass
        async def close(self): pass

    monkeypatch.setattr(main.settings, "ANALYTICS_V2_ENABLED", True)
    monkeypatch.setattr(main.settings, "ANALYTICS_CONNECT_DATABRICKS_ENABLED", False)
    monkeypatch.setattr(main.settings, "ANALYTICS_DEMO_ENABLED", False)
    monkeypatch.setattr(pw, "build_pilot_runtime", fake_build)
    monkeypatch.setattr(main, "analytics_service", Svc())
    monkeypatch.setattr(main.v2_runtime, "service", None)

    async def run():
        async with main.lifespan(main.app):
            pass

    asyncio.run(run())
    assert captured["assets_for_tenant"] is None and main.databricks_runtime.setups is None
