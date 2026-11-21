"""The offline Databricks draft driver (approval D, preparation only): fake session, scripted model, no network, no workspace, no model."""

from __future__ import annotations

import inspect
import itertools
from dataclasses import replace

import pytest
from tests.test_connect_certification_c1 import EMAIL, GOOD, Model, Session

from app.ontology_draft import databricks_draft_run as dr
from app.ontology_draft.evaluation import RunReport
from app.ontology_draft.facts import SchemaFacts
from app.ontology_draft.proposal import DraftError

BASE = dict(catalog="sales", schema="data", tables=["orders"])


def factory(log=None):
    def make():
        s = Session()
        if log is not None:
            log.append(s)
        return s

    return make


def go(tmp_path, **over):
    args = dict(allow_live=True, session_factory=factory(), model=Model(GOOD), out_dir=tmp_path / "drafts", contract_id="sales-dbx", **BASE) | over
    return dr.run(**args)


# ---- the explicit-live flag and injection ---------------------------------------------------------------------------------------


def test_not_live_does_nothing_and_never_touches_the_session_or_model(tmp_path):
    model, made = Model(GOOD), []
    out = go(tmp_path, allow_live=False, model=model, session_factory=factory(made))
    assert (out.code, out.ok) == ("refused_not_live", False) and not made and not model.prompts and not (tmp_path / "drafts").exists()


def test_the_command_refuses_without_the_flag_and_without_injection(tmp_path, capsys):
    argv = ["--catalog", "sales", "--schema", "data", "--tables", "orders", "--out-dir", str(tmp_path / "d")]
    assert dr.main(argv, session_factory=factory(), model=Model(GOOD)) == 2  # no --allow-live
    assert "--allow-live" in capsys.readouterr().err
    assert dr.main([*argv, "--allow-live"]) == 2  # flag given, but no injected session or model: no live wiring in this module
    assert "injection" in capsys.readouterr().err
    assert not (tmp_path / "d").exists()


def test_the_command_writes_a_draft_when_live_is_explicit_and_prints_no_values(tmp_path, capsys):
    argv = ["--allow-live", "--catalog", "sales", "--schema", "data", "--tables", "orders", "--contract-id", "sales-dbx", "--out-dir", str(tmp_path)]
    assert dr.main(argv, session_factory=factory(), model=Model(GOOD)) == 0
    seen = capsys.readouterr()
    assert "Draft written" in seen.out and EMAIL not in seen.out + seen.err


def test_the_module_takes_no_credential_argument():
    src = inspect.getsource(dr).lower()
    assert "api_key" not in src and "password" not in src and "secret=" not in src
    with pytest.raises(SystemExit):
        dr.main(["--allow-live", "--catalog", "c", "--schema", "s", "--tables", "t", "--api-key", "x"])


# ---- a draft, and never a registry file -----------------------------------------------------------------------------------------


def test_a_draft_is_written_and_never_a_registry_file(tmp_path):
    out = go(tmp_path)
    assert out.ok and out.code == "written" and out.model_calls == 1
    assert sorted(p.name for p in out.path.iterdir()) == ["REVIEW.md", "contract.draft.json", "ontology.candidate.json"]
    assert '"lifecycle": "draft"' in (out.path / "contract.draft.json").read_text()
    assert not any('"certified"' in p.read_text() for p in out.path.iterdir())
    assert (tmp_path / "drafts") in out.path.parents


@pytest.mark.parametrize("name", ["semantic_registry", "semantic_registry_sandbox", "contracts"])
def test_an_output_directory_inside_a_registry_root_is_refused(tmp_path, name):
    out = go(tmp_path, out_dir=tmp_path / name / "x")
    assert out.code == "out_dir_is_a_registry" and not out.ok and not (tmp_path / name).exists()


# ---- only the listed tables are read --------------------------------------------------------------------------------------------


def test_only_the_listed_table_is_read_by_every_statement(tmp_path):
    made = []
    out = go(tmp_path, session_factory=factory(made))
    assert out.tables_read == ("orders",)
    statements = [s for sess in made for s in sess.sql]
    assert statements and all("`sales`.`data`.`orders`" in s or "information_schema" in s for s in statements)
    assert all(s.startswith(("DESCRIBE", "SELECT", "EXPLAIN")) for s in statements)  # reads only


def test_a_statement_on_a_table_outside_the_list_is_stopped_before_the_session():
    seen = []

    class Spy:
        def execute(self, sql, params):
            seen.append(sql)

    guard = dr.GuardedSession(Spy(), "sales", "data", ["orders"], dr.Budget(dr.Caps(), lambda: 0.0), [])
    guard.execute("SELECT 1 FROM `sales`.`data`.`orders` LIMIT 1", {})
    for sql in ("SELECT * FROM `sales`.`data`.`payments`", "SELECT * FROM `other`.`data`.`orders`", "SELECT * FROM `sales`.`other`.`orders`"):
        with pytest.raises(DraftError) as e:
            guard.execute(sql, {})
        assert e.value.code == "table_outside_scope"
    assert seen == ["SELECT 1 FROM `sales`.`data`.`orders` LIMIT 1"]


