"""The warm-up: bounded, read-only by construction (the caller supplies certified statements), off by default, never breaks a request."""

from __future__ import annotations

import threading

from app.connect.warmup import Warmer


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(**kw):
    c = Clock()
    return c, Warmer(clock=c, **kw)


def test_it_does_nothing_unless_the_connection_turned_it_on():
    sent: list[str] = []
    _c, w = make()
    assert w.warm("t", "c", ["SELECT 1"], sent.append, enabled=False).code == "skipped_disabled" and sent == []


def test_it_runs_the_statements_once_then_skips_while_the_warehouse_is_probably_still_running():
    sent: list[str] = []
    c, w = make(idle_after=480)
    assert w.warm("t", "c", ["a", "b"], sent.append, enabled=True).code == "warmed" and sent == ["a", "b"]
    c.t += 100
    assert w.warm("t", "c", ["a", "b"], sent.append, enabled=True).code == "skipped_recent" and sent == ["a", "b"]
    c.t += 400
    assert w.warm("t", "c", ["a"], sent.append, enabled=True).code == "warmed" and sent == ["a", "b", "a"]


def test_a_real_answer_counts_as_activity_so_no_warm_up_follows_it():
    sent: list[str] = []
    c, w = make(idle_after=480)
    w.note_activity("t", "c")
    c.t += 60
    assert w.warm("t", "c", ["a"], sent.append, enabled=True).code == "skipped_recent" and sent == []


def test_each_tenant_and_connection_has_its_own_clock():
    sent: list[str] = []
    _c, w = make()
    assert w.warm("t1", "c", ["a"], sent.append, enabled=True).code == "warmed"
    assert w.warm("t2", "c", ["a"], sent.append, enabled=True).code == "warmed" and w.warm("t1", "d", ["a"], sent.append, enabled=True).code == "warmed"


def test_a_failure_is_a_stable_code_with_no_message_and_does_not_count_as_warmed():
    def boom(sql):
        raise RuntimeError("secret-bearing driver message")
    _c, w = make()
    r = w.warm("t", "c", ["a"], boom, enabled=True)
    assert r.code == "failed" and "secret" not in repr(r)
    assert w.warm("t", "c", ["a"], lambda s: None, enabled=True).code == "warmed"  # the next session tries again


def test_it_is_bounded_by_the_statement_cap_and_the_deadline():
    sent: list[str] = []
    c, w = make(max_statements=2, deadline=30)
    assert w.warm("t", "c", ["a", "b", "c", "d"], sent.append, enabled=True).statements_run == 2 and sent == ["a", "b"]
    c2, w2 = make(deadline=30)
    sent2: list[str] = []

    def slow(sql):
        sent2.append(sql)
        c2.t += 20
    r = w2.warm("t", "c", ["a", "b", "c"], slow, enabled=True)
    assert r.code == "stopped_by_deadline" and sent2 == ["a", "b"]


def test_two_sessions_opening_together_warm_once():
    sent: list[str] = []
    gate, results = threading.Event(), []
    _c, w = make()

    def slow(sql):
        sent.append(sql)
        gate.wait(2)

    t = threading.Thread(target=lambda: results.append(w.warm("t", "c", ["a"], slow, enabled=True)))
    t.start()
    while not sent:
        pass
    results.append(w.warm("t", "c", ["a"], sent.append, enabled=True))
    gate.set()
    t.join()
    assert sorted(r.code for r in results) == ["skipped_recent", "warmed"] and sent == ["a"]


def test_an_audit_hook_that_raises_does_not_break_the_request_and_sees_only_a_code_and_a_count():
    seen = []

    def hook(r):
        seen.append(r)
        raise RuntimeError("audit store down")
    _c, w = make()
    r = w.warm("t", "c", ["a"], lambda s: None, enabled=True, on_done=hook)
    assert r.code == "warmed" and seen == [r] and set(vars(r)) == {"code", "statements_run", "seconds"}
