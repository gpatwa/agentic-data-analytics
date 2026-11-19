"""C2 (draft) and C4 (ask) runners against a fake workspace, a fake session and a fake model. No network, no workspace, no model."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from tests.test_connect_certification_c1 import DATA, GOOD, Model, Stream
from tests.test_connect_certification_c1 import Session as BaseSession
from tests.test_databricks_registration import Fake

from app.connect.e2e import databricks_certify as dc
from app.connect.e2e import databricks_spike as sp
from app.execution import DatabricksGateway

HOST = "dbc-1234abcd-5678.cloud.databricks.com"
SUFFIX = "0a1b2c3d"
N = sp.Names.for_run(SUFFIX)


class UuidFake(Fake):
    """The registration fake, with application ids shaped like Databricks' (UUIDs)."""

    def _create(self, display_name):
        self.n += 1
        p = SimpleNamespace(id=f"p{self.n}", application_id=f"0b1c2d3e-4f50-6172-8394-a5b6c7d8e{self.n:03d}", display_name=display_name)
        self.principals[p.id] = p
        return p


WID = "abcdef0123456789"


class WorkspaceSession(BaseSession):
    """The c1 fake session, plus the statements the connection test and a compiled metric run."""

    def __init__(self, deny_outside=True):
        super().__init__()
        self.explain_cost = lambda sql, params: 1.0
        self.cancel = lambda: None

    def execute(self, sql, params):
        low = sql.lower()
        if sql.startswith("SELECT 1 FROM"):
            self.sql.append(sql)
            return Stream([(1,)])
        if sql.startswith("SELECT") and "sum(" in low and "describe" not in low and "count(*), " not in sql:
            self.sql.append(sql)
            return Stream([(3255,)])
        return super().execute(sql, params)


def build(tmp_path, *, rotate=True, model=None, registry=None, fail=None):
    fake = UuidFake()
    sessions: list[WorkspaceSession] = []

    def factory_for(secrets):
        def factory(record):
            assert secrets.read(record.tenant_id, record.secret_name)  # the secret comes from custody
            s = WorkspaceSession()
            s.fail = fail
            sessions.append(s)
            return s

        return factory

    run = dc.Certify(fake, WID, "Starter", HOST, N, factory_for, model=model, out_dir=tmp_path / "drafts", registry=registry, dns=lambda h: ["34.1.2.3"], rotate=rotate)
    return fake, run, sessions


@pytest.fixture(autouse=True)
def _gateway(monkeypatch):
    monkeypatch.setattr("app.connect.gateway.databricks_gateway",
                        lambda config, *, allowed_tables, connector=None, **o: DatabricksGateway(lambda: WorkspaceSession(), allowed_tables=allowed_tables))


def certify_from_draft(draft_dir, registry):
    """A TEST FIXTURE standing in for the owner's certification (C3). Nothing in the product does this."""
    from packages.platform_contracts.semantic import SemanticRegistryDocument

    doc = SemanticRegistryDocument.model_validate_json((draft_dir / "contract.draft.json").read_text())
    contract = doc.contract.model_copy(update={"version": "1.0.0"})
    registry.mkdir(parents=True, exist_ok=True)
    (registry / "contract.json").write_text(doc.model_copy(update={"lifecycle": "certified", "contract": contract}).model_dump_json())


# ---- C2 ------------------------------------------------------------------------------------------------------------------------


