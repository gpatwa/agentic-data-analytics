"""C1 of the certification plan: a connected Databricks workspace drafted through its principal, and a certified contract assembled
into one tenant's governed-run assets. Fakes only. The `certified` documents here are test fixtures standing in for the owner's
certification; nothing in the product certifies anything."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.connect.gateway import ConnectionUnavailable
from app.connect.store import ConnectionRecord, ConnectionStore, TenantSecretStore
from app.connect.tenant_assets import connected_assets_resolver, contract_problem
from app.execution import DatabricksGateway, ExecutionLimits
from app.ontology_draft import PROMPT_VERSION, DraftContext, draft_ontology
from app.ontology_draft.databricks_source import (
    databricks_explain_probe,
    infer_keys,
    introspect_session,
    profile_session,
    qualified_prefix,
)
from app.ontology_draft.proposal import DraftError
from app.runtime.pilot_wiring import GovernedRuntime, TenantAssets
from packages.platform_contracts.analytics_intent import AnalyticalIntent, IntentMetric, SemanticContractReference

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
HOST = "dbc-1234abcd-5678.cloud.databricks.com"
APP = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
PATH = "/sql/1.0/warehouses/abcdef0123456789"
EMAIL = "alice@example.com"


# ---- a fake Databricks session -------------------------------------------------------------------------------------------------

DATA = {
    "orders": {
        "columns": [("id", "bigint", None), ("amount", "decimal(12,2)", "Order total"), ("status", "string", None), ("customer_email", "string", None)],
        "rows": [(i, (i + 1) * 7, "paid" if i % 5 else "refunded", EMAIL if i % 2 else "bob@example.org") for i in range(30)],
        "comment": "Orders placed online",
    },
}


class Stream:
    def __init__(self, rows):
        self._rows, self.columns = list(rows), ()

    def fetchmany(self, n):
        out, self._rows = self._rows[:n], self._rows[n:]
        return out

    def close(self):
        pass


class Session:
    def __init__(self, fail=None):
        self.sql, self.fail, self.closed, self.prepared = [], fail, False, None

    def prepare(self, *, timeout_seconds):
        self.prepared = timeout_seconds

    def cancel(self):
        pass

    def close(self):
        self.closed = True

    def execute(self, sql, params):
        self.sql.append(sql)
        if self.fail and self.fail in sql:
            raise RuntimeError(f"PERMISSION_DENIED row value {EMAIL}")  # a driver message that quotes data
        if sql.startswith("DESCRIBE TABLE EXTENDED"):
            table = re.search(r"`([^`]+)`$", sql)[1]
            d = DATA[table]
            rows = [(n, t, c) for n, t, c in d["columns"]] + [("", "", ""), ("# Detailed Table Information", "", ""), ("Comment", d["comment"], "")]
            return Stream(rows)
        if sql.startswith("EXPLAIN "):
            return Stream([("== Physical Plan ==",)])
        table = re.search(r"`([^`]+)`(?:\)| w|$)", sql.split(" FROM ", 1)[1].replace("(SELECT * FROM ", "").replace("(SELECT ", ""))
        names = [c[0] for c in DATA["orders"]["columns"]]
        rows = DATA["orders"]["rows"]
        if sql.startswith("SELECT count(*), "):
            out = [len(rows)]
            for fn, col in re.findall(r"(count\(DISTINCT|count|min|max)\(?\s*w\.`(\w+)`", sql):
                i = names.index(col)
                vals = [r[i] for r in rows]
                out.append({"count(DISTINCT": len(set(vals)), "count": len(vals), "min": min(vals), "max": max(vals)}[fn])
            return Stream([tuple(out)])
        m = re.match(r"SELECT `(\w+)`, count\(\*\) AS n", sql)
        if m:
            i = names.index(m[1])
            tally = {}
            for r in rows:
                tally[r[i]] = tally.get(r[i], 0) + 1
            return Stream(sorted(tally.items(), key=lambda kv: (-kv[1], str(kv[0])))[:51])
        raise AssertionError(sql)


# ---- introspection and profiling -----------------------------------------------------------------------------------------------


def facts_and_profile(session=None):
    session = session or Session()
    intro = introspect_session(session, catalog="sales", tables=[("data", "orders")])
    profile = profile_session(session, intro.facts, catalog="sales", schema="data")
    return session, SimpleNamespace(facts=infer_keys(intro.facts, profile), skipped=intro.skipped), profile


def test_introspection_reads_columns_types_and_the_table_comment_only_for_the_named_tables():
    session, intro, _ = facts_and_profile()
    table = intro.facts.table("orders")
    assert table.asset_id == "sales.data.orders" and table.comment == "Orders placed online"
    assert {c.name: c.data_type for c in table.columns} == {"id": "integer", "amount": "decimal", "status": "string", "customer_email": "string"}
    describes = [s for s in session.sql if s.startswith("DESCRIBE")]
    assert describes == ["DESCRIBE TABLE EXTENDED `sales`.`data`.`orders`"]


def test_the_profile_applies_the_redaction_rule_and_no_row_value_reaches_a_serialised_profile():
    _, _, profile = facts_and_profile()
    table = profile.table("orders")
    assert table.column("amount").min_value == "7" and table.column("amount").max_value == "210"
    assert table.column("customer_email").sample_status != "none" and not table.column("customer_email").samples  # a sensitive name: no samples
    assert table.column("status").samples == ("paid",)  # "refunded" occurs in 6 rows, under the cohort floor of 10: dropped by the rule
    assert EMAIL not in profile.model_dump_json() and EMAIL not in repr(profile)


def test_profiling_is_bounded_and_runs_only_selects_under_a_timeout():
    session, _, _ = facts_and_profile()
    assert session.prepared and all(s.startswith(("DESCRIBE", "SELECT")) for s in session.sql)
    from app.ontology_draft import ProfileLimits

    limits = [int(n) for s in session.sql if s.startswith("SELECT") for n in re.findall(r"LIMIT (\d+)", s)]
    assert limits and all(n in (ProfileLimits().row_limit, 51) for n in limits)  # the window, and the sample tally cap


def test_a_database_error_is_one_fixed_code_without_the_drivers_message():
    for fail, code in (("DESCRIBE", "introspection_failed"), ("SELECT count(*)", "profile_failed")):
        with pytest.raises(DraftError) as caught:
            facts_and_profile(Session(fail=fail))
        assert caught.value.code == code and EMAIL not in str(caught.value) and caught.value.__suppress_context__ is True


@pytest.mark.parametrize("tables", [[("a", "x"), ("b", "y")], [("data", "bad`name")], [("data", "")]])
def test_more_than_one_schema_or_a_bad_name_is_refused_before_any_statement_runs(tables):
    session = Session()
    with pytest.raises(DraftError):
        introspect_session(session, catalog="sales", tables=tables)
    assert session.sql == []


def test_a_key_is_inferred_only_from_a_fully_seen_table_with_unique_non_null_values():
    session = Session()
    raw = introspect_session(session, catalog="sales", tables=[("data", "orders")]).facts
    profile = profile_session(session, raw, catalog="sales", schema="data")
    assert raw.table("orders").primary_key == () and infer_keys(raw, profile).table("orders").primary_key == ("id",)
    assert infer_keys(raw, profile, row_limit=30).table("orders").primary_key == ()  # the window may not have covered the table


# ---- drafting for Databricks --------------------------------------------------------------------------------------------------

GOOD = {
    "datasets": [{"table": "orders", "display_name": "Orders", "description": "One row per order."}],
    "metrics": [{"id": "revenue", "table": "orders", "aggregation": "sum", "measure_column": "amount", "grain_key_columns": ["id"], "description": "Total order amount."}],
    "dimensions": [{"id": "status", "table": "orders", "column": "status", "dimension_type": "categorical", "description": "Order status."}],
    "glossary": [{"term": "Revenue", "definition": "Sum of order amounts."}],
}


class Model:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def complete_json(self, *, prompt, schema, max_tokens):
        self.prompts.append(prompt)
        return self.reply


def draft(tenant="ta", reply=None, probe=None):
    session, intro, profile = facts_and_profile()
    ctx = DraftContext(tenant_id=tenant, domain="sales", contract_id="sales-dbx", owner_id="team.owners", owner_name="Owners", model_id="fake",
                       prompt_version=PROMPT_VERSION, dialect="databricks", physical_prefix=qualified_prefix("sales", "data"))
    model = Model(reply or GOOD)
    return draft_ontology(intro.facts, model, ctx, profile=profile, explain=probe, now=NOW), model, session


def test_a_databricks_draft_uses_qualified_physical_names_compiles_for_databricks_and_probes_with_explain():
    probes = []
    probe_session = Session()
    probe = databricks_explain_probe(lambda: probe_session)
    result, model, _ = draft(probe=lambda sql, params: (probes.append(sql), probe(sql, params))[1])
    assert result.dropped == () and result.document.lifecycle == "draft"
    assert {d.physical_name for d in result.document.contract.datasets} == {"sales.data.orders"}
    assert probes and all("`sales`.`data`.`orders`" in s or "sales.data.orders" in s.replace("`", "") for s in probes)
    assert all("`" in s for s in probes)  # compiled for Databricks (backtick quoting), not PostgreSQL
    assert all(s.startswith("EXPLAIN ") for s in probe_session.sql) and probe_session.closed
    assert EMAIL not in model.prompts[0]  # only the redacted view went to the model


def test_a_probe_failure_drops_the_candidate_by_class_name_only():
    def probe(sql, params):
        raise RuntimeError(f"PERMISSION_DENIED {EMAIL}")

    result, _, _ = draft(probe=probe)
    assert any(d.code.startswith("explain_failed:RuntimeError") for d in result.dropped) and EMAIL not in repr(result.dropped)


def test_the_pilots_defaults_are_unchanged():
    ctx = DraftContext(tenant_id="t", domain="d", contract_id="c", owner_id="o", owner_name="O", model_id="m", prompt_version="p")
    assert ctx.dialect == "postgresql" and ctx.physical_prefix == ""


# ---- the certified contract and the tenant's assets ---------------------------------------------------------------------------


def certified(result, tenant="ta", version="1.0.0"):
    """A TEST FIXTURE standing in for the owner's certification in Git."""
    doc = result.document
    contract = doc.contract.model_copy(update={"tenant_id": tenant, "version": version})
    return doc.model_copy(update={"lifecycle": "certified", "contract": contract})


