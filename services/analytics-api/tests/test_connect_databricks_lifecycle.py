"""Secret custody over time: the vault adapter, the gateway cache, rotation, expiry and the health check. Fakes only."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from tests.test_connect_databricks_api import APP_ID, HOST, PATH, SECRET, Session, auth, begin, build, finish

from app.connect import databricks_registration as reg
from app.connect.gateway import ConnectionUnavailable
from app.connect.runtime_gateway import connected_gateway_resolver
from app.connect.store import ConnectionRecord, ConnectionStore, TenantSecretStore
from app.connect.vault import KeyVaultSecretStore, VaultUnavailable

NEW = "second-OAUTH-secret-value-9876543210-do-not-leak"


# ---- the vault adapter ----------------------------------------------------------------------------------------------------------


class ResourceNotFoundError(Exception):
    pass


class FakeVault:
    """The SDK's method names: set_secret, get_secret, begin_delete_secret."""

    def __init__(self, fail=False):
        self.items, self.fail, self.n = {}, fail, 0

    def set_secret(self, name, value, **kw):
        if self.fail:
            raise RuntimeError(f"vault down {value}")
        self.n += 1
        self.items[name] = (value, f"v{self.n}")
        return SimpleNamespace(properties=SimpleNamespace(version=f"v{self.n}"), name=name)

    def get_secret(self, name):
        if self.fail:
            raise RuntimeError(f"vault down {name}")
        if name not in self.items:
            raise ResourceNotFoundError(name)
        value, version = self.items[name]
        return SimpleNamespace(value=value, properties=SimpleNamespace(version=version))

    def begin_delete_secret(self, name):
        if self.fail:
            raise RuntimeError(f"vault down {name}")
        if name not in self.items:
            raise ResourceNotFoundError(name)
        del self.items[name]
        return SimpleNamespace(wait=lambda: None)


def test_the_vault_adapter_has_the_stores_interface_and_scopes_by_tenant():
    client = FakeVault()
    v = KeyVaultSecretStore(client)
    v1 = v.put("ta", "databricks/c1", SECRET)
    assert v.read("ta", "databricks/c1") == SECRET and v.exists("ta", "databricks/c1") and v.version("ta", "databricks/c1") == v1
    with pytest.raises(KeyError):
        v.read("tb", "databricks/c1")  # another tenant: a different vault name
    assert not v.exists("tb", "databricks/c1")
    assert v.put("ta", "databricks/c1", NEW) != v1
    names = list(client.items)
    assert len(names) == 1 and "ta" not in names[0] and "databricks" not in names[0] and SECRET not in names[0]
    receipt = v.delete("ta", "databricks/c1")
    assert receipt.tenant_id == "ta" and not client.items
    with pytest.raises(KeyError):
        v.delete("ta", "databricks/c1")


def test_a_vault_failure_is_one_fixed_word_with_no_vault_text_and_a_value_is_never_printed():
    v = KeyVaultSecretStore(FakeVault(fail=True))
    for call in (lambda: v.put("ta", "n", SECRET), lambda: v.read("ta", "n"), lambda: v.exists("ta", "n"), lambda: v.version("ta", "n"), lambda: v.delete("ta", "n")):
        with pytest.raises(VaultUnavailable) as caught:
            call()
        assert str(caught.value) == "vault_unavailable" and caught.value.__suppress_context__ is True  # the vault's own error is hidden
    assert "ta" not in repr(v) and SECRET not in repr(v)
    with pytest.raises(ValueError):
        KeyVaultSecretStore(FakeVault(), prefix="bad prefix!")


