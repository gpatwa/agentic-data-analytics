"""Temporary warehouses, one per size, created and deleted by the run. The shared warehouse is never resized."""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol

from reference_stack.loaders.databricks_cli import EngineError

PREFIX = "compass-scale-"
SIZES = ("2X-Small", "X-Small", "Small", "Medium")
# ASSUMED DBU per hour by size: a placeholder to be checked against the vendor's current table, never a quote.
DBU_PER_HOUR = {"2X-Small": 4.0, "X-Small": 6.0, "Small": 12.0, "Medium": 24.0}


class Api(Protocol):
    def api(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]: ...


class WarehouseAdmin:
    def __init__(self, api: Api, *, auto_stop_mins: int = 2, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep, start_timeout: float = 600.0) -> None:
        self._api, self._clock, self._sleep, self._start_timeout, self._auto_stop = api, clock, sleep, start_timeout, auto_stop_mins
        self.created: dict[str, tuple[str, float]] = {}  # id -> (size, created_at)

    def create(self, size: str, run_id: str) -> str:
        if size not in DBU_PER_HOUR:
            raise EngineError("unknown_size")
        body = {"name": f"{PREFIX}{size.lower()}-{run_id}", "cluster_size": size, "min_num_clusters": 1, "max_num_clusters": 1, "auto_stop_mins": self._auto_stop,
                "enable_serverless_compute": True, "warehouse_type": "PRO"}
        wid = str(self._api.api("post", "/api/2.0/sql/warehouses", body)["id"])
        self.created[wid] = (size, self._clock())
        return wid

    def wait_running(self, wid: str) -> float:
        start = self._clock()
        while self._api.api("get", f"/api/2.0/sql/warehouses/{wid}").get("state") != "RUNNING":
            if self._clock() - start > self._start_timeout:
                raise EngineError("warehouse_did_not_start")
            self._sleep(5.0)
        return self._clock() - start

    def delete(self, wid: str) -> None:
        try:
            self._api.api("delete", f"/api/2.0/sql/warehouses/{wid}")
        except EngineError as exc:
            if str(exc) != "cli_output_not_json":  # a delete may answer with an empty body
                raise

    def release(self, wid: str) -> float:
        """Delete one warehouse now; the seconds from its creation to its deletion (what it could have been billed for)."""
        _size, at = self.created.pop(wid)
        self.delete(wid)
        return self._clock() - at

    def remaining(self) -> list[str]:
        """Names of any scale-test warehouse still present, whoever made it."""
        listing = self._api.api("get", "/api/2.0/sql/warehouses").get("warehouses") or []
        return sorted(w["name"] for w in listing if str(w.get("name", "")).startswith(PREFIX))

    def teardown(self) -> list[str]:
        """Delete every warehouse this run created, then prove none remains. Returns the names still present (empty when clean)."""
        failures = 0
        for wid in list(self.created):
            try:
                self.delete(wid)
            except EngineError:
                failures += 1
        try:
            left = self.remaining()
        except EngineError:
            left = ["<could not list warehouses>"]
        return left if (left or failures) else []
