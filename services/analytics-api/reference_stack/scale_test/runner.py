"""Sequential by size: one warehouse is up at a time (billing is by uptime, so interleaving four would cost about four times as much). Each size runs every statement once cold, then warm,
then a concurrency batch, and is deleted before the next opens. A larger size is tried only while the last step still paid off. One answer mismatch stops everything."""

from __future__ import annotations

import itertools
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from reference_stack.scale_test.guard import check_statement
from reference_stack.scale_test.workload import Statement, compare

MAX_STATEMENT_SECONDS = 300.0


class Stop(Exception):
    """A stop rule fired. The text is a short code, never a statement or a value."""


class Session(Protocol):
    def query_with_id(self, sql: str) -> tuple[str, list[tuple[Any, ...]]]: ...


@dataclass(frozen=True)
class Plan:
    sizes: tuple[str, ...]  # ascending
    warm_runs: int = 2
    concurrency: tuple[int, ...] = (4,)
    concurrency_statements: int = 8
    seed: int = 1
    max_statements: int = 450
    max_minutes: float = 30.0
    min_gain: float = 0.15  # a larger size is tried only if the last step cut the warm median by at least this fraction

    def statement_count(self, n_statements: int) -> int:
        """Warehouse statements this plan sends at most (it can stop early), preflight included (two checks that the views exist)."""
        return 2 + len(self.sizes) * (n_statements * (1 + self.warm_runs) + len(self.concurrency) * min(self.concurrency_statements, n_statements))


@dataclass
class Row:
    key: str
    shape: str
    size: str
    phase: str  # cold | warm | concurrent
    concurrency: int
    wall_ms: float
    statement_id: str
    mismatch: str | None = None


@dataclass
class Budget:
    max_statements: int
    deadline: float
    clock: Callable[[], float] = time.monotonic
    count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def take(self) -> None:
        with self._lock:
            self.count += 1
            if self.count > self.max_statements:
                raise Stop("statement_budget_reached")
            if self.clock() > self.deadline:
                raise Stop("time_cap_reached")


class Provider(Protocol):
    def open(self, size: str) -> Session | None:
        """A running warehouse of this size, or None when the workspace refuses it."""

    def close(self, size: str) -> float:
        """Delete it (never for a borrowed one); seconds it was up."""


@dataclass
class Outcome:
    rows: list[Row] = field(default_factory=list)
    history: dict[str, Any] = field(default_factory=dict)
    uptime: dict[str, float] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    stopped_escalating_after: str | None = None
    warm_median_ms: dict[str, float] = field(default_factory=dict)


_SERIAL = itertools.count()


def unique(sql: str, tag: str) -> str:
    """The same statement with a constant output column unique to this run. A comment alone does not change the warehouse's result-cache key (the first live run: 72 of 104
    runs were cache hits); a differing output does. The answer check reads the columns by position and ignores the extra one. Nothing else changes."""
    if "\nFROM " not in sql:
        raise Stop("statement_shape_unexpected")
    return f"/* scale {tag} */ " + sql.replace("\nFROM ", f", '{tag}' AS scale_tag\nFROM ", 1)


def warm_median_ms(rows: list[Row], history: dict[str, Any]) -> float:
    """The decision metric: median time of the sequential warm runs, from the warehouse's record where it exists, else wall-clock."""
    values = []
    for r in rows:
        if r.phase == "warm":
            h = history.get(r.statement_id)
            values.append(h.execution_ms if h is not None and h.execution_ms is not None else r.wall_ms)
    return statistics.median(values)


def run_size(plan: Plan, statements: list[Statement], session: Session, size: str, catalog: str, schema: str, run_id: str, budget: Budget, out: list[Row],
             clock: Callable[[], float] = time.monotonic) -> None:
    lock = threading.Lock()

    def one(st: Statement, phase: str, level: int) -> None:
        check_statement(st.sql, catalog, schema)
        sql = unique(st.sql, f"{run_id}-{next(_SERIAL)}")
        check_statement(sql, catalog, schema)
        budget.take()
        t0 = clock()
        sid, got = session.query_with_id(sql)
        wall = (clock() - t0) * 1000
        bad = compare(st, got)
        with lock:
            out.append(Row(st.key, st.shape, size, phase, level, wall, sid, bad))
        if bad:
            raise Stop(f"answer_mismatch:{st.key}:{size}:{bad}")
        if wall / 1000 > MAX_STATEMENT_SECONDS:
            raise Stop(f"statement_too_slow:{st.key}:{size}")

    rng = random.Random(f"{plan.seed}:{size}")
    for pass_no in range(1 + plan.warm_runs):
        order = list(statements)
        rng.shuffle(order)
        for st in order:
            one(st, "cold" if pass_no == 0 else "warm", 1)
    ranked = sorted(statements, key=lambda s: s.key)
    chosen = ranked[:: max(1, len(ranked) // max(1, plan.concurrency_statements))][: plan.concurrency_statements]
    for level in plan.concurrency:
        with ThreadPoolExecutor(level) as pool:
            for future in [pool.submit(one, st, "concurrent", level) for st in chosen]:
                future.result()


def run(plan: Plan, statements: list[Statement], provider: Provider, catalog: str, schema: str, run_id: str, budget: Budget,
        history_for: Callable[[list[Row]], dict[str, Any]], out: Outcome, clock: Callable[[], float] = time.monotonic) -> Outcome:
    """`out` is filled as it goes, so a stop still leaves everything measured so far."""
    previous: float | None = None
    for size in plan.sizes:
        session = provider.open(size)
        if session is None:
            out.skipped.append(size)
            continue
        mine: list[Row] = []
        try:
            run_size(plan, statements, session, size, catalog, schema, run_id, budget, mine, clock)
        finally:
            out.rows += mine
            out.uptime[size] = out.uptime.get(size, 0.0) + provider.close(size)
        out.history |= history_for(mine)
        current = out.warm_median_ms[size] = warm_median_ms(mine, out.history)
        if previous is not None and current > previous * (1 - plan.min_gain):
            out.stopped_escalating_after = size
            break
        previous = current
    return out
