"""Warm a customer's warehouse before the user's first question (design: docs/design/WAREHOUSE_WARMUP_DESIGN.md).

The measured problem (docs/evaluation/scale-test-tier-m-results.md): after the warehouse has been idle, the first read of a table comes from cloud storage and took
5 to 6 s on tier M, against 0.35 s once the data is in the warehouse's disk cache. The cache lives only while the warehouse runs.

What this does, and no more: when a user opens a session and the connection has been quiet for longer than `idle_after`, run a few certified, read-only statements (the caller compiles
them from the certified contract, as a question would be compiled) through the connection's own read-only session, and throw the results away. It adds no privilege (same principal, same
allow-list as an answer), sends nothing to a model, and never blocks or fails the user's request: every error is swallowed into a stable code. It is OFF unless a connection turns it
on, and a warm-up is skipped when the previous one is recent enough that the warehouse is probably still running, so a busy connection pays nothing extra.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class WarmResult:
    code: str  # warmed | skipped_recent | skipped_disabled | skipped_none | failed | stopped_by_deadline
    statements_run: int = 0
    seconds: float = 0.0


class Warmer:
    """Per-connection, in-process. `idle_after` should be at most the warehouse's auto-stop (the starter warehouse's is 10 minutes); the deadline bounds the time one warm-up may take."""

    def __init__(self, *, idle_after: float = 480.0, deadline: float = 30.0, max_statements: int = 4, clock: Callable[[], float] = time.monotonic) -> None:
        self.idle_after, self.deadline, self.max_statements, self._clock = idle_after, deadline, max_statements, clock
        self._last: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def warm(self, tenant_id: str, connection_id: str, statements: Sequence[Any], execute: Callable[[Any], Any], *, enabled: bool,
             on_done: Callable[[WarmResult], None] | None = None) -> WarmResult:
        """`execute` runs one statement on the connection's read-only session and consumes the result. The key is the tenant and the connection, so one tenant never warms (or learns of) another's."""
        result = self._warm((tenant_id, connection_id), list(statements)[: self.max_statements], execute, enabled)
        if on_done is not None:
            try:
                on_done(result)  # the caller's audit record: a code and a count, never a statement or a value
            except Exception:  # noqa: BLE001 - an audit hook must not break the request
                pass
        return result

    def _warm(self, key: tuple[str, str], statements: list[Any], execute: Callable[[Any], Any], enabled: bool) -> WarmResult:
        if not enabled:
            return WarmResult("skipped_disabled")
        if not statements:
            return WarmResult("skipped_none")
        start = self._clock()
        with self._lock:
            last = self._last.get(key)
            if last is not None and start - last < self.idle_after:
                return WarmResult("skipped_recent")
            self._last[key] = start  # claimed now, so two sessions opening together warm once
        run = 0
        try:
            for sql in statements:
                if self._clock() - start > self.deadline:
                    return WarmResult("stopped_by_deadline", run, self._clock() - start)
                execute(sql)
                run += 1
        except Exception:  # noqa: BLE001 - never echo a driver message; the user's request goes on
            with self._lock:
                self._last.pop(key, None)  # a failed warm-up does not count, so the next session tries again
            return WarmResult("failed", run, self._clock() - start)
        return WarmResult("warmed", run, self._clock() - start)

    def recent(self, tenant_id: str, connection_id: str) -> bool:
        """True when a warm-up or a real answer ran less than `idle_after` ago, so a warm-up now would be skipped. Read only: it claims nothing."""
        with self._lock:
            last = self._last.get((tenant_id, connection_id))
        return last is not None and self._clock() - last < self.idle_after

    def note_activity(self, tenant_id: str, connection_id: str) -> None:
        """A real answer just ran, so the warehouse is warm now: a warm-up right after it would be wasted."""
        with self._lock:
            self._last[(tenant_id, connection_id)] = self._clock()
