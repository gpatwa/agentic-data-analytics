"""Consent, setup and teardown records for Connect mode (threats T13, T14, T15, T22).

What the admin approved is bound to a digest of the exact statements shown, and the run refuses any other statements.
The records never hold an admin token, a private key or a statement body; a guard refuses a payload that would.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.connect.grants import digest_statements
from app.connect.store import DeletionReceipt
from app.security.audit import AuditChain

_FORBIDDEN_KEY = re.compile(r"token|password|secret|private|credential|authorization|statements?$", re.I)
_PEM = re.compile(r"-----BEGIN|PRIVATE KEY", re.I)


class SecretInRecord(ValueError):
    """A record payload carried (or looked like it carried) secret material and was refused."""


class ApprovalMismatch(ValueError):
    """The statements to run are not the ones the admin approved."""


class SetupRecorder:
    def __init__(self, chain: AuditChain, *, tenant_id: str, connection_id: str, admin_id: str) -> None:
        self._chain = chain
        self._tenant, self._connection, self._admin = tenant_id, connection_id, admin_id
        self._watched: list[str] = []
        self._shown: str | None = None
        self._approved: str | None = None

    def watch(self, *values: str) -> None:
        """Register secret values (an admin token) that must never appear in any record. Cleared by `close`."""
        self._watched.extend(v for v in values if v and len(v) >= 8)

    def close(self) -> None:
        self._watched.clear()

    def _scan(self, value: Any, path: str = "") -> None:
        if isinstance(value, Mapping):
            for key, inner in value.items():
                if _FORBIDDEN_KEY.search(str(key)):
                    raise SecretInRecord(f"field name is not allowed in a record: {key}")
                self._scan(inner, f"{path}.{key}")
        elif isinstance(value, (list, tuple, set, frozenset)):
            for inner in value:
                self._scan(inner, path)
        elif isinstance(value, (bytes, bytearray)):
            raise SecretInRecord("binary values are not allowed in a record")
        elif isinstance(value, str):
            if _PEM.search(value) or any(w in value for w in self._watched):
                raise SecretInRecord("a record payload contained secret material")

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self._scan(payload)
        self._chain.append(f"{self._connection}:{len(self._chain.events) + 1}", event_type, self._tenant, self._admin, payload)

    def consent_shown(self, statements: Sequence[str], *, engine: str, host: str) -> str:
        digest = digest_statements(statements)
        self._shown = digest
        self._emit("connect.consent_shown", {"engine": engine, "host": host, "statement_count": len(statements), "digest": digest})
        return digest

    def approved(self, digest: str) -> None:
        if self._shown is None or digest != self._shown:
            raise ApprovalMismatch("approval does not match what was shown")
        self._approved = digest
        self._emit("connect.setup_approved", {"digest": digest})

    def require_approved(self, statements: Sequence[str]) -> None:
        """Call immediately before executing: refuse anything but the approved statements."""
        if self._approved is None or digest_statements(statements) != self._approved:
            raise ApprovalMismatch("statements differ from the approved list")

    def executed(self, *, steps: int, outcome: str) -> None:
        self._emit("connect.setup_executed", {"steps": steps, "outcome": outcome, "digest": self._approved})

    def failed(self, code: str) -> None:
        self._emit("connect.setup_failed", {"code": code})

    def verified(self, *, ok: bool, failed_checks: Iterable[str]) -> None:
        self._emit("connect.verified", {"ok": ok, "failed_checks": sorted(failed_checks)})

    def torn_down(self, *, dropped: int) -> None:
        self._emit("connect.torn_down", {"dropped": dropped})

    def script_run_reported(self) -> None:
        """The admin says they ran the script. A claim, not a fact: the connection test is what checks it."""
        self._emit("connect.script_run_reported", {})

    def access_removed(self, *, teardown_digest: str) -> None:
        self._emit("connect.access_removed", {"teardown_digest": teardown_digest})

    def credential_deleted(self, receipt: DeletionReceipt) -> None:
        self._emit("connect.credential_deleted", {"ref": receipt.secret_name, "deleted_at": receipt.deleted_at.isoformat()})
