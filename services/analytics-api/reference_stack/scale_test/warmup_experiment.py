"""Does warming on session open remove the first-question delay? (design: docs/design/WAREHOUSE_WARMUP_DESIGN.md, "A live experiment")

Two rounds on ONE existing warehouse that is never created, resized, stopped or deleted here. Each round first waits until the warehouse has stopped on its own (idle past its auto-stop), so its
disk cache is empty. Round 1 (control) runs the 32 workload statements. Round 2 (warmed) runs the product's Warmer on two certified no-dimension statements, then the same 32. The
question is the first measured statement's execution time and how much it read from remote storage; the warm-up's own duration is the cost that moved."""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol

from app.connect.warmup import Warmer
from reference_stack.scale_test.guard import check_statement
from reference_stack.scale_test.runner import Budget, Plan, Session, Stop, run_size, unique
from reference_stack.scale_test.workload import Statement

WARM_METRICS = ("order_count", "gross_order_value")  # one certified no-dimension aggregate per dataset
SLOW_FIRST_MS = 3000.0
FAST_FIRST_MS = 1500.0


class Api(Protocol):
    def api(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]: ...


def wait_until_stopped(api: Api, warehouse_id: str, *, poll: float = 30.0, max_wait: float = 900.0, clock: Callable[[], float] = time.monotonic,
                       sleep: Callable[[float], None] = time.sleep) -> float:
    """Read-only: poll the warehouse's state until it has stopped by itself. Never stops it."""
    start = clock()
    while api.api("get", f"/api/2.0/sql/warehouses/{warehouse_id}").get("state") != "STOPPED":
        if clock() - start > max_wait:
            raise Stop("warehouse_did_not_go_idle")
        sleep(poll)
    return clock() - start


def verdict(control_first_ms: float | None, warmed_first_ms: float | None) -> str:
    if control_first_ms is None or warmed_first_ms is None:
        return "inconclusive: no warehouse record for a first statement"
    if control_first_ms < SLOW_FIRST_MS:
        return "inconclusive: the control round did not show the delay"
    return "warm-up removed the delay" if warmed_first_ms <= FAST_FIRST_MS else "warm-up did not remove the delay"


def run_experiment(statements: list[Statement], session: Session, api: Api, warehouse_id: str, catalog: str, schema: str, run_id: str, budget: Budget,
                   history_for: Callable[[list[Any]], dict[str, Any]], *, poll: float = 30.0, clock: Callable[[], float] = time.monotonic,
                   sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    plan = Plan(("existing",), warm_runs=0, concurrency=())
    picks = [st for st in statements if st.shape == "no_dimension" and st.metric_id in WARM_METRICS]
    warmer = Warmer(idle_after=0.0, deadline=60.0, max_statements=len(WARM_METRICS), clock=clock)
    rounds: list[dict[str, Any]] = []
    for arm in ("control", "warmed"):
        waited = wait_until_stopped(api, warehouse_id, poll=poll, clock=clock, sleep=sleep)
        warm_info = None
        if arm == "warmed":
            sql = [unique(st.sql, f"{run_id}-warm-{i}") for i, st in enumerate(picks)]
            for q in sql:
                check_statement(q, catalog, schema)

            def execute(q: str) -> None:
                budget.take()
                session.query_with_id(q)
            res = warmer.warm("scale", warehouse_id, sql, execute, enabled=True)
            warm_info = {"code": res.code, "statements_run": res.statements_run, "seconds": round(res.seconds, 2)}
            if res.code != "warmed":
                raise Stop(f"warm_up_{res.code}")
        rows: list[Any] = []
        try:
            run_size(plan, statements, session, "existing", catalog, schema, run_id, budget, rows, clock)
        finally:
            hist = history_for(rows)
        first = rows[0]
        h = hist.get(first.statement_id)
        rest = [hist[r.statement_id].execution_ms for r in rows[1:] if r.statement_id in hist and hist[r.statement_id].execution_ms is not None]
        rounds.append({"arm": arm, "waited_for_idle_s": round(waited), "warm_up": warm_info, "first_key": first.key, "first_wall_ms": round(first.wall_ms),
                       "first_execution_ms": getattr(h, "execution_ms", None), "first_remote_bytes": getattr(h, "read_remote_bytes", None),
                       "rest_execution_ms_median": sorted(rest)[len(rest) // 2] if rest else None, "statements": len(rows),
                       "mismatches": sum(1 for r in rows if r.mismatch)})
    control, warmed = rounds
    return {"rounds": rounds, "verdict": verdict(control["first_execution_ms"], warmed["first_execution_ms"])}