def test_the_whole_connect_flow_works_with_the_vault_adapter_in_place_of_the_memory_store():
    client = FakeVault()
    vault = KeyVaultSecretStore(client)

    def factory(record):
        assert vault.read(record.tenant_id, record.secret_name) == SECRET  # the session reads custody from the vault
        return Session()

    http, setups, store, _memory, chain, _ = build(factory=factory)
    setups.secrets = vault
    view = begin(http)
    assert finish(http, view).json()["ok"] is True
    assert len(client.items) == 1 and SECRET in next(iter(client.items.values()))[0]
    assert http.delete(f"/api/v2/connect/databricks/connections/{view['connection_id']}", headers=auth()).status_code == 200
    assert not client.items  # removal deleted it from the vault


# ---- the gateway cache ----------------------------------------------------------------------------------------------------------


def rec(**over):
    base = dict(tenant_id="ta", connection_id="c1", engine="databricks", host=HOST, account=HOST, user=APP_ID, role="", warehouse="w", database="sales",
                default_schema="data", allowed_tables=("sales.data.orders",), auth_method="oauth_m2m", secret_name="databricks/c1", http_path=PATH, status="active")
    return ConnectionRecord(**{**base, **over})


@pytest.fixture
def built(monkeypatch):
    made = []
    monkeypatch.setattr("app.connect.gateway.databricks_gateway", lambda config, *, allowed_tables, connector=None, **o: made.append(config.client_secret) or SimpleNamespace(dialect="databricks"))
    return made


def test_a_gateway_is_reused_until_the_ttl_the_secret_changes_or_the_connection_stops_being_active(built):
    store, secrets = ConnectionStore(), TenantSecretStore()
    secrets.put("ta", "databricks/c1", SECRET)
    store.add(rec())
    now = [0.0]
    resolve = connected_gateway_resolver(store, secrets, ttl_seconds=100, clock=lambda: now[0])
    a = resolve("ta")
    assert resolve("ta") is a and len(built) == 1
    now[0] = 101
    assert resolve("ta") is not a and len(built) == 2  # the TTL ended
    secrets.put("ta", "databricks/c1", NEW)  # rotation: a new version
    c = resolve("ta")
    assert len(built) == 3 and built[-1] == NEW and resolve("ta") is c
    store.set_status("ta", "c1", "suspended")
    with pytest.raises(ConnectionUnavailable):
        resolve("ta")  # a cached gateway never outlives suspension
    store.set_status("ta", "c1", "removed")
    assert resolve("ta") is None


def test_a_secret_that_vanished_is_refused_even_with_a_cached_gateway(built):
    store, secrets = ConnectionStore(), TenantSecretStore()
    secrets.put("ta", "databricks/c1", SECRET)
    store.add(rec())
    resolve = connected_gateway_resolver(store, secrets)
    resolve("ta")
    secrets.delete("ta", "databricks/c1")
    with pytest.raises(ConnectionUnavailable) as caught:
        resolve("ta")
    assert caught.value.code == "credential_unavailable"


# ---- rotation, expiry, health ---------------------------------------------------------------------------------------------------


def connected(fail_on=None):
    http, setups, store, secrets, chain, sessions = build()
    view = begin(http)
    assert finish(http, view).json()["ok"]
    return http, setups, store, secrets, chain, view["connection_id"]


def test_a_new_connection_records_when_its_secret_expires():
    http, setups, store, secrets, chain, cid = connected()
    expires = store.get("ta", cid).secret_expires_at
    assert expires is not None and abs((expires - datetime.now(timezone.utc)).total_seconds() - reg.SECRET_LIFETIME_SECONDS) < 60


def test_the_listing_flags_a_connection_that_needs_rotation_and_shows_no_secret():
    http, setups, store, secrets, chain, cid = connected()
    listed = http.get("/api/v2/connect/databricks/connections", headers=auth()).json()
    assert listed[0]["connection_id"] == cid and listed[0]["needs_rotation"] is False and listed[0]["expires_in_days"] >= 89
    assert SECRET not in json.dumps(listed)
    store.update(store.get("ta", cid).__class__(**{**store.get("ta", cid).__dict__, "secret_expires_at": datetime.now(timezone.utc) + timedelta(days=3)}))
    assert http.get("/api/v2/connect/databricks/connections", headers=auth()).json()[0]["needs_rotation"] is True