def registry(tmp_path, *documents):
    for i, doc in enumerate(documents):
        (tmp_path / f"c{i}.json").write_text(doc.model_dump_json())
    return tmp_path


def rec(tenant="ta", status="active", tables=("sales.data.orders",)):
    return ConnectionRecord(tenant_id=tenant, connection_id=f"c-{tenant}", engine="databricks", host=HOST, account=HOST, user=APP, role="", warehouse="w",
                            database="sales", default_schema="data", allowed_tables=tables, auth_method="oauth_m2m", secret_name=f"databricks/{tenant}",
                            http_path=PATH, status=status)


@pytest.fixture
def world(monkeypatch):
    store, secrets = ConnectionStore(), TenantSecretStore()
    session = Session()
    monkeypatch.setattr("app.connect.gateway.databricks_gateway",
                        lambda config, *, allowed_tables, connector=None, **o: DatabricksGateway(lambda: session, allowed_tables=allowed_tables))

    def add(**kw):
        r = rec(**kw)
        store.add(r)
        secrets.put(r.tenant_id, r.secret_name, "dose-OAUTH-secret-value-0123456789")

    return store, secrets, add, session


def test_a_certified_contract_inside_the_ceiling_assembles_the_tenants_assets(world, tmp_path):
    store, secrets, add, _ = world
    add()
    root = registry(tmp_path, certified(draft()[0]))
    assets = connected_assets_resolver(store, secrets, lambda t: root)("ta")
    assert isinstance(assets, TenantAssets) and assets.compiler.adapter.dialect == "databricks" and assets.gateway.dialect == "databricks"
    assert assets.context is not None and assets.contracts is not None