def test_draft_stage_drafts_through_the_connections_session_writes_a_draft_and_leaves_nothing(tmp_path):
    fake, run, sessions = build(tmp_path, model=Model(GOOD))
    result = run.run("draft")
    steps = {o["step"]: o for o in result["outcomes"]}
    assert result["stopped"] == "" and steps["connection_active"]["result"] == "allowed"
    assert "keys_inferred=1" in steps["introspect_profile"]["detail"]
    assert steps["draft"]["result"] == "allowed" and "metrics=1" in steps["draft"]["detail"] and "leaks=0" in steps["draft"]["detail"]
    written = list((tmp_path / "drafts").rglob("contract.draft.json"))
    assert len(written) == 1 and "draft" in written[0].read_text()
    assert not list((tmp_path / "drafts").rglob("*certified*"))  # a draft is never written as certified
    assert result["teardown"]["left"] == [] and result["sweep"] == {"catalogs": 0, "principals": 0, "warehouse_entries": 0}
    assert not fake.principals and not fake.secrets and not fake.catalogs
    assert result["leaks"]["clean"] and result["audit_chain_verifies"]
    assert not any(v in json.dumps(result) for v in ("oauth-secret",))


def test_the_draft_stage_reads_only_the_first_ceiling_table_through_select_and_describe(tmp_path):
    fake, run, sessions = build(tmp_path, model=Model(GOOD))
    run.run("draft")
    used = [s for sess in sessions for s in sess.sql]
    assert any(s.startswith("DESCRIBE TABLE EXTENDED") and "`orders`" in s for s in used)
    assert not any("payroll_outside" in s for s in used)  # nothing outside the ceiling is ever read
    assert all(s.startswith(("DESCRIBE", "SELECT", "EXPLAIN")) for s in used)


def test_without_a_model_the_draft_stage_stops_cleanly_and_still_tears_down(tmp_path):
    fake, run, _ = build(tmp_path, model=None)
    result = run.run("draft")
    assert result["stopped"] == "model_not_configured" and not fake.principals and not fake.catalogs


def test_a_model_that_proposes_nothing_usable_is_reported_not_written(tmp_path):
    fake, run, _ = build(tmp_path, model=Model({"datasets": [], "metrics": [], "dimensions": [], "glossary": []}))
    result = run.run("draft")
    assert result["stopped"] == "no_draft" and not list((tmp_path / "drafts").rglob("*.json")) and result["teardown"]["left"] == []


def test_a_failed_explain_probe_stops_the_draft_run_and_nothing_is_written(tmp_path):
    fake, run, _ = build(tmp_path, model=Model(GOOD), fail="EXPLAIN")
    result = run.run("draft")
    assert result["stopped"] == "stop_rule:explain_failed" and not list((tmp_path / "drafts").rglob("*.json")) and result["teardown"]["left"] == []


def test_a_secret_that_reaches_the_draft_files_is_caught_by_the_scanner(tmp_path):
    leaky = {**GOOD, "datasets": [{**GOOD["datasets"][0], "description": "Orders. Reference SECRET-API-KEY-VALUE-0000 for support."}]}
    fake, run, _ = build(tmp_path, model=Model(leaky))
    run.scanner.watch("model_api_key", "SECRET-API-KEY-VALUE-0000")
    result = run.run("draft")
    assert result["leaks"]["clean"] is False and result["leaks"]["findings"][0]["secret"] == "model_api_key"


# ---- C4 ------------------------------------------------------------------------------------------------------------------------


def test_ask_stage_answers_from_the_certified_contract_checks_the_value_and_exercises_the_failure_cases(tmp_path):
    fake, run, _ = build(tmp_path, model=Model(GOOD))
    run.run("draft")
    registry = tmp_path / "registry"
    certify_from_draft(next((tmp_path / "drafts").rglob("contract.draft.json")).parent, registry)
    fake2, ask, _ = build(tmp_path, registry=registry)
    result = ask.run("ask")
    steps = {o["step"]: o for o in result["outcomes"]}
    assert result["stopped"] == "", result["stopped"]
    assert steps["assets_assembled"]["result"] == "allowed"
    assert steps["answer"]["result"] == "allowed" and "value=3255" in steps["answer"]["detail"] and "expected=3255" in steps["answer"]["detail"]
    assert steps["outside_allowlist"]["result"] == "denied"
    assert steps["suspended_connection"] == {"step": "suspended_connection", "result": "denied", "detail": "connection_not_active"}
    assert steps["rotation"]["result"] == "allowed" and steps["answer_after_rotation"]["result"] == "allowed"
    assert steps["connection_removed"]["result"] == "allowed"
    assert result["teardown"]["left"] == [] and result["sweep"] == {"catalogs": 0, "principals": 0, "warehouse_entries": 0}
    assert not fake2.principals and not fake2.secrets and result["leaks"]["clean"]


