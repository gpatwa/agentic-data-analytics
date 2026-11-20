"""E3: a backlog of refused questions that keeps counts of salted word hashes and never a word."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from test_intent_coverage import ask, runtime

from app.runtime import refusal_backlog as rb
from app.runtime.intent_coverage import coverage_reference
from app.runtime.pilot_wiring import GovernedRuntime
from packages.platform_contracts.semantic_terms import tokens

SALT = b"s" * 32
OTHER = b"o" * 32


def ref(question, words):
    toks = tokens(question)
    return coverage_reference(question, [i for i, t in enumerate(toks) if t in words])


# ---- hashing ----------------------------------------------------------------------------------------------------------------------


def test_a_hash_is_keyed_stable_and_differs_by_salt_and_word():
    assert rb.term_hash(SALT, "refund") == rb.term_hash(SALT, "refund") != rb.term_hash(OTHER, "refund") != rb.term_hash(SALT, "refunds")
    with pytest.raises(ValueError):
        rb.term_hash(b"short", "refund")


def test_hashes_cover_stems_so_plurals_count_together_and_never_return_words():
    q = "Show revenue for refunded orders and refunds"
    hashes = rb.stem_hashes(q, [3, 6], SALT)  # "refunded" and "refunds"
    assert len(hashes) == 2 and not any(w in " ".join(hashes) for w in ("refund", "orders"))
    assert rb.stem_hashes("refunds and refund", [0, 2], SALT) == [rb.term_hash(SALT, "refund")]  # one stem, counted once
    assert rb.stem_hashes(q, [99, -1], SALT) == []


@pytest.mark.parametrize("bad", ["", "unaccounted_words:1@2", "nonsense", "unaccounted_words:1@x#abcdefabcdef"])
def test_a_malformed_reference_has_no_positions(bad):
    assert rb.positions_from_reference(bad) == []


# ---- stores -----------------------------------------------------------------------------------------------------------------------


@pytest.fixture(params=["memory", "sql"])
def store(request, tmp_path):
    if request.param == "memory":
        return rb.InMemoryBacklogStore()
    engine = create_engine(f"sqlite:///{tmp_path / 'b.db'}")
    rb.COUNTS.metadata.create_all(engine)
    return rb.SqlBacklogStore(engine)


def test_a_run_is_counted_once_and_counts_add_up_per_tenant(store):
    assert store.add_once("r1", "t1", "2026-11-19", "c", ["h1", "h2"])
    assert not store.add_once("r1", "t1", "2026-11-19", "c", ["h1", "h2"])  # a repeat read of the same run adds nothing
    assert store.add_once("r2", "t1", "2026-11-19", "c", ["h1"])
    assert store.add_once("r3", "t2", "2026-11-19", "c", ["h1"])
    assert sorted(store.rows("t1")) == [("c", "h1", 2), ("c", "h2", 1)] and store.rows("t2") == [("c", "h1", 1)]
    assert store.rows("t1", since_day="2026-11-20") == []


def test_the_migration_matches_the_code_definitions(tmp_path, monkeypatch):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    u = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("ANALYTICS_CONTROL_DB_URL", u)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")
    insp = inspect(create_engine(u))
    for table in (rb.COUNTS, rb.SEEN):
        assert {c["name"] for c in insp.get_columns(table.name)} == {c.name for c in table.columns}
        assert set(insp.get_pk_constraint(table.name)["constrained_columns"]) == {c.name for c in table.primary_key.columns}
    command.downgrade(config, "0013_connect_store")
    assert not {"refusal_backlog_counts", "refusal_backlog_seen"} & set(inspect(create_engine(u)).get_table_names())


# ---- the report -------------------------------------------------------------------------------------------------------------------


def test_the_report_ranks_named_words_and_leaves_the_rest_opaque():
    recorder = rb.BacklogRecorder(rb.InMemoryBacklogStore(), lambda t: SALT)
    for i, q in enumerate(["revenue for refunded orders", "how many refunded orders", "revenue for returned orders", "orders shipped to mars"]):
        words = {"refunded", "returned", "mars", "shipped"}
        assert recorder(run_id=f"r{i}", tenant_id="t", code="intent_coverage_mismatch", question=q, reference=ref(q, words))
    backlog = rb.build_backlog(recorder.store.rows("t"), {"refund", "return", "order", "revenue"}, SALT)
    assert [(i.term, i.refusals) for i in backlog.resolved] == [("refund", 2), ("return", 1)]
    assert (backlog.unresolved_terms, backlog.unresolved_refusals) == (2, 2)  # "mars" and "shipped" stay opaque
    report = rb.render_backlog(backlog, tenant_id="t")
    assert "**refund**: 2" in report and "mars" not in report and "shipped" not in report and "never a certified synonym" in report


def test_the_vocabulary_comes_from_the_contracts_own_words_and_skips_digests():
    from test_m7_pilot_wiring import DOCUMENT

    vocab = rb.vocabulary_from_contract(DOCUMENT.contract)
    assert "revenue" in vocab and not any(len(w) > 20 for w in vocab)


# ---- through the governed runtime -------------------------------------------------------------------------------------------------


def with_recorder(monkeypatch, recorder):
    """The service is built when the client is, so the hook is attached as the runtime builds it."""
    original = GovernedRuntime.service

    def service(self):
        built = original(self)
        built.refusal_recorder = recorder
        return built

    monkeypatch.setattr(GovernedRuntime, "service", service)


def wired(tmp_path, monkeypatch):
    store = rb.InMemoryBacklogStore()
    with_recorder(monkeypatch, rb.BacklogRecorder(store, lambda t: SALT))
    client, stack, _ = runtime(tmp_path, frozenset({"*"}))
    return client, stack, store


def test_a_coverage_refusal_is_counted_once_even_when_read_again_and_the_words_are_not_stored(tmp_path, monkeypatch):
    client, stack, store = wired(tmp_path, monkeypatch)
    body = ask(client, stack, "Show revenue for refunded orders", "bk-key-000001")
    run_id = body["run_id"]
    auth = {"Authorization": f"Bearer {stack.token('requester')}"}
    for _ in range(3):
        client.get(f"/api/v2/analytics/runs/{run_id}", headers=auth)
    rows = store.rows("tenant-a")
    assert body["outcome"]["outcome"] == "refuse"
    assert ("intent_coverage_mismatch", rb.term_hash(SALT, "refunded"), 1) in rows and {n for _, _, n in rows} == {1}  # counted once per read
    assert "refunded" not in repr(rows) and "refunded" not in repr(vars(store))


def test_an_answered_question_is_never_counted(tmp_path, monkeypatch):
    client, stack, store = wired(tmp_path, monkeypatch)
    assert ask(client, stack, "Show revenue", "bk-key-000002")["outcome"]["outcome"] == "answer"
    assert store.rows("tenant-a") == []


def test_a_failing_recorder_never_changes_the_refusal(tmp_path, monkeypatch):
    def boom(**_):
        raise RuntimeError("store down")

    with_recorder(monkeypatch, boom)
    client, stack, _ = runtime(tmp_path, frozenset({"*"}))
    out = ask(client, stack, "Show revenue for refunded orders", "bk-key-000003")["outcome"]
    assert out["outcome"] == "refuse" and '"refunded"' in out["explanation"]


def test_no_word_reaches_the_sql_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'b.db'}")
    rb.COUNTS.metadata.create_all(engine)
    recorder = rb.BacklogRecorder(rb.SqlBacklogStore(engine), lambda t: SALT)
    q = "revenue for refunded orders"
    recorder(run_id="r1", tenant_id="t", code="intent_coverage_mismatch", question=q, reference=ref(q, {"refunded"}))
    with engine.connect() as c:
        stored = " ".join(str(v) for t in ("refusal_backlog_counts", "refusal_backlog_seen") for row in c.execute(text(f"SELECT * FROM {t}")) for v in row)
    assert "refund" not in stored.lower() and "revenue" not in stored.lower()
