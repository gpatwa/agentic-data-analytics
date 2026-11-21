"""Medians and p95 (never means), cold and warm apart, from the warehouse's own record where it exists. Cost uses an assumed price the owner supplies."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any

from reference_stack.scale_test.history import HistoryRow
from reference_stack.scale_test.runner import Row
from reference_stack.scale_test.warehouses import DBU_PER_HOUR


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


def build(rows: list[Row], history: dict[str, HistoryRow], *, usd_per_dbu: float | None, uptime_seconds: dict[str, float]) -> dict[str, Any]:
    cached = [r for r in rows if history.get(r.statement_id) and history[r.statement_id].from_cache]
    excluded = {id(r) for r in cached}
    cells: dict[tuple[str, str, str, int], list[Row]] = defaultdict(list)
    for r in rows:
        if id(r) not in excluded:
            cells[(r.size, r.shape, r.phase, r.concurrency)].append(r)
    table = []
    for (size, shape, phase, level), group in sorted(cells.items()):
        recorded = [history[r.statement_id] for r in group if r.statement_id in history and history[r.statement_id].execution_ms is not None]
        walls = [r.wall_ms for r in group]
        exec_ms = [h.execution_ms for h in recorded]
        read = [h.read_bytes for h in recorded if h.read_bytes is not None]
        entry: dict[str, Any] = {"size": size, "shape": shape, "phase": phase, "concurrency": level, "runs": len(group), "from_warehouse_record": len(recorded),
                                 "wall_ms_median": statistics.median(walls), "wall_ms_p95": _p95(walls)}
        if exec_ms:
            entry |= {"execution_ms_median": statistics.median(exec_ms), "execution_ms_p95": _p95(exec_ms)}
        if read:
            entry["bytes_read_median"] = statistics.median(read)
        if usd_per_dbu is not None and exec_ms:
            entry["attributable_usd_per_answer"] = statistics.median(exec_ms) / 3_600_000 * DBU_PER_HOUR[size] * usd_per_dbu
        table.append(entry)
    cost = None
    if usd_per_dbu is not None:
        cost = {size: round(sec / 3600 * DBU_PER_HOUR[size] * usd_per_dbu, 4) for size, sec in uptime_seconds.items()}
    return {"statements_run": len(rows), "excluded_served_from_cache": len(cached), "no_warehouse_record": sum(1 for r in rows if r.statement_id not in history),
            "table": table, "uptime_cost_usd_upper_bound": cost,
            "notes": ["Execution time comes from the warehouse's query history where it was found; otherwise only wall-clock is shown, which includes this tool's own overhead.",
                      "Cold means the first run of a statement on that warehouse; earlier statements may already have warmed the table's data.",
                      "Tier M is about 1.1 million orders: a flat curve across sizes is a result, not evidence about larger data.",
                      "DBU rates by size are assumed placeholders and the price is supplied by the owner; uptime cost is an upper bound, attributable cost counts execution time only."]}


def markdown(report: dict[str, Any]) -> str:
    lines = ["# Warehouse scale test", "", f"Statements run: {report['statements_run']}; served from cache and excluded: {report['excluded_served_from_cache']}; with no warehouse record: {report['no_warehouse_record']}.", "",
             "| size | shape | phase | conc | runs | exec ms p50 | exec ms p95 | wall ms p50 | wall ms p95 |", "|---|---|---|---|---|---|---|---|---|"]
    for e in report["table"]:
        f = lambda k: f"{e[k]:.0f}" if k in e else "n/a"  # noqa: E731
        lines.append(f"| {e['size']} | {e['shape']} | {e['phase']} | {e['concurrency']} | {e['runs']} | {f('execution_ms_median')} | {f('execution_ms_p95')} | {f('wall_ms_median')} | {f('wall_ms_p95')} |")
    lines += ["", *[f"- {n}" for n in report["notes"]]]
    return "\n".join(lines) + "\n"
