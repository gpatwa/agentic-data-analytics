"""A live check of the WIRED warm-up (design: docs/design/WAREHOUSE_WARMUP_DESIGN.md, "As built"): the product's own `WarmUpService`, not the experiment's hand-picked statements.

What is real: `WarmUpService`, `Warmer` (default 8-minute idle gate), `warm_plans` choosing statements from the certified contract, the Databricks compiler, the connection record's
`warm_up` switch, and the audit hook. What stands in: the connection store is in memory (one record, no secret), and the gateway runs the compiled SQL through the owner's own CLI session
on an existing warehouse instead of the OAuth connector, so no credential is read. Every statement passes the scale-test guard (a plain SELECT on the target schema) first.

Order: (0) the switch off answers `skipped_disabled` and runs nothing; (1) the switch on, after an idle gap, `request` answers `warming` and the warm-up runs on its background thread;
(2) a second `request` and `on_question` right after run nothing; (3) the 32 workload statements, whose first read is compared with the control (8.5 s) and the experiment (2.9 s)."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Callable

from app.compiler import DatabricksCompilerAdapter
from app.connect.store import ConnectionRecord, ConnectionStore
from app.connect.warmup import WarmResult
from app.connect.warmup_service import WarmUpService
from app.context.registry_context import CertifiedContracts
from app.semantic_registry import SemanticRegistry
from reference_stack import synthetic_views_contract as sv
from reference_stack.scale_test.guard import check_statement
from reference_stack.scale_test.runner import Budget, Plan, Session, Stop, run_size, unique
from reference_stack.scale_test.warmup_experiment import Api, wait_idle_gap
from reference_stack.scale_test.workload import Statement, retarget

TENANT = "synthetic"  # the certified contract is bound to this tenant; the in-memory store holds nothing else
CONNECTION = "live-check-1"
WAIT_FOR_WARM_SECONDS = 120.0


class CliGateway:
    """The `execute(plan, limits=)` the service calls, over the CLI session. Refuses anything but a plain SELECT on the target schema, or a plan with parameters."""

    def __init__(self, session: Session, catalog: str, schema: str, budget: Budget, run_id: str) -> None:
        self._session, self._catalog, self._schema, self._budget, self._run_id = session, catalog, schema, budget, run_id
        self.statement_ids: list[str] = []
        self._n = 0

    def execute(self, plan: Any, *, limits: Any, cancellation: Any = None) -> None:  # noqa: ARG002
        if getattr(plan, "parameters", None):
            raise Stop("warm_up_plan_has_parameters")
        self._n += 1
        sql = unique(plan.sql, f"{self._run_id}-svc-{self._n}")
        check_statement(sql, self._catalog, self._schema)
        self._budget.take()
        sid, _ = self._session.query_with_id(sql)
        self.statement_ids.append(sid)


def build_service(contract_root: Any, catalog: str, schema: str, gateway: Any, audit: Callable[[str, str, WarmResult], None]) -> tuple[WarmUpService, ConnectionStore]:
    """The real service over the certified contract pointed at the target schema (the same retargeting the workload uses), with one in-memory connection whose switch is off."""
    document = next(e.document for e in SemanticRegistry(contract_root).list_entries()
                    if e.document is not None and e.document.lifecycle == "certified" and e.document.contract.id == sv.CONTRACT_ID)
    target = retarget(document.contract, catalog, schema)
    document = document.model_copy(update={"contract": target})
    adapter = DatabricksCompilerAdapter()
    compiler = SimpleNamespace(compile=lambda intent, *, policy_values=None: adapter.compile(intent, target, policy_values))
    assets = SimpleNamespace(gateway=gateway, compiler=compiler, contracts=CertifiedContracts([document]))
    store = ConnectionStore()
    store.add(ConnectionRecord(tenant_id=TENANT, connection_id=CONNECTION, engine="databricks", host="live-check.invalid", account="", user="live-check-client", role="", warehouse="existing",
                               database=catalog, default_schema=schema, allowed_tables=tuple(f"{catalog}.{schema}.{d.id}".lower() for d in target.datasets), auth_method="oauth_m2m",
                               secret_name="none", http_path="/sql/1.0/warehouses/none", status="active", warm_up=False))
    return WarmUpService(store, lambda _tenant: assets, audit=audit), store


def run_service_check(statements: list[Statement], session: Session, api: Api, warehouse_id: str, contract_root: Any, catalog: str, schema: str, run_id: str, budget: Budget,
                      history_for: Callable[[list[Any]], dict[str, Any]], *, idle_minutes: float, clock: Callable[[], float] = time.monotonic,
                      sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    done = threading.Event()
    audits: list[dict[str, Any]] = []

    def audit(_tenant: str, _connection: str, result: WarmResult) -> None:
        audits.append({"code": result.code, "statements_run": result.statements_run, "seconds": round(result.seconds, 2)})
        done.set()

    gateway = CliGateway(session, catalog, schema, budget, run_id)
    service, store = build_service(contract_root, catalog, schema, gateway, audit)
    steps: dict[str, Any] = {}
    steps["switch_off"] = {"code": service.request(TENANT), "statements_sent": len(gateway.statement_ids)}  # (0) free: nothing leaves the process
    store.update(replace(store.get(TENANT, CONNECTION), warm_up=True))
    waited = wait_idle_gap(idle_minutes, clock=clock, sleep=sleep)
    t0 = clock()
    steps["request"] = {"code": service.request(TENANT), "answered_in_ms": round((clock() - t0) * 1000, 1)}  # (1) must answer at once, whatever the warehouse does
    finished = done.wait(WAIT_FOR_WARM_SECONDS)
    steps["warm_up"] = (audits[0] if audits else {"code": "no_audit_within_wait"}) | {"finished": finished, "statements_sent": len(gateway.statement_ids)}
    sent = len(gateway.statement_ids)
    steps["second_request"] = {"code": service.request(TENANT)}  # (2) the idle gate: nothing runs
    service.on_question(TENANT)
    steps["after_on_question"] = {"statements_sent_since": len(gateway.statement_ids) - sent, "audits": len(audits)}
    rows: list[Any] = []
    try:
        run_size(Plan(("existing",), warm_runs=0, concurrency=()), statements, session, "existing", catalog, schema, run_id, budget, rows, clock)
    finally:
        hist = history_for(rows)
    first = rows[0]
    h = hist.get(first.statement_id)
    rest = [hist[r.statement_id].execution_ms for r in rows[1:] if r.statement_id in hist and hist[r.statement_id].execution_ms is not None]
    ok = (steps["switch_off"] == {"code": "skipped_disabled", "statements_sent": 0} and steps["request"]["code"] == "warming" and steps["warm_up"].get("code") == "warmed"
          and steps["second_request"]["code"] == "skipped_recent" and steps["after_on_question"]["statements_sent_since"] == 0 and not any(r.mismatch for r in rows))
    return {"waited_s": round(waited), "steps": steps, "first_key": first.key, "first_wall_ms": round(first.wall_ms), "first_execution_ms": getattr(h, "execution_ms", None),
            "first_remote_bytes": getattr(h, "read_remote_bytes", None), "rest_execution_ms_median": sorted(rest)[len(rest) // 2] if rest else None, "statements": len(rows),
            "mismatches": sum(1 for r in rows if r.mismatch), "wired_path_ok": ok,
            "compare_with": {"control_first_execution_ms": 8502, "experiment_warmed_first_execution_ms": 2939, "source": "earlier runs, 2026-11-22; one pair; not measured here"},
            "verdict": "wired path behaved as designed" if ok else "wired path did NOT behave as designed: read the steps"}
