"""The Connect-mode connection test (threat T3, T22): what Compass can check on a customer's own data.

Two of the eight conformance checks run here, because they need no known-answer table: `read_only_grants` (the role holds
only what the script gave it) and `metadata_read` (each named table can be described). The other six (compiled query,
result equivalence, cost ceiling, timeout, cancel, no leaks) need a table whose contents are known, so they run on the
sample data, not on a customer's. The screen says so; it never reports eight checks passed on customer data.

Failures are reported as check names only: a driver message can carry SQL or values. Not verified against a live account:
the `SHOW GRANTS` and `DESCRIBE TABLE` result shapes through the driver session.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.connect.grants import verify_grants
from app.connect.store import ConnectionRecord
from app.connect.worker import PendingSetup

CHECK_WORDS = {
    "read_only_grants": "Your access is read-only",
    "metadata_read": "We can read your table layout",
}
NOT_RUN_ON_CUSTOMER_DATA = (
    "A governed query ran",
    "Results match what we expected",
    "A too-expensive query is refused",
    "A runaway query is stopped",
    "A cancelled query stops",
    "Nothing sensitive leaks",
)


def _rows(stream: Any) -> list[dict[str, Any]]:
    columns = [str(c).lower() for c in stream.columns]
    rows = []
    while True:
        batch = stream.fetchmany(256)
        if not batch:
            break
        rows += [dict(zip(columns, row, strict=False)) for row in batch]
    stream.close()
    return rows


def grants_connection_test(
    session_factory: Callable[[ConnectionRecord], Any], pending: PendingSetup
) -> Callable[[ConnectionRecord], tuple[bool, list[str]]]:
    """Build the test for one pending setup. `session_factory` opens a session as the service identity."""

    def test(record: ConnectionRecord) -> tuple[bool, list[str]]:
        failed: list[str] = []
        session = None
        try:
            session = session_factory(record)
            session.prepare(timeout_seconds=30.0)
            try:
                rows = _rows(session.execute(f"SHOW GRANTS TO ROLE {pending.names.role}", {}))
                if not verify_grants(rows, pending.scope, pending.names).ok:
                    failed.append("read_only_grants")
            except Exception:  # noqa: BLE001 - names only
                failed.append("read_only_grants")
            try:
                for schema, table in pending.scope.tables:
                    quoted = ".".join('"' + p.replace('"', '""') + '"' for p in (pending.scope.database, schema, table))
                    if not _rows(session.execute(f"DESCRIBE TABLE {quoted}", {})):
                        raise ValueError("no columns")
            except Exception:  # noqa: BLE001
                failed.append("metadata_read")
        except Exception:  # noqa: BLE001 - could not connect as the service identity
            failed = list(CHECK_WORDS)
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:  # noqa: BLE001
                    pass
        return (not failed, failed)

    return test
