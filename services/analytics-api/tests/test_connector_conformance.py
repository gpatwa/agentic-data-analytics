"""Connector contract and conformance suite (plan C1): capability records, gating, the generated table, and import hygiene."""

import dataclasses
from pathlib import Path

import pytest
from tests.test_warehouse_validation import (  # noqa: F401 - spec_for is a fixture
    DB_ENV,
    SF_ENV,
    FakeSession,
    World,
    spec_for,
)

from app.execution import connector_conformance as cc
from app.execution import warehouse_validation as wv

DOC = Path(__file__).resolve().parents[3] / "docs" / "WAREHOUSE_CONNECTOR_CAPABILITIES.md"
CHECKS = ["read_only_grants", "metadata_read", "compiled_query", "result_equivalence", "cost_ceiling", "timeout", "cancel", "no_leaks"]


def caps(**changes):
    return dataclasses.replace(cc.CAPABILITIES["snowflake"], **changes)


# ------------------------------------------------------------------------------------------------- the capability record


def test_both_engines_have_a_capability_record_that_claims_a_way_to_stop_a_statement():
    assert set(cc.CAPABILITIES) == {"snowflake", "databricks"}
    for record in cc.CAPABILITIES.values():
        assert record.statement_timeout or record.cancel
        assert record.cost_estimate and record.read_only_proof and record.verified_live


def test_an_engine_that_cannot_stop_a_statement_is_refused_at_construction():
    with pytest.raises(ValueError, match="stop a statement"):
        caps(statement_timeout=False, cancel=False)


@pytest.mark.parametrize("field", ["cost_estimate", "read_only_proof"])
def test_the_cost_estimate_and_the_read_only_proof_are_mandatory(field):
    with pytest.raises(ValueError, match="mandatory"):
        caps(**{field: "  "})


# ------------------------------------------------------------------------------------------------- the generated table


def test_the_checked_in_capability_table_is_exactly_what_the_code_generates():
    assert DOC.read_text() == cc.capability_table_markdown(), "regenerate: python3 -m app.execution.connector_conformance --print-table"


def test_the_table_covers_every_engine_and_every_capability_and_holds_no_secret():
    text = cc.capability_table_markdown()
    for needle in ("snowflake", "databricks", "Statement timeout", "Cancel", "Cost estimate (mandatory)", "Read-only proof (mandatory)", "Verified live"):
        assert needle in text
    for secret in (*SF_ENV.values(), *DB_ENV.values()):
        assert secret not in text


def test_the_print_table_command_writes_the_same_text(capsys):
    assert cc.main(["--print-table"]) == 0
    assert capsys.readouterr().out == cc.capability_table_markdown()


# ------------------------------------------------------------------------------------------------- gating by claimed capability


def with_caps(spec, **changes):
    return dataclasses.replace(spec, capabilities=dataclasses.replace(spec.capabilities, **changes))


def test_a_cancel_the_connector_does_not_claim_is_skipped_and_said_so_while_a_claimed_one_must_pass(spec_for):  # noqa: F811
    spec, _ = spec_for("snowflake", cancel_ignored=True)  # the fake's cancel never stops the statement
    claimed = cc.run_conformance(spec)
    assert (claimed.checks[-1].name, claimed.checks[-1].code, claimed.passed) == ("cancel", "finished", False)

    spec, _ = spec_for("snowflake", cancel_ignored=True)
    unclaimed = cc.run_conformance(with_caps(spec, cancel=False))
    by_name = {c.name: c for c in unclaimed.checks}
    assert unclaimed.passed and [c.name for c in unclaimed.checks] == CHECKS
    assert (by_name["cancel"].ok, by_name["cancel"].code) == (True, "not_claimed")
    text = cc.render_markdown(unclaimed)
    assert "| cancel | not claimed | - |" in text and "all checks ok" in text


def test_a_statement_timeout_the_connector_does_not_claim_is_skipped_too(spec_for):  # noqa: F811
    spec, _ = spec_for("databricks", no_timeout=True)
    assert cc.run_conformance(spec).checks[-1].code == "no_timeout"
    spec, _ = spec_for("databricks", no_timeout=True)
    report = cc.run_conformance(with_caps(spec, statement_timeout=False))
    assert report.passed and {c.name: c.code for c in report.checks}["timeout"] == "not_claimed"


@pytest.mark.parametrize(
    ("option", "stopped_at"),
    [({"grants": []}, "read_only_grants"), ({"estimate": 0.0}, "cost_ceiling"), ({"estimate": 99.0}, "compiled_query")],
)
def test_the_mandatory_checks_cannot_be_skipped_by_any_capability_claim(spec_for, option, stopped_at):  # noqa: F811
    spec, _ = spec_for("snowflake", **option)
    report = cc.run_conformance(with_caps(spec, statement_timeout=True, cancel=True))
    assert report.checks[-1].name == stopped_at and not report.passed


def test_a_spec_can_bring_its_own_slow_statement(spec_for):  # noqa: F811
    spec, world = spec_for("snowflake")
    report = cc.run_conformance(dataclasses.replace(spec, slow_sql="SELECT SYSTEM$WAIT(60) /* custom */"))
    assert report.passed and any("custom" in sql for sql in world.log)  # the timeout and cancel checks used the spec's statement


# ------------------------------------------------------------------------------------------------- the library stays a library


def test_the_conformance_module_has_no_live_guards_provisioning_or_credentials():
    import ast

    tree = ast.parse(Path(cc.__file__).read_text())
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any("warehouse_provisioning" in m or "warehouse_validation" in m for m in imported)
    assert "os" not in imported  # no environment access: credentials belong to the live runner
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not any(v.startswith(("SNOWFLAKE_", "DATABRICKS_")) or v == "--allow-live" for v in strings)


def test_the_live_runner_reuses_the_library_instead_of_duplicating_it():
    assert wv.run_validation is cc.run_conformance and wv.EngineSpec is cc.ConformanceSpec
    source = Path(wv.__file__).read_text()
    assert "def run_validation" not in source and "class StopRule" not in source and "def _bounded" not in source


def test_the_live_specs_carry_their_capability_records():
    assert wv.build_spec("snowflake", SF_ENV).capabilities is cc.CAPABILITIES["snowflake"]
    assert wv.build_spec("databricks", DB_ENV).capabilities is cc.CAPABILITIES["databricks"]
