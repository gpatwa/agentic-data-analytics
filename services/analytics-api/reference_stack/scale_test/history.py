"""The warehouse's own record of each statement (duration, compile, queue, bytes read, cache hit), joined to ours by statement id.

Field names were confirmed against one real response from this workspace on 2026-11-21 (the first run read the wrong execution field and found none): `metrics.execution_time_ms`,
`compilation_time_ms`, `total_time_ms`, `read_bytes`, `rows_read_count`, `result_from_cache`. There is no queue duration, only a timestamp, so queue time is left out. Every field is read defensively."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol


class Api(Protocol):
    def api(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class HistoryRow:
    duration_ms: float | None
    execution_ms: float | None
    compile_ms: float | None
    queue_ms: float | None
    read_bytes: float | None
    rows_read: float | None
    from_cache: bool | None
    read_remote_bytes: float | None = None


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse(entry: dict[str, Any]) -> HistoryRow:
    m = entry.get("metrics") or {}
    cache = m.get("result_from_cache")
    return HistoryRow(_num(entry.get("duration", m.get("total_time_ms"))), _num(m.get("execution_time_ms")), _num(m.get("compilation_time_ms")),
                      None, _num(m.get("read_bytes")), _num(m.get("rows_read_count")),
                      cache if isinstance(cache, bool) else None, _num(m.get("read_remote_bytes")))


def fetch(api: Api, ids: list[str], *, wait_seconds: float = 90.0, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> dict[str, HistoryRow]:
    """History can lag the statement by seconds, so it is polled until every id appears or the wait ends."""
    found: dict[str, HistoryRow] = {}
    start = clock()
    while True:
        pending = [i for i in ids if i not in found]
        for at in range(0, len(pending), 50):
            query = "&".join(f"filter_by.statement_ids={i}" for i in pending[at:at + 50])
            for entry in api.api("get", f"/api/2.0/sql/history/queries?include_metrics=true&max_results=100&{query}").get("res") or []:
                sid = str(entry.get("query_id") or entry.get("statement_id") or "")
                if sid in ids:
                    found[sid] = parse(entry)
        if len(found) == len(set(ids)) or clock() - start > wait_seconds:
            return found
        sleep(5.0)
