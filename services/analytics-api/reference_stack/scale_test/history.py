"""The warehouse's own record of each statement (duration, compile, queue, bytes read, cache hit), joined to ours by statement id.

Field names are from the vendor's query-history reference as recalled and are NOT verified on this workspace: every field is read defensively, and a statement with no record is
reported as such, with the wall-clock time used instead and labelled."""

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


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse(entry: dict[str, Any]) -> HistoryRow:
    m = entry.get("metrics") or {}
    cache = m.get("result_from_cache")
    return HistoryRow(_num(entry.get("duration", m.get("total_time_ms"))), _num(m.get("execution_total_time_ms")), _num(m.get("compilation_time_ms")),
                      _num(m.get("queue_time_ms")), _num(m.get("read_bytes")), _num(m.get("rows_read_count")),
                      cache if isinstance(cache, bool) else None)


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
