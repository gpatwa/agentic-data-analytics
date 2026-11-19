"""Which warehouse a tenant's governed run executes on: its own connection, or the pilot's, and never another tenant's."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.connect.gateway import ConnectionUnavailable
from app.connect.runtime_gateway import connected_gateway_resolver
from app.connect.store import ConnectionRecord, ConnectionStore, TenantSecretStore
from app.resilience.retry import CircuitBreaker
from app.runtime.pilot_wiring import GovernedRuntime

HOST = "dbc-1234abcd-5678.cloud.databricks.com"


def rec(tenant="ta", cid="c1", status="active", **over):
    base = dict(tenant_id=tenant, connection_id=cid, engine="databricks", host=f"{cid}-{HOST}", account=HOST, user="0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0",
                role="", warehouse="w", database="sales", default_schema="data", allowed_tables=("sales.data.orders",), auth_method="oauth_m2m",
                secret_name=f"databricks/{cid}", http_path="/sql/1.0/warehouses/abcdef0123456789", status=status)
    return ConnectionRecord(**{**base, **over})


def world(*records):
    store, secrets = ConnectionStore(), TenantSecretStore()
    for r in records:
        store.add(r)
        secrets.put(r.tenant_id, r.secret_name, "dose-OAUTH-secret-value-0123456789")
    built = []

    def builder(config, *, allowed_tables, connector=None, **o):
        built.append((config.client_id, config.server_hostname, tuple(allowed_tables)))
        return SimpleNamespace(dialect="databricks", host=config.server_hostname)

    import app.connect.gateway as g
    g.databricks_gateway = builder
    return store, secrets, built


@pytest.fixture(autouse=True)
def _restore():
    import app.connect.gateway as g
    from app.execution.warehouse_sessions import databricks_gateway as real
    yield
    g.databricks_gateway = real


def test_a_tenant_with_no_live_connection_uses_the_pilot_gateway():
    store, secrets, _ = world(rec(status="pending"), rec(cid="c2", status="removed"))
    assert connected_gateway_resolver(store, secrets)("ta") is None
    assert connected_gateway_resolver(store, secrets)("nobody") is None


def test_one_active_connection_gives_its_own_gateway_and_never_another_tenants():
    store, secrets, built = world(rec("ta", "c1"), rec("tb", "c2"))
    resolve = connected_gateway_resolver(store, secrets)
    assert resolve("ta").host == f"c1-{HOST}" and resolve("tb").host == f"c2-{HOST}"
    assert [b[1] for b in built] == [f"c1-{HOST}", f"c2-{HOST}"]


@pytest.mark.parametrize(("records", "code"), [
    ((rec(status="suspended"),), "connection_not_active"),
    ((rec("ta", "c1"), rec("ta", "c2")), "connection_ambiguous"),
])
def test_a_connected_tenant_fails_closed_never_back_to_the_pilot(records, code):
    store, secrets, _ = world(*records)
    with pytest.raises(ConnectionUnavailable) as caught:
        connected_gateway_resolver(store, secrets)("ta")
    assert caught.value.code == code


def runtime(resolver):
    return GovernedRuntime(control=SimpleNamespace(engine=None), evidence=None, gateway=SimpleNamespace(dialect="postgresql", name="pilot"), compiler=None,
                           contracts=None, contexts={"ta": (None, None), "other": (None, None)}, model_factory=None, plans=object(), results=object(), policy_values=object(), gateway_for_tenant=resolver)


def test_the_runtime_uses_the_resolved_gateway_with_a_breaker_per_tenant_and_the_pilot_otherwise():
    own = SimpleNamespace(dialect="databricks", name="own")
    rt = runtime(lambda tenant: own if tenant == "ta" else None)
    gateway, breaker = rt._gateway("ta")
    assert gateway is own and isinstance(breaker, CircuitBreaker) and breaker is not rt.warehouse_breaker
    assert rt._gateway("ta")[1] is breaker  # the same breaker for the same tenant
    gateway, breaker = rt._gateway("other")
    assert gateway.name == "pilot" and breaker is rt.warehouse_breaker


def test_without_a_resolver_the_runtime_is_unchanged_and_a_resolver_error_propagates():
    assert runtime(None)._gateway("ta")[0].name == "pilot"

    def boom(tenant):
        raise ConnectionUnavailable("connection_not_active")

    with pytest.raises(ConnectionUnavailable):
        runtime(boom)._gateway("ta")


def test_control_totals_run_on_the_tenants_own_gateway(monkeypatch):
    own = SimpleNamespace(dialect="databricks", name="own")
    seen = {}
    monkeypatch.setattr("app.runtime.pilot_wiring.run_control_totals", lambda intent, contract, **kw: seen.update(gateway=kw["gateway"]) or {})
    rt = runtime(lambda tenant: own)
    rt.compiler = SimpleNamespace(compile=lambda i: None)
    rt.control_totals(SimpleNamespace(tenant_id="ta"), None, None)
    assert seen["gateway"] is own
