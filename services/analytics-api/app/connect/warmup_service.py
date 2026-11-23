"""Warm a connected customer's warehouse before a user's first question (design: docs/design/WAREHOUSE_WARMUP_DESIGN.md, "Wiring plan").

`WarmUpService.request(tenant_id)` is what the `POST /api/v2/analytics/warm` call and the fallback at the top of `/analyze` both use. It decides in memory, answers at once with a
code, and does any warehouse work on a background thread, so it never makes a request wait and never raises into one.

What it runs: for each certified dataset of the tenant's own contract, the no-dimension aggregate of its first two metrics (at most four statements in all), compiled by the tenant's own
certified compiler and run through the tenant's own gateway, so it has exactly an answer's allow-list and the connection's read-only principal. The results are thrown away. Nothing is sent
to a model, and nothing but a code, a count and a duration is recorded. A contract that needs policy values to compile simply fails to warm (a stable code), which is the safe outcome.

When it does nothing: the connection's `warm_up` switch is off (the default), the tenant has no single active Databricks connection, or a warm-up or a real answer ran within the Warmer's
`idle_after`. So a busy connection pays nothing, and calling it in a loop cannot keep a warehouse awake.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.connect.store import ConnectionRecord, ConnectionStore
from app.connect.warmup import Warmer, WarmResult
from app.execution.gateway import ExecutionLimits
from packages.platform_contracts.analytics_intent import AnalyticalIntent

LIVE = ("active", "suspended")
PER_DATASET = 2
MAX_STATEMENTS = 4
WARM_LIMITS = ExecutionLimits(timeout_seconds=30.0, max_rows=10, max_bytes=100_000)  # a no-dimension aggregate is one row


def warm_plans(assets: Any, tenant_id: str, *, per_dataset: int = PER_DATASET, cap: int = MAX_STATEMENTS) -> list[Any]:
    """Compiled plans for the warm-up, from the tenant's certified contracts only, chosen in contract order so a rerun picks the same ones."""
    plans: list[Any] = []
    for document in assets.contracts.documents():
        contract = document.contract
        for dataset in contract.datasets:
            for metric in [m for m in contract.metrics if m.dataset_id == dataset.id][:per_dataset]:
                intent = AnalyticalIntent.model_validate({
                    "query_id": "warm-up", "tenant_id": tenant_id, "dataset_id": dataset.id, "metrics": [{"metric_id": metric.id}],
                    "semantic_contract": {"contract_id": contract.id, "contract_version": contract.version}})
                plans.append(assets.compiler.compile(intent, policy_values=None))
                if len(plans) >= cap:
                    return plans
    return plans


class WarmUpService:
    def __init__(self, store: ConnectionStore, assets_for_tenant: Callable[[str], Any | None], *, warmer: Warmer | None = None,
                 submit: Callable[[Callable[[], None]], Any] | None = None, audit: Callable[[str, str, WarmResult], None] | None = None,
                 limits: ExecutionLimits = WARM_LIMITS) -> None:
        self.store, self._assets, self.warmer, self._audit, self._limits = store, assets_for_tenant, warmer or Warmer(), audit, limits
        self._pool = None if submit else ThreadPoolExecutor(max_workers=2, thread_name_prefix="warm-up")
        self._submit = submit or self._pool.submit  # type: ignore[union-attr]
        self._busy = threading.BoundedSemaphore(4)  # at most four warm-ups queued or running at once, whatever is called

    def _connection(self, tenant_id: str) -> ConnectionRecord | None:
        """The tenant's single live Databricks connection with warming on, or None. Anything else (none, several, suspended, off) means nothing runs."""
        live = [r for r in self.store.list(tenant_id) if r.status in LIVE]
        if len(live) != 1:
            return None
        record = live[0]
        return record if record.status == "active" and record.engine == "databricks" and record.warm_up else None

    def request(self, tenant_id: str) -> str:
        """`skipped_disabled`, `skipped_recent`, `skipped_busy` or `warming`. Never raises, never waits for the warehouse."""
        try:
            record = self._connection(tenant_id)
            if record is None:
                return "skipped_disabled"
            if self.warmer.recent(tenant_id, record.connection_id):
                return "skipped_recent"
            if not self._busy.acquire(blocking=False):
                return "skipped_busy"
            try:
                self._submit(lambda: self._run(tenant_id, record))
            except Exception:  # noqa: BLE001
                self._busy.release()
                return "failed"
            return "warming"
        except Exception:  # noqa: BLE001 - a warm-up must never break the request that called it
            return "failed"

    def on_question(self, tenant_id: str) -> None:
        """The fallback at the top of `/analyze`: warm if idle, and otherwise record that the connection is about to be used, so a warm-up is not wasted right after."""
        try:
            record = self._connection(tenant_id)
            if record is None:
                return
            if self.request(tenant_id) != "warming":
                self.warmer.note_activity(tenant_id, record.connection_id)
        except Exception:  # noqa: BLE001
            return

    def _run(self, tenant_id: str, record: ConnectionRecord) -> None:
        try:
            try:
                assets = self._assets(tenant_id)
                plans = warm_plans(assets, tenant_id) if assets is not None else []
            except Exception:  # noqa: BLE001 - a compile or lookup problem is a code, never a message
                self._record(tenant_id, record.connection_id, WarmResult("failed"))
                return
            self.warmer.warm(tenant_id, record.connection_id, plans, lambda plan: assets.gateway.execute(plan, limits=self._limits), enabled=True,
                             on_done=lambda result: self._record(tenant_id, record.connection_id, result))
        finally:
            self._busy.release()

    def _record(self, tenant_id: str, connection_id: str, result: WarmResult) -> None:
        if self._audit is not None:
            try:
                self._audit(tenant_id, connection_id, result)
            except Exception:  # noqa: BLE001 - the audit hook must not break a warm-up
                pass
