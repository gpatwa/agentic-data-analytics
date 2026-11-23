"""Drift detection and secret-age warnings for Connect on Databricks (docs/design/SELF_SERVE_CONNECT_DESIGN.md, layer 3), so nobody has to remember a date.

`DriftMonitor.tick` does two things:
- the EXPIRY SWEEP, every tick and free (it reads only the connection records, never the customer's warehouse): a connection whose secret has ended is paused (`suspended`, so the
  gateway stops using it) with an audit event, and one that is within `ROTATE_WITHIN_DAYS` of ending gets one `connect.secret_expiring` event (the screens show the same warning);
- the READ CHECK, only when `read_check_interval` is set: the existing health check (read each named table as the principal) for every active connection. It runs short queries on
  the customer's warehouse and starts it if it was stopped, so it is OFF unless the owner sets `ANALYTICS_CONNECT_DRIFT_CHECK_HOURS`; a failed read pauses the connection.
Every event carries codes and counts only. A failure of the monitor itself is swallowed: it must never take the API down. Runs on fakes only so far."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

ROTATE_WITHIN_DAYS = 30  # the secret lasts 90 days: warn once 60 have passed
SYSTEM_ACTOR = "system:drift-monitor"


class DriftMonitor:
    def __init__(self, setups: Any, session_factory: Callable[[Any], Any], *, expiry_interval: float = 3600.0, read_check_interval: float = 0.0,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.setups, self.session_factory, self._clock = setups, session_factory, clock
        self.expiry_interval, self.read_check_interval = expiry_interval, read_check_interval
        self._warned: set[tuple[str, str]] = set()
        self._last_read_check: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _event(self, record: Any, kind: str, payload: dict[str, Any]) -> None:
        chain = self.setups.chain
        chain.append(f"{record.connection_id}:{kind}:{len(chain.events) + 1}", f"connect.{kind}", record.tenant_id, SYSTEM_ACTOR, {"connection_id": record.connection_id, **payload})

    def tick(self, now: datetime | None = None) -> dict[str, int]:
        moment = now or self._clock()
        summary = {"expired": 0, "warned": 0, "read_checked": 0, "paused": 0}
        try:
            records = [r for r in self.setups.store.all() if r.engine == "databricks" and r.auth_method == "oauth_m2m" and r.status in ("active", "suspended")]
            for record in records:
                expires = record.secret_expires_at
                if expires is None:
                    continue
                if expires <= moment:
                    if record.status == "active":
                        self.setups.store.set_status(record.tenant_id, record.connection_id, "suspended")
                        self._event(record, "secret_expired", {"expires_at": expires.isoformat()})
                        summary["expired"] += 1
                elif (expires - moment).days <= ROTATE_WITHIN_DAYS and (record.connection_id, expires.isoformat()) not in self._warned:
                    self._warned.add((record.connection_id, expires.isoformat()))
                    self._event(record, "secret_expiring", {"days_left": (expires - moment).days, "expires_at": expires.isoformat()})
                    summary["warned"] += 1
            if self.read_check_interval > 0 and (self._last_read_check is None or (moment - self._last_read_check).total_seconds() >= self.read_check_interval):
                self._last_read_check = moment
                for record in self.setups.store.all():
                    if record.engine == "databricks" and record.auth_method == "oauth_m2m" and record.status == "active":
                        result = self.setups.health_check(record.tenant_id, record.connection_id, actor_id=SYSTEM_ACTOR, session_factory=self.session_factory, now=moment)
                        summary["read_checked"] += 1
                        summary["paused"] += 0 if result.ok else 1
        except Exception:  # noqa: BLE001 - the monitor must never take the API down; the next tick tries again
            pass
        return summary

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(self.expiry_interval):
                self.tick()

        self._thread = threading.Thread(target=loop, name="connect-drift-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
