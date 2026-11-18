"""The Approve step (threat T1, T15): the admin approves an exact list of statements, once, before a deadline.

The UI shows `summary` and `statements`; the admin's click calls `approve` with the identity from the vendor sign-in. A run
takes the approval exactly once. The requester who started the flow may differ from the admin, but only the signed-in admin
can approve.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.connect.grants import SetupScript, digest_statements

DEFAULT_TTL_SECONDS = 300.0
MAX_TTL_SECONDS = 900.0


class ApprovalError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    admin_id: str
    requested_by: str
    summary: tuple[str, ...]
    statements: tuple[str, ...]
    digest: str
    expires_at: float


class ApprovalGate:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        if not 0 < ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError("invalid approval lifetime")
        self._clock, self._ttl = clock, ttl_seconds
        self._lock = threading.Lock()
        self._requests: dict[str, ApprovalRequest] = {}
        self._approved: set[str] = set()

    def present(self, script: SetupScript, *, admin_id: str, requested_by: str) -> ApprovalRequest:
        if not admin_id:
            raise ApprovalError("admin_required")
        if script.digest != digest_statements(script.statements):
            raise ApprovalError("script_digest_mismatch")
        request = ApprovalRequest(
            request_id=secrets.token_urlsafe(16),
            admin_id=admin_id,
            requested_by=requested_by,
            summary=script.summary,
            statements=script.statements,
            digest=script.digest,
            expires_at=self._clock() + self._ttl,
        )
        with self._lock:
            self._requests[request.request_id] = request
        return request

    def _live(self, request_id: str, admin_id: str) -> ApprovalRequest:
        request = self._requests.get(request_id)
        if request is None:
            raise ApprovalError("unknown_request")
        if request.admin_id != admin_id:
            raise ApprovalError("wrong_admin")
        if self._clock() >= request.expires_at:
            self._requests.pop(request_id, None)
            self._approved.discard(request_id)
            raise ApprovalError("approval_expired")
        return request

    def approve(self, request_id: str, *, admin_id: str, digest: str) -> None:
        with self._lock:
            request = self._live(request_id, admin_id)
            if digest != request.digest:
                raise ApprovalError("digest_mismatch")
            self._approved.add(request_id)

    def take(self, request_id: str, *, admin_id: str) -> ApprovalRequest:
        """Consume the approval. A second call, an unapproved request or an expired one fails."""
        with self._lock:
            request = self._live(request_id, admin_id)
            if request_id not in self._approved:
                raise ApprovalError("not_approved")
            self._approved.discard(request_id)
            self._requests.pop(request_id, None)
            return request

    def is_approved(self, request_id: str, *, admin_id: str) -> bool:
        """True while the request is approved, unexpired and not yet taken. Never consumes it."""
        with self._lock:
            try:
                self._live(request_id, admin_id)
            except ApprovalError:
                return False
            return request_id in self._approved

    def cancel(self, request_id: str) -> None:
        with self._lock:
            self._requests.pop(request_id, None)
            self._approved.discard(request_id)
