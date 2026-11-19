"""The durable Connect state (migration 0013): the connection store keeps the in-memory store's rules, survives a restart, the host
claim is exclusive, and the setup audit chain is durable and cannot fork. SQLite stands in for the control database."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.connect.records import SetupRecorder
from app.connect.sql_store import AUDIT, CLAIMS, CONNECTIONS, SqlAuditChain, SqlConnectionStore
from app.connect.store import ConnectionConflict, ConnectionNotFound, ConnectionRecord
from app.runtime.pilot_wiring import migrate

HOST = "dbc-1234abcd-5678.cloud.databricks.com"
APP = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
SERVICE_ROOT = Path(__file__).parent.parent


@pytest.fixture
def url(tmp_path):
    u = f"sqlite:///{tmp_path / 'control.db'}"
    migrate(u)
    return u


@pytest.fixture
def engine(url):
    return create_engine(url)


def rec(tenant="ta", cid="c1", host=HOST, **over):
    base = dict(tenant_id=tenant, connection_id=cid, engine="databricks", host=host, account=host, user=APP, role="", warehouse="w", database="sales",
                default_schema="data", allowed_tables=("sales.data.orders", "sales.data.items"), auth_method="oauth_m2m", secret_name=f"databricks/{cid}",
                teardown=("DROP x",), http_path="/sql/1.0/warehouses/abcdef0123456789", status="active",
                secret_expires_at=datetime(2027, 2, 16, 12, 0, tzinfo=timezone.utc))
    return ConnectionRecord(**{**base, **over})


# ---- schema -----------------------------------------------------------------------------------------------------------------------


def test_the_migration_creates_and_removes_the_tables_and_the_code_definitions_match_it(tmp_path, monkeypatch):
    u = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("ANALYTICS_CONTROL_DB_URL", u)
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_ROOT / "alembic"))
    command.upgrade(config, "head")
    insp = inspect(create_engine(u))
    for table in (CONNECTIONS, CLAIMS, AUDIT):
        assert {c["name"] for c in insp.get_columns(table.name)} == {c.name for c in table.columns}, table.name
        assert set(insp.get_pk_constraint(table.name)["constrained_columns"]) == {c.name for c in table.primary_key.columns}
    command.downgrade(config, "0012_durable_run_material")
    assert not {"connect_connections", "connect_target_claims", "connect_audit_events"} & set(inspect(create_engine(u)).get_table_names())


def test_no_column_can_hold_a_secret_value():
    names = {c.name for t in (CONNECTIONS, CLAIMS, AUDIT) for c in t.columns}
    assert not {n for n in names if "secret" in n or "password" in n or "token" in n or "key" in n} - {"secret_name", "secret_expires_at"}


# ---- the store --------------------------------------------------------------------------------------------------------------------


def test_a_record_round_trips_exactly_and_survives_a_new_store_on_the_same_database(url, engine):
    original = rec()
    SqlConnectionStore(engine).add(original)
    again = SqlConnectionStore(create_engine(url)).get("ta", "c1")  # a restart: new engine, new store
    assert again == original
    assert isinstance(again.allowed_tables, tuple) and again.secret_expires_at.tzinfo is not None


def test_another_tenant_cannot_tell_a_record_exists(engine):
    s = SqlConnectionStore(engine)
    s.add(rec())
    with pytest.raises(ConnectionNotFound) as other:
        s.get("tb", "c1")
    with pytest.raises(ConnectionNotFound) as missing:
        s.get("tb", "nope")
    assert str(other.value) == str(missing.value)
    assert s.list("tb") == [] and [r.connection_id for r in s.list("ta")] == ["c1"]
    with pytest.raises(ConnectionNotFound):
        s.set_status("tb", "c1", "suspended")
    with pytest.raises(ConnectionNotFound):
        s.update(rec(tenant="tb"))
    assert s.get("ta", "c1").status == "active"  # untouched


def test_conflicts_use_the_same_stable_codes(engine):
    s = SqlConnectionStore(engine)
    s.add(rec())
    with pytest.raises(ConnectionConflict) as dup:
        s.add(rec())
    assert dup.value.code == "connection_id_taken"
    with pytest.raises(ConnectionConflict) as claimed:
        s.add(rec(tenant="tb", cid="c2"))
    assert claimed.value.code == "target_claimed"
    assert s.list("tb") == []  # the refused add left nothing behind, not even a half-written row
    s.add(rec(cid="c3"))  # the same tenant may connect the same host again
    with pytest.raises(ConnectionConflict):
        s.add(rec(tenant="tb", cid="c4"))


def test_a_target_is_free_again_only_when_every_record_on_it_is_removed(engine):
    s = SqlConnectionStore(engine)
    s.add(rec())
    s.add(rec(cid="c3"))
    s.set_status("ta", "c1", "removed")
    with pytest.raises(ConnectionConflict):
        s.add(rec(tenant="tb", cid="c5"))  # c3 still holds it
    s.set_status("ta", "c3", "removed")
    s.add(rec(tenant="tb", cid="c5"))  # now the claim moves to tb
    with pytest.raises(ConnectionConflict):
        s.add(rec(tenant="ta", cid="c6"))  # and ta is the one refused
    assert [r.connection_id for r in s.list("tb")] == ["c5"]


def test_status_and_update_persist_and_validate(url, engine):
    s = SqlConnectionStore(engine)
    s.add(rec(status="pending"))
    assert s.set_status("ta", "c1", "active").status == "active"
    with pytest.raises(ValueError):
        s.set_status("ta", "c1", "bogus")
    s.update(rec(secret_expires_at=datetime(2027, 6, 1, tzinfo=timezone.utc), teardown=()))
    got = SqlConnectionStore(create_engine(url)).get("ta", "c1")
    assert got.secret_expires_at == datetime(2027, 6, 1, tzinfo=timezone.utc) and got.teardown == () and got.status == "active"


def test_a_freed_claim_taken_by_someone_else_first_is_not_overwritten(engine):
    """The takeover is a compare-and-set on the owner that was read: if the claim changed hands in between, this add loses."""
    s = SqlConnectionStore(engine)
    s.add(rec())
    s.set_status("ta", "c1", "removed")  # ta's claim is free in principle: its owner has no live record
    s._after_owner_read = lambda conn: conn.execute(CLAIMS.update().values(tenant_id="tc"))  # tc takes it after tb read "ta"
    with pytest.raises(ConnectionConflict) as lost:
        s.add(rec(tenant="tb", cid="c9"))
    assert lost.value.code == "target_claimed" and s.list("tb") == []


def test_two_stores_on_one_database_share_the_claim(url, engine):
    SqlConnectionStore(engine).add(rec())
    other_process = SqlConnectionStore(create_engine(url))
    with pytest.raises(ConnectionConflict) as claimed:
        other_process.add(rec(tenant="tb", cid="c2"))
    assert claimed.value.code == "target_claimed"


# ---- the audit chain --------------------------------------------------------------------------------------------------------------


def test_the_chain_is_durable_extends_across_instances_and_verifies(url, engine):
    a = SqlAuditChain(engine)
    a.append("e1", "consent_shown", "ta", "admin", {"n": 1})
    b = SqlAuditChain(create_engine(url))  # a restart, or a second replica
    e2 = b.append("e2", "script_run", "ta", "admin", {"n": 2})
    a.append("e3", "connected", "ta", "admin", {"n": 3})
    events = SqlAuditChain(create_engine(url)).events
    assert [e.event_id for e in events] == ["e1", "e2", "e3"] and events[1].previous_hash == events[0].event_hash and e2.event_hash == events[1].event_hash
    assert SqlAuditChain(create_engine(url)).verify() is True


def test_a_tampered_row_breaks_verification(url, engine):
    c = SqlAuditChain(engine)
    for i in range(3):
        c.append(f"e{i}", "x", "ta", "admin", {"i": i})
    with engine.begin() as conn:
        conn.execute(text("UPDATE connect_audit_events SET payload = '{\"i\": 99}' WHERE seq = 2"))
    assert SqlAuditChain(engine).verify() is False


def test_two_writers_never_fork_the_chain(engine):
    """b extends the chain after a read the head and before a wrote: a's insert hits the taken position, re-reads and extends b's event."""
    a, b = SqlAuditChain(engine), SqlAuditChain(engine)
    a.append("e1", "x", "ta", "admin", {})
    stale, real_head = {"first": True}, a._head
    old_head = real_head()

    def head():
        if stale["first"]:
            stale["first"] = False
            b.append("from-b", "x", "ta", "admin", {})  # lands first
            return old_head  # a still believes e1 is the head
        return real_head()

    a._head = head
    a.append("from-a", "x", "ta", "admin", {})
    chain = SqlAuditChain(engine)
    assert [e.event_id for e in chain.events] == ["e1", "from-b", "from-a"] and chain.verify()


def test_an_unwritable_chain_fails_loudly_instead_of_dropping_the_event(engine):
    a = SqlAuditChain(engine, attempts=2)
    a.append("e1", "x", "ta", "admin", {})
    a._head = lambda: None  # always stale: position 1 is taken
    with pytest.raises(RuntimeError, match="audit_chain_busy"):
        a.append("e2", "x", "ta", "admin", {})
    assert [e.event_id for e in SqlAuditChain(engine).events] == ["e1"]


def test_the_setup_recorder_writes_to_the_durable_chain_and_its_ids_stay_unique_after_a_restart(url, engine):
    SetupRecorder(SqlAuditChain(engine), tenant_id="ta", connection_id="dbx-1", admin_id="admin").consent_shown(["GRANT x"], engine="databricks", host=HOST)
    restarted = SqlAuditChain(create_engine(url))
    SetupRecorder(restarted, tenant_id="ta", connection_id="dbx-2", admin_id="admin").consent_shown(["GRANT y"], engine="databricks", host=HOST)
    events = restarted.events
    assert len(events) == 2 and len({e.event_id for e in events}) == 2 and restarted.verify()