def test_rotation_replaces_the_secret_only_after_the_new_one_is_proven(monkeypatch):
    http, setups, store, secrets, chain, cid = connected()
    name = store.get("ta", cid).secret_name
    old_version = secrets.version("ta", name)

    def factory(record):
        assert secrets.read("ta", record.secret_name) == NEW  # the check ran with the new value
        return Session()

    result = setups.rotate("ta", cid, actor_id="lead-a", client_secret=NEW, session_factory=factory)
    assert result.ok and secrets.read("ta", name) == NEW and secrets.version("ta", name) != old_version
    assert store.get("ta", cid).status == "active"
    assert "connect.credential_rotated" in [e.event_type for e in chain.events] and NEW not in json.dumps([e.payload for e in chain.events])


def test_a_failed_rotation_puts_the_old_secret_back_and_leaves_the_connection_as_it_was():
    http, setups, store, secrets, chain, cid = connected()
    name = store.get("ta", cid).secret_name
    result = setups.rotate("ta", cid, actor_id="lead-a", client_secret=NEW, session_factory=lambda r: Session(fail_on="`orders`"))
    assert not result.ok and result.code == "connection_test_failed"
    assert secrets.read("ta", name) == SECRET and store.get("ta", cid).status == "active"


def test_the_rotate_endpoint_refuses_bad_input_by_code_and_never_echoes_the_secret():
    http, setups, store, secrets, chain, cid = connected()
    url = f"/api/v2/connect/databricks/connections/{cid}/secret"
    for body, code in (({"client_secret": "short"}, "invalid_secret"), ({"client_secret": "has a space 0123456789012345"}, "invalid_secret"), ({"other": NEW}, "invalid_request")):
        r = http.post(url, json=body, headers=auth())
        assert r.status_code == 422 and r.json() == {"detail": code} and NEW not in r.text
    assert http.post(url, json={"client_secret": NEW}, headers=auth("usr-a")).status_code == 403
    assert http.post(url, json={"client_secret": NEW}, headers=auth("mgr-b")).status_code == 404


def test_a_health_check_suspends_a_connection_that_stops_working_and_restores_it_when_it_works():
    http, setups, store, secrets, chain, cid = connected()
    bad = setups.health_check("ta", cid, actor_id="lead-a", session_factory=lambda r: Session(fail_on="`orders`"))
    assert not bad.ok and store.get("ta", cid).status == "suspended"
    good = setups.health_check("ta", cid, actor_id="lead-a", session_factory=lambda r: Session())
    assert good.ok and store.get("ta", cid).status == "active"
    assert [e.event_type for e in chain.events].count("connect.health_checked") == 2


def test_an_expired_secret_suspends_the_connection_without_calling_the_warehouse():
    http, setups, store, secrets, chain, cid = connected()
    called = []
    result = setups.health_check("ta", cid, actor_id="lead-a", session_factory=lambda r: called.append(1) or Session(),
                                 now=datetime.now(timezone.utc) + timedelta(days=365))
    assert not result.ok and not called and store.get("ta", cid).status == "suspended"


def test_a_suspended_connection_can_be_recovered_by_rotation():
    http, setups, store, secrets, chain, cid = connected()
    setups.health_check("ta", cid, actor_id="lead-a", session_factory=lambda r: Session(fail_on="`orders`"))
    assert store.get("ta", cid).status == "suspended"
    assert setups.rotate("ta", cid, actor_id="lead-a", client_secret=NEW, session_factory=lambda r: Session()).ok
    assert store.get("ta", cid).status == "active"


def test_the_store_update_is_tenant_bound():
    store = ConnectionStore()
    store.add(rec())
    store.update(rec(status="suspended"))
    assert store.get("ta", "c1").status == "suspended"
    with pytest.raises(KeyError):
        store.update(rec(tenant_id="tb"))