def test_ask_stage_fails_closed_without_a_certified_contract_and_never_uses_the_draft(tmp_path):
    fake, run, _ = build(tmp_path, model=Model(GOOD))
    run.run("draft")
    drafts = next((tmp_path / "drafts").rglob("contract.draft.json")).parent
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "draft.json").write_text((drafts / "contract.draft.json").read_text())  # a draft placed in the registry is still not certified
    fake2, ask, _ = build(tmp_path, registry=registry)
    result = ask.run("ask")
    assert result["stopped"] == "unavailable:no_certified_contract" and not [o for o in result["outcomes"] if o["step"] == "answer"]
    assert not fake2.principals and result["teardown"]["left"] == []


def test_a_wrong_answer_is_reported_as_a_problem_not_hidden(tmp_path, monkeypatch):
    fake, run, _ = build(tmp_path, model=Model(GOOD))
    run.run("draft")
    registry = tmp_path / "registry"
    certify_from_draft(next((tmp_path / "drafts").rglob("contract.draft.json")).parent, registry)

    class Wrong(WorkspaceSession):
        def execute(self, sql, params):
            if sql.startswith("SELECT") and "sum(" in sql.lower() and "count(*), " not in sql:
                return Stream([(1,)])
            return super().execute(sql, params)

    monkeypatch.setattr("app.connect.gateway.databricks_gateway", lambda config, *, allowed_tables, connector=None, **o: DatabricksGateway(lambda: Wrong(), allowed_tables=allowed_tables))
    fake2, ask, _ = build(tmp_path, registry=registry)
    steps = {o["step"]: o for o in ask.run("ask")["outcomes"]}
    assert steps["answer"]["result"] == "problem:wrong_answer"


def test_the_ask_stage_needs_a_registry(tmp_path):
    fake, ask, _ = build(tmp_path)
    assert ask.run("ask")["stopped"] == "registry_not_given" and not fake.principals


# ---- the command line and the guard ---------------------------------------------------------------------------------------------


def test_the_command_line_refuses_without_the_live_flag_a_bad_host_or_a_missing_registry(monkeypatch):
    base = ["--stage", "ask", "--expect-host", HOST, "--suffix", SUFFIX, "--warehouse", "Starter"]
    assert dc.main(base) == 2
    assert dc.main([*base, "--allow-live"]) == 2  # no --registry
    assert dc.main(["--stage", "draft", "--allow-live", "--expect-host", "evil.example.com", "--suffix", SUFFIX, "--warehouse", "w"]) == 2
    for name in dc.MODEL_ENV:
        monkeypatch.delenv(name, raising=False)
    assert dc.main(["--stage", "draft", "--allow-live", "--expect-host", HOST, "--suffix", SUFFIX, "--warehouse", "Starter"]) == 2  # no model settings


def test_the_dev_tool_is_never_imported_by_the_app():
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    assert [p.name for p in app.rglob("*.py") if "connect/e2e" not in p.as_posix() and re.search(r"databricks_certify", p.read_text())] == []


_unused = (DATA, SimpleNamespace)


def test_the_statements_document_equals_the_generator():
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "docs/design/CONNECT_CERTIFICATION_STATEMENTS.md"
    assert path.read_text() == dc.render_markdown(), "regenerate with render_markdown()"


def test_the_explain_stop_rule_matches_the_keys_a_report_really_has():
    from app.ontology_draft.draft_run import stop_reasons

    report = SimpleNamespace(leaks=0, escapes=(), injections=0, drops={"explain_failed": 2})
    assert stop_reasons(report, remote=True) == ["explain_failed"] and stop_reasons(report, remote=False) == []
