"""E1: where a table's key came from is a fact, shown to the reviewer, and not a gate beyond the existing one."""

from __future__ import annotations

import pytest

from app.ontology_draft.emit import KEY_EVIDENCE_NOTES
from app.ontology_draft.facts import FactColumn, FactTable, SchemaFacts


def table(**kw):
    return FactTable(name="orders", asset_id="s.orders", columns=(FactColumn(name="id", data_type="integer"), FactColumn(name="amount", data_type="decimal")), **kw)


def test_a_key_given_without_a_source_is_declared_and_no_key_is_none():
    assert table(primary_key=("id",)).key_evidence == "declared" and table().key_evidence == "none"


def test_a_key_and_its_evidence_must_agree():
    with pytest.raises(ValueError):
        table(primary_key=("id",), key_evidence="none")
    with pytest.raises(ValueError):
        table(key_evidence="profiled_full")


def test_tested_is_not_accepted_yet():
    with pytest.raises(ValueError):
        table(primary_key=("id",), key_evidence="tested")


def test_fingerprints_of_facts_without_a_profiled_key_are_unchanged_and_a_profiled_key_changes_it():
    declared = SchemaFacts(tables=(table(primary_key=("id",)),))
    profiled = SchemaFacts(tables=(table(primary_key=("id",), key_evidence="profiled_full"),))
    assert declared.fingerprint() != profiled.fingerprint()
    import hashlib
    import json
    legacy = declared.model_dump(mode="json")
    for t in legacy["tables"]:
        del t["key_evidence"]
        for col in t["columns"]:
            del col["restricted"]
    assert declared.fingerprint() == hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_every_evidence_level_has_a_note_for_the_reviewer():
    assert set(KEY_EVIDENCE_NOTES) == {"declared", "profiled_full", "none"}


def test_a_profiled_key_is_labelled_profiled_full_and_the_review_note_says_what_it_proves(tmp_path):
    from tests.test_ontology_draft_synonyms import draft, reply

    from app.ontology_draft.emit import write_draft

    result, _, _ = draft(reply=reply({}))
    assert result.provenance["keys"], "the draft records the key behind each table in use"
    note = (write_draft(result, tmp_path, report=None, skipped_columns=(), source_note="t") / "REVIEW.md").read_text()
    assert "The key behind each table's metrics" in note and "evidence **" in note


def test_infer_keys_labels_what_it_picks():
    from tests.test_connect_certification_c1 import Session, infer_keys, introspect_session, profile_session

    session = Session()
    raw = introspect_session(session, catalog="sales", tables=[("data", "orders")]).facts
    profile = profile_session(session, raw, catalog="sales", schema="data")
    picked = infer_keys(raw, profile).table("orders")
    assert picked.primary_key == ("id",) and picked.key_evidence == "profiled_full" and raw.table("orders").key_evidence == "none"


# ---- E2: declared keys on Databricks ----------------------------------------------------------------------------------------------


def _intro(keys=None, fail=None):
    from tests.test_connect_certification_c1 import Session, introspect_session

    session = Session(fail=fail)
    session.declared_keys = keys or {}
    return session, introspect_session(session, catalog="sales", tables=[("data", "orders")]).facts.table("orders")


def test_a_declared_primary_key_is_read_labelled_declared_and_needs_no_profile():
    session, t = _intro({"orders": ("id",)})
    assert t.primary_key == ("id",) and t.key_evidence == "declared"
    key_sql = [s for s in session.sql if "information_schema" in s]
    assert len(key_sql) == 1 and key_sql[0].startswith("SELECT") and "'orders'" in key_sql[0] and "'data'" in key_sql[0]


def test_a_composite_declared_key_keeps_its_order():
    assert _intro({"orders": ("id", "status")})[1].primary_key == ("id", "status")


def test_no_declared_key_is_none_and_the_profile_can_still_supply_one():
    _, t = _intro({})
    assert t.primary_key == () and t.key_evidence == "none"


def test_a_key_naming_a_column_the_facts_do_not_hold_is_dropped_not_guessed():
    assert _intro({"orders": ("id", "skipped_col")})[1].primary_key == ()
    assert _intro({"orders": ("id", "id")})[1].primary_key == ()


def test_a_failed_key_read_gives_no_key_and_leaks_nothing():
    _, t = _intro({"orders": ("id",)}, fail="information_schema")
    assert t.primary_key == () and t.key_evidence == "none"


def test_a_table_over_the_profile_window_gets_a_key_only_when_declared():
    from tests.test_connect_certification_c1 import Session, infer_keys, introspect_session, profile_session

    for keys, expected in (({"orders": ("id",)}, ("id",)), ({}, ())):
        session = Session()
        session.declared_keys = keys
        raw = introspect_session(session, catalog="sales", tables=[("data", "orders")]).facts
        profile = profile_session(session, raw, catalog="sales", schema="data")
        assert infer_keys(raw, profile, row_limit=30).table("orders").primary_key == expected  # the window (30) is smaller than the table


def test_identifiers_in_the_key_query_are_checked():
    from app.ontology_draft.databricks_source import _literal
    from app.ontology_draft.proposal import DraftError

    with pytest.raises(DraftError):
        _literal("x'; DROP TABLE t; --")