def test_no_live_connection_means_the_pilots_assets(world, tmp_path):
    store, secrets, add, _ = world
    assert connected_assets_resolver(store, secrets, lambda t: tmp_path)("ta") is None


@pytest.mark.parametrize(("docs", "code"), [
    ((), "no_certified_contract"),  # nothing certified at all
    (("draft",), "no_certified_contract"),  # a draft never counts
    (("other_tenant",), "no_certified_contract"),  # another tenant's contract is not ours
    (("outside",), "contract_outside_ceiling"),
    (("two",), "contract_ambiguous"),
])
def test_anything_missing_or_wrong_fails_closed_with_a_stable_code(world, tmp_path, docs, code):
    store, secrets, add, _ = world
    add()
    result = draft()[0]
    made = []
    for kind in docs:
        if kind == "draft":
            made.append(result.document)
        elif kind == "other_tenant":
            made.append(certified(result, tenant="tb"))
        elif kind == "outside":
            doc = certified(result)
            datasets = [d.model_copy(update={"physical_name": "sales.data.payroll_outside"}) for d in doc.contract.datasets]
            made.append(doc.model_copy(update={"contract": doc.contract.model_copy(update={"datasets": datasets})}))
        else:
            made += [certified(result, version="1.0.0"), certified(result, version="1.0.1")]
    root = registry(tmp_path, *made)
    with pytest.raises(ConnectionUnavailable) as caught:
        connected_assets_resolver(store, secrets, lambda t: root)("ta")
    assert caught.value.code == code


