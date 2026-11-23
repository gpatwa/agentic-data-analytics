"""Warming a connected warehouse before the first question, with fakes only: no warehouse, no network. Nothing here claims a live run."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from reference_stack import synthetic_views_contract as sv

from app.api_v2 import V2Runtime, build_v2_router
from app.compiler import CertifiedIntentCompiler, DatabricksCompilerAdapter
from app.connect.store import ConnectionRecord, ConnectionStore
from app.connect.warmup import Warmer
from app.connect.warmup_service import MAX_STATEMENTS, WARM_LIMITS, WarmUpService, warm_plans
from app.context.registry_context import CertifiedContracts
from app.runtime.analyze_service import ServiceError
from app.security import AuthenticationError
from app.semantic_registry import SemanticRegistry
from packages.platform_contracts.security import AnalyticsIdentity

ROOT = sv.CERTIFIED.parents[1]
TENANT = "synthetic"


def _assets(gateway):
    registry = SemanticRegistry(ROOT)
    docs = [e.document for e in registry.list_entries() if e.document is not None and e.document.lifecycle == "certified" and e.document.contract.id == sv.CONTRACT_ID]
    return SimpleNamespace(gateway=gateway, compiler=CertifiedIntentCompiler(ROOT, DatabricksCompilerAdapter()), contracts=CertifiedContracts(docs))


class Gateway:
    def __init__(self):
        self.plans, self.limits = [], []

    def execute(self, plan, *, limits, cancellation=None):
        self.plans.append(plan)
        self.limits.append(limits)


def _record(tenant=TENANT, cid="c1", warm=True, status="active", engine="databricks", host="h.cloud.databricks.com"):
    return ConnectionRecord(tenant_id=tenant, connection_id=cid, engine=engine, host=host, account="", user="app-id", role="", warehouse="", database="dev",
                            default_schema="s", allowed_tables=("dev.s.orders_current",), auth_method="oauth_m2m", secret_name="sec", http_path="/sql/1.0/warehouses/x",
                            status=status, warm_up=warm)


def _service(records, gateway=None, assets=None, audit=None, now=None, **kw):
    store = ConnectionStore()
    for r in records:
        store.add(r)
    gw = gateway or Gateway()
    t = now if now is not None else [0.0]
    svc = WarmUpService(store, assets or (lambda tenant: _assets(gw)), warmer=Warmer(clock=lambda: t[0]), submit=kw.pop("submit", lambda f: f()), audit=audit, **kw)
    return svc, gw, t


def test_the_statements_are_the_certified_no_dimension_aggregates_two_per_dataset_and_capped():
    plans = warm_plans(_assets(Gateway()), TENANT)
    assert 1 <= len(plans) <= MAX_STATEMENTS
    assert all("GROUP BY" not in p.sql.upper() for p in plans)
    assert len({p.sql for p in plans}) == len(plans)
    assert [p.sql for p in warm_plans(_assets(Gateway()), TENANT)] == [p.sql for p in plans]  # the same choice every time


def test_off_by_default_nothing_reaches_the_warehouse():
    svc, gw, _ = _service([_record(warm=False)])
    assert svc.request(TENANT) == "skipped_disabled" and gw.plans == []


def test_on_it_runs_the_statements_through_the_tenants_own_gateway_and_records_only_a_code_count_and_duration():
    seen = []
    svc, gw, t = _service([_record()], audit=lambda tenant, cid, r: seen.append((tenant, cid, r)))
    assert svc.request(TENANT) == "warming"
    assert len(gw.plans) == len(warm_plans(_assets(gw), TENANT)) and all(lim == WARM_LIMITS for lim in gw.limits)
    [(tenant, cid, result)] = seen
    assert (tenant, cid, result.code, result.statements_run) == (TENANT, "c1", "warmed", len(gw.plans))
    assert not hasattr(result, "sql")


def test_a_second_request_inside_the_idle_window_runs_nothing_and_a_later_one_runs_again():
    svc, gw, t = _service([_record()])
    assert svc.request(TENANT) == "warming"
    n = len(gw.plans)
    t[0] = 60.0
    assert svc.request(TENANT) == "skipped_recent" and len(gw.plans) == n  # calling it in a loop cannot keep a warehouse awake
    t[0] = 10_000.0
    assert svc.request(TENANT) == "warming" and len(gw.plans) == 2 * n


def test_only_one_active_databricks_connection_with_the_switch_on_is_warmed():
    for records in ([_record(status="suspended")], [_record(), _record(cid="c2", host="h2.cloud.databricks.com")], [_record(status="pending")]):
        svc, gw, _ = _service(records)
        assert svc.request(TENANT) == "skipped_disabled" and gw.plans == []
    svc, gw, _ = _service([_record(tenant="other")])
    assert svc.request(TENANT) == "skipped_disabled"  # another tenant's connection is never used


def test_a_failure_in_assets_or_in_the_warehouse_is_a_stable_code_and_never_raises():
    seen = []

    def broken(_tenant):
        raise RuntimeError("password=hunter2 in a driver message")

    svc, gw, _ = _service([_record()], assets=broken, audit=lambda *a: seen.append(a[2]))
    assert svc.request(TENANT) == "warming" and [r.code for r in seen] == ["failed"]
    assert "hunter2" not in repr(seen)

    class Failing(Gateway):
        def execute(self, plan, *, limits, cancellation=None):
            raise RuntimeError("secret in message")

    seen.clear()
    svc, gw, t = _service([_record()], gateway=Failing(), audit=lambda *a: seen.append(a[2]))
    assert svc.request(TENANT) == "warming" and [r.code for r in seen] == ["failed"]
    assert svc.request(TENANT) == "warming"  # a failed warm-up does not count, so the next call tries again


def test_at_most_four_warm_ups_can_be_queued_at_once():
    queued = []
    svc, _gw, _ = _service([_record(cid=f"c{i}", host=f"h{i}.cloud.databricks.com") for i in range(1)], submit=queued.append)
    store = svc.store
    for i in range(1, 6):
        store.add(_record(tenant=f"t{i}", cid=f"x{i}", host=f"x{i}.cloud.databricks.com"))
    codes = [svc.request(t) for t in (TENANT, "t1", "t2", "t3", "t4", "t5")]
    assert codes.count("warming") == 4 and codes.count("skipped_busy") == 2


def test_on_question_warms_when_idle_and_otherwise_only_marks_the_connection_busy():
    svc, gw, t = _service([_record()])
    svc.on_question(TENANT)
    assert gw.plans  # idle: the fallback warmed
    n = len(gw.plans)
    t[0] = 10_000.0
    svc.warmer.note_activity(TENANT, "c1")
    svc.on_question(TENANT)
    assert len(gw.plans) == n  # a connection in use is not warmed
    svc2, gw2, _ = _service([_record(warm=False)])
    svc2.on_question(TENANT)
    assert gw2.plans == []
    boom = WarmUpService(SimpleNamespace(list=lambda tenant: (_ for _ in ()).throw(RuntimeError("db down"))), lambda t: None)
    boom.on_question(TENANT)  # never raises into the question
    assert boom.request(TENANT) == "failed"


# ---- the API ---------------------------------------------------------------------------------------------------------------------


IDENT = {"a": AnalyticsIdentity(tenant_id=TENANT, user_id="u", groups=[]), "b": AnalyticsIdentity(tenant_id="tb", user_id="v", groups=[])}


class Verifier:
    def verify(self, token):
        try:
            return IDENT[token]
        except KeyError as exc:
            raise AuthenticationError("bad") from exc


class Service:
    def __init__(self):
        self.called = 0

    def analyze(self, who, **kw):
        self.called += 1
        raise ServiceError(409, "stop_here")


def _client(warm):
    service = Service()
    app = FastAPI()
    app.include_router(build_v2_router(V2Runtime(service=service, verifier=Verifier(), warm=warm), lambda: None))
    return TestClient(app), service


def _h(t="a"):
    return {"Authorization": f"Bearer {t}"}


def test_the_warm_call_answers_at_once_with_a_code_and_takes_the_tenant_from_the_identity():
    svc, gw, t = _service([_record()])
    client, _ = _client(svc)
    r = client.post("/api/v2/analytics/warm", headers=_h())
    assert r.status_code == 202 and r.json() == {"code": "warming"} and gw.plans
    r = client.post("/api/v2/analytics/warm", headers=_h())
    assert r.status_code == 200 and r.json() == {"code": "skipped_recent"}
    assert client.post("/api/v2/analytics/warm", headers=_h("b")).json() == {"code": "skipped_disabled"}  # another tenant: nothing to learn
    assert client.post("/api/v2/analytics/warm").status_code == 401
    assert _client(None)[0].post("/api/v2/analytics/warm", headers=_h()).json() == {"code": "skipped_disabled"}


def test_analyze_warms_first_as_a_fallback_and_a_warm_up_error_never_breaks_the_question():
    svc, gw, t = _service([_record()])
    client, service = _client(svc)
    r = client.post("/api/v2/analytics/analyze", json={"purpose": "p", "request_text": "how many orders"}, headers=_h())
    assert gw.plans and service.called == 1 and r.status_code == 409  # the question went on to the service
    broken = WarmUpService(SimpleNamespace(list=lambda tenant: (_ for _ in ()).throw(RuntimeError("db down"))), lambda t: None)
    client, service = _client(broken)
    client.post("/api/v2/analytics/analyze", json={"purpose": "p", "request_text": "how many orders"}, headers=_h())
    assert service.called == 1


def test_a_metric_that_needs_policy_values_is_skipped_the_next_is_tried_and_none_means_nothing_runs():
    from app.compiler.postgres import CompilationError

    assets = _assets(Gateway())
    real = assets.compiler
    base = warm_plans(assets, TENANT)
    documents = assets.contracts.documents()
    metrics = [m for d in documents for m in d.contract.metrics]
    victim = next(m for m in metrics if sum(1 for x in metrics if x.dataset_id == m.dataset_id) > 2)  # a dataset with a spare metric
    refuse = {victim.id}

    def compile_(intent, *, policy_values=None):
        if intent.metrics[0].metric_id in refuse:
            raise CompilationError("missing policy value for required filter f1")
        return real.compile(intent, policy_values=policy_values)

    assets.compiler = SimpleNamespace(compile=compile_)
    plans = warm_plans(assets, TENANT)
    assert len(plans) == len(base)  # the dataset's next metric took the refused one's place
    svc, _, _ = _service([_record()], assets=lambda tenant: assets)
    assert svc.request(TENANT) == "warming" and len(assets.gateway.plans) == len(base)  # the rest still warm
    # nothing compiles: a stable code, no statement, and the idle slot is not claimed
    seen = []
    refuse.update(m.id for m in metrics)
    assets.gateway.plans.clear()
    svc, _, _ = _service([_record()], assets=lambda tenant: assets, audit=lambda *a: seen.append(a[2]))
    assert svc.request(TENANT) == "warming" and assets.gateway.plans == [] and [r.code for r in seen] == ["skipped_none"]
    assert svc.request(TENANT) == "warming"  # tries again next time


def test_the_cors_list_allows_the_connection_admin_calls():
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    origin = settings.cors_origins[0] if settings.cors_origins and settings.cors_origins != ["*"] else "http://localhost:5173"
    for method in ("PUT", "DELETE"):
        r = TestClient(app).options("/api/v2/connect/databricks/connections/x/warm-up", headers={"Origin": origin, "Access-Control-Request-Method": method,
                                                                                                  "Access-Control-Request-Headers": "authorization,content-type"})
        assert r.status_code == 200 and method in r.headers["access-control-allow-methods"], method
