"""The matrix: sizes x statements x (1 cold + warm runs), then concurrency. One answer mismatch stops everything."""

from __future__ import annotations

import random
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
    sizes: tuple[str, ...]
    warm_runs: int = 3
    concurrency: tuple[int, ...] = (4, 8)
    concurrency_statements: int = 8
    seed: int = 1
    max_statements: int = 600
    max_minutes: float = 45.0

    def statement_count(self, n_statements: int) -> int:
        """Warehouse statements this plan sends, preflight included (two checks that the views exist)."""
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


def run(plan: Plan, statements: list[Statement], sessions: dict[str, Session], catalog: str, schema: str, run_id: str, budget: Budget,
        clock: Callable[[], float] = time.monotonic) -> list[Row]:
    rows: list[Row] = []
    lock = threading.Lock()
    serial = iter(range(1_000_000))

    def one(st: Statement, size: str, phase: str, level: int) -> None:
        sql = f"/* scale {run_id} {next(serial)} */ {st.sql}"  # a unique comment per run, so an identical text is not answered from a cache
        check_statement(sql, catalog, schema)
        budget.take()
        t0 = clock()
        sid, got = sessions[size].query_with_id(sql)
        wall = (clock() - t0) * 1000
        bad = compare(st, got)
        with lock:
            rows.append(Row(st.key, st.shape, size, phase, level, wall, sid, bad))
        if bad:
            raise Stop(f"answer_mismatch:{st.key}:{size}:{bad}")
        if wall / 1000 > MAX_STATEMENT_SECONDS:
            raise Stop(f"statement_too_slow:{st.key}:{size}")

    rng = random.Random(plan.seed)
    for pass_no in range(1 + plan.warm_runs):
        order = list(statements)
        rng.shuffle(order)
        for st in order:
            sizes = list(plan.sizes)
            rng.shuffle(sizes)
            for size in sizes:
                one(st, size, "cold" if pass_no == 0 else "warm", 1)
    ranked = sorted(statements, key=lambda s: s.key)
    step = max(1, len(ranked) // max(1, plan.concurrency_statements))
    chosen = ranked[::step][: plan.concurrency_statements]
    sizes = list(plan.sizes)
    rng.shuffle(sizes)
    for size in sizes:
        for level in plan.concurrency:
            with ThreadPoolExecutor(level) as pool:
                for future in [pool.submit(one, st, size, "concurrent", level) for st in chosen]:
                    future.result()
    return rows
