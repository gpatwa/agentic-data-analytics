"""Which warehouse a tenant's governed run executes on (docs/design/CONNECT_DATABRICKS_DESIGN.md).

`connected_gateway_resolver` is handed to `GovernedRuntime.gateway_for_tenant`. A tenant with no live connection gets `None` (the
pilot's own gateway, as before). A tenant with exactly one active connection gets that connection's gateway, built from its own
record. Anything else (suspended, more than one) raises, so the run fails closed: a connected tenant never silently falls back to
another warehouse, and one tenant's connection is never used for another.

What this does not do: the plan is compiled by the runtime's certified compiler for its own dialect, and the gateway is keyed by
that dialect, so a connected warehouse answers a question only when certified contracts and a compiler exist for its tables. That
certification is a human gate and is not built here.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.connect.gateway import ConnectionUnavailable, gateway_for
from app.connect.store import ConnectionStore

LIVE = ("active", "suspended")  # pending (mid-setup) and removed connections route nowhere


def connected_gateway_resolver(store: ConnectionStore, secrets: Any, *, ttl_seconds: float = 300.0, clock: Callable[[], float] = time.monotonic,
                               **options: Any) -> Callable[[str], Any | None]:
    """Gateways are cheap (sessions open per query) but reading custody and rebuilding the config on every request is wasteful, so a
    gateway is reused for `ttl_seconds` per (tenant, connection, secret version). A rotated or deleted secret changes the version,
    and a removed or suspended connection is refused before the cache is consulted, so a cached gateway can never outlive either."""
    cache: dict[tuple[str, str, Any], tuple[float, Any]] = {}

    def resolve(tenant_id: str) -> Any | None:
        live = [r for r in store.list(tenant_id) if r.status in LIVE]
        if not live:
            return None
        if len(live) > 1:
            raise ConnectionUnavailable("connection_ambiguous")
        record = live[0]
        if record.status != "active":
            raise ConnectionUnavailable("connection_not_active")
        try:
            version: Any = secrets.version(tenant_id, record.secret_name or "")
        except Exception as exc:  # noqa: BLE001 - never echo why
            raise ConnectionUnavailable("credential_unavailable") from exc
        key = (tenant_id, record.connection_id, version)
        now = clock()
        hit = cache.get(key)
        if hit is not None and now < hit[0]:
            return hit[1]
        gateway = gateway_for(store, secrets, tenant_id, record.connection_id, **options)
        for stale in [k for k in cache if k[:2] == key[:2]]:
            del cache[stale]
        cache[key] = (now + ttl_seconds, gateway)
        return gateway

    return resolve