def test_a_missing_registry_and_a_suspended_connection_fail_closed(world, tmp_path):
    store, secrets, add, _ = world
    add()
    with pytest.raises(ConnectionUnavailable) as caught:
        connected_assets_resolver(store, secrets, lambda t: None)("ta")
    assert caught.value.code == "no_certified_contract"
    store.set_status("ta", "c-ta", "suspended")
    with pytest.raises(ConnectionUnavailable) as caught:
        connected_assets_resolver(store, secrets, lambda t: tmp_path)("ta")
    assert caught.value.code == "connection_not_active"


def test_contract_problem_checks_lifecycle_tenant_and_ceiling():
    result = draft()[0]
    assert contract_problem(certified(result), rec()) is None
    assert contract_problem(result.document, rec()) == "contract_not_certified"
    assert contract_problem(certified(result, tenant="tb"), rec()) == "contract_tenant_mismatch"
    assert contract_problem(certified(result), rec(tables=("sales.data.other",))) == "contract_outside_ceiling"


def test_a_governed_question_compiles_for_databricks_and_runs_only_inside_the_allowlist(world, tmp_path):
    store, secrets, add, session = world
    add()
    root = registry(tmp_path, certified(draft()[0]))
    assets = connected_assets_resolver(store, secrets, lambda t: root)("ta")
    intent = AnalyticalIntent(query_id="q1", tenant_id="ta", dataset_id="orders", semantic_contract=SemanticContractReference(contract_id="sales-dbx", contract_version="1.0.0"),
                              metrics=[IntentMetric(metric_id="revenue")], limit=10)
    plan = assets.compiler.compile(intent)
    assert plan.dialect == "databricks" and "sales" in plan.sql and "orders" in plan.sql
    limits = ExecutionLimits(timeout_seconds=5, max_rows=10, max_cost_units=100)
    session.explain_cost = lambda sql, params: 1.0
    session.execute = lambda sql, params: Stream([])  # type: ignore[method-assign]
    assets.gateway.execute(plan, limits=limits)  # the table the contract names is inside the admin's allowlist
    other = SimpleNamespace(dialect="databricks", sql="SELECT * FROM sales.data.payroll_outside", parameters={})
    from app.execution import QueryRejected

    with pytest.raises(QueryRejected):
        assets.gateway.execute(other, limits=limits)


# ---- the runtime uses the tenant's assets -------------------------------------------------------------------------------------------


def runtime(resolver):
    pilot = SimpleNamespace(dialect="postgresql", name="pilot")
    return GovernedRuntime(control=SimpleNamespace(engine=None), evidence=None, gateway=pilot, compiler="pilot-compiler", contracts="pilot-contracts",
                           contexts={"other": ("pilot-context", "pilot-ontology")}, model_factory=None, vocabularies={"other": "pilot-vocab"},
                           plans=object(), results=object(), policy_values=object(), assets_for_tenant=resolver)


def test_the_runtime_uses_a_tenants_own_assets_and_the_pilots_for_everyone_else():
    own = TenantAssets(gateway=SimpleNamespace(dialect="databricks"), compiler="dbx-compiler", contracts="dbx-contracts", context="dbx-context",
                       ontology="dbx-ontology", vocabulary="dbx-vocab")
    rt = runtime(lambda t: own if t == "ta" else None)
    t = rt._tenant("ta")
    assert (t.compiler, t.contracts, t.context, t.ontology, t.vocabulary) == ("dbx-compiler", "dbx-contracts", "dbx-context", "dbx-ontology", "dbx-vocab")
    assert t.breaker is not rt.warehouse_breaker and rt._tenant("ta").breaker is t.breaker
    p = rt._tenant("other")
    assert (p.compiler, p.contracts, p.context, p.vocabulary, p.gateway.name) == ("pilot-compiler", "pilot-contracts", "pilot-context", "pilot-vocab", "pilot")
    assert p.breaker is rt.warehouse_breaker


def test_an_assembly_error_propagates_so_the_run_fails_closed():
    def boom(tenant):
        raise ConnectionUnavailable("no_certified_contract")

    with pytest.raises(ConnectionUnavailable):
        runtime(boom)._tenant("ta")