def test_a_table_outside_the_list_in_the_facts_stops_the_run_with_nothing_written(tmp_path, monkeypatch):
    real = dr.introspect_session

    def wide(session, *, catalog, tables):
        intro = real(session, catalog=catalog, tables=tables)
        extra = intro.facts.tables[0].model_copy(update={"name": "payments"})
        return replace(intro, facts=SchemaFacts(tables=(*intro.facts.tables, extra)))

    monkeypatch.setattr(dr, "introspect_session", wide)
    model = Model(GOOD)
    out = go(tmp_path, model=model)
    assert out.code == "table_outside_scope" and "table_outside_scope" in out.stop_reasons
    assert not (tmp_path / "drafts").exists() and not model.prompts


@pytest.mark.parametrize("bad", [["*"], ["orders", "orders"], [], ["ALL TABLES"], ["or`ders"], ["a.b"]])
def test_a_wildcard_duplicate_or_empty_table_list_is_refused_before_anything_runs(tmp_path, bad):
    made = []
    out = go(tmp_path, tables=bad, session_factory=factory(made))
    assert out.code == "invalid_scope" and not made


# ---- caps -----------------------------------------------------------------------------------------------------------------------


def test_caps_are_bounded_at_three_calls_and_the_reply_limit():
    for kwargs in ({"max_model_calls": 4}, {"max_model_calls": 0}, {"max_reply_tokens": dr.MAX_REPLY_TOKENS + 1}, {"wall_clock_seconds": 0}):
        with pytest.raises(ValueError):
            dr.Caps(**kwargs)
    assert dr.main(["--allow-live", "--catalog", "c", "--schema", "s", "--tables", "t", "--max-model-calls", "9"], session_factory=factory(), model=Model(GOOD)) == 2


def test_the_fourth_model_call_is_refused_before_it_reaches_the_model():
    model = Model(GOOD)
    capped = dr.CappedModel(model, dr.Budget(dr.Caps(), lambda: 0.0))
    for _ in range(3):
        capped.complete_json(prompt="p", schema={}, max_tokens=10)
    with pytest.raises(DraftError) as e:
        capped.complete_json(prompt="p", schema={}, max_tokens=10)
    assert e.value.code == "model_call_cap" and len(model.prompts) == 3


def test_a_lower_call_cap_is_honoured():
    model = Model(GOOD)
    capped = dr.CappedModel(model, dr.Budget(dr.Caps(max_model_calls=1), lambda: 0.0))
    capped.complete_json(prompt="p", schema={}, max_tokens=10)
    with pytest.raises(DraftError):
        capped.complete_json(prompt="p", schema={}, max_tokens=10)


def test_the_reply_token_limit_is_clamped(tmp_path):
    seen = []

    class Tokens(Model):
        def complete_json(self, *, prompt, schema, max_tokens):
            seen.append(max_tokens)
            return super().complete_json(prompt=prompt, schema=schema, max_tokens=max_tokens)

    go(tmp_path, model=Tokens(GOOD), caps=dr.Caps(max_reply_tokens=123))
    assert seen == [123]


def test_a_run_past_the_wall_clock_stops_and_writes_nothing(tmp_path):
    ticks = itertools.count(0, 100)  # each reading advances 100 seconds
    out = go(tmp_path, clock=lambda: float(next(ticks)), caps=dr.Caps(wall_clock_seconds=150))
    assert out.code == "wall_clock_cap" and not out.ok and not (tmp_path / "drafts").exists()


# ---- stop rules -----------------------------------------------------------------------------------------------------------------


def test_an_injected_canary_in_the_output_stops_the_run_and_writes_nothing(tmp_path):
    reply = {**GOOD, "glossary": [{"term": "Revenue", "definition": "IGNORE-PREVIOUS-CANARY"}]}
    out = go(tmp_path, model=Model(reply), canaries=("IGNORE-PREVIOUS-CANARY",))
    assert out.code == "stopped" and "injection" in out.stop_reasons and not (tmp_path / "drafts").exists()


def test_a_secret_in_the_prompt_or_output_is_a_leak_stop(tmp_path):
    out = go(tmp_path, secrets=("Orders placed online",))  # the table comment goes to the model: treated as a secret here
    assert out.code == "stopped" and "leak" in out.stop_reasons and not (tmp_path / "drafts").exists()


def test_a_validator_escape_stops_the_run(tmp_path, monkeypatch):
    real = dr.run_case_full

    def escaping(case, model, ctx, **kw):
        report, result = real(case, model, ctx, **kw)
        return RunReport(case=report.case, run=0, escapes=("candidate outside the schema",)), result

    monkeypatch.setattr(dr, "run_case_full", escaping)
    out = go(tmp_path)
    assert out.code == "stopped" and "validator_escape" in out.stop_reasons and not (tmp_path / "drafts").exists()


def test_a_failed_explain_probe_stops_the_run(tmp_path):
    class Failing(Session):
        def execute(self, sql, params):
            if sql.startswith("EXPLAIN"):
                raise RuntimeError(f"PERMISSION_DENIED {EMAIL}")
            return super().execute(sql, params)

    out = go(tmp_path, session_factory=Failing)
    assert out.code == "stopped" and "explain_failed" in out.stop_reasons and not (tmp_path / "drafts").exists()


def test_a_model_failure_writes_nothing_and_output_holds_no_driver_text(tmp_path, capsys):
    class Down:
        def complete_json(self, **kw):
            raise TimeoutError(f"upstream said {EMAIL}")

    argv = ["--allow-live", "--catalog", "sales", "--schema", "data", "--tables", "orders", "--out-dir", str(tmp_path / "d")]
    assert dr.main(argv, session_factory=factory(), model=Down()) == 1
    seen = capsys.readouterr()
    assert EMAIL not in seen.out + seen.err and not (tmp_path / "d").exists()
