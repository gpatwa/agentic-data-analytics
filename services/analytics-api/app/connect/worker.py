"""The Connect-mode setup worker for Snowflake (threat T1, T3, T22, T23): runs the approved setup once, then forgets.

Two phases. `begin` validates the target, generates the key pair (the private key goes straight into the tenant's secret
store and is never held here), builds the script and returns the Approve request. `run` takes the single-use approval,
signs in with the admin token, checks that none of the names exist, executes exactly the approved statements, verifies the
resulting grants, and wipes the token. Any failure drops only what this run created and deletes the credential. Nothing
here logs, and no result, record or exception carries the token.

State is per run (a `PendingSetup` and local variables); nothing is shared between runs. Real process isolation (one
short-lived process or container per run) is a deployment property this code is written to allow but does not provide.
The deadline bounds the setup statements between steps; a single statement is bounded only by the admin session's own timeouts.
Not verified live: the preflight `SHOW` shapes and the rollback against a real account.
"""

from __future__ import annotations

import base64
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.connect.approval import ApprovalError, ApprovalGate, ApprovalRequest
from app.connect.grants import (
    ConnectScope,
    Names,
    SetupScript,
    digest_statements,
    generate_snowflake_setup,
    verify_grants,
    verify_user_roles,
)
from app.connect.records import SetupRecorder
from app.connect.store import ConnectionRecord, ConnectionStore, TenantSecretStore
from app.connect.targets import Resolver, system_resolver, validate_target
from app.connect.token import AdminToken
from app.execution.warehouse_provisioning import ProvisioningError, _exec, new_key_pair
from app.security.audit import AuditChain

DEADLINE_SECONDS = 120.0

_CREATES = (
    ("CREATE ROLE ", "role"),
    ("CREATE RESOURCE MONITOR ", "monitor"),
    ("CREATE WAREHOUSE ", "warehouse"),
    ("CREATE USER ", "user"),
    ("CREATE NETWORK POLICY ", "network policy"),
)
_DROPS = {"role": "DROP ROLE ", "monitor": "DROP RESOURCE MONITOR ", "warehouse": "DROP WAREHOUSE ", "user": "DROP USER ", "network policy": "DROP NETWORK POLICY "}


@dataclass(frozen=True)
class PendingSetup:
    setup_id: str
    tenant_id: str
    connection_id: str
    admin_id: str
    scope: ConnectScope
    names: Names
    script: SetupScript
    request: ApprovalRequest
    secret_name: str
    host: str
    account: str


@dataclass(frozen=True)
class SetupResult:
    ok: bool
    code: str | None
    steps: int = 0
    created: tuple[str, ...] = ()
    leftovers: tuple[str, ...] = ()  # objects that could not be dropped on a failed run; the admin must remove them
    connection_id: str = ""
    teardown_digest: str = ""
    failed_checks: tuple[str, ...] = field(default=())


def _kind_of(statement: str) -> str | None:
    return next((kind for prefix, kind in _CREATES if statement.startswith(prefix)), None)


class SetupCoordinator:
    def __init__(
        self,
        store: ConnectionStore,
        secrets: TenantSecretStore,
        chain: AuditChain,
        gate: ApprovalGate,
        *,
        clock: Callable[[], float] = time.monotonic,
        key_pair: Callable[[], tuple[bytes, str]] = new_key_pair,
        id_source: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._store, self._secrets, self._chain, self._gate = store, secrets, chain, gate
        self._clock, self._key_pair, self._ids = clock, key_pair, id_source
        self._recorders: dict[str, SetupRecorder] = {}

    # -- phase 1 ---------------------------------------------------------------------------------------------------

    def begin(
        self,
        *,
        tenant_id: str,
        admin_id: str,
        requested_by: str,
        host: str,
        account: str,
        scope: ConnectScope,
        credit_quota: int = 1,
        allowed_networks: Iterable[str] = (),
        resolver: Resolver = system_resolver,
    ) -> PendingSetup:
        target = validate_target("snowflake", host, expected_account=account, resolver=resolver)
        setup_id = self._ids()
        connection_id, suffix = f"conn-{setup_id[:12]}", setup_id[:8]
        names = Names.for_connection(suffix)
        private_der, public_body = self._key_pair()
        script = generate_snowflake_setup(scope, suffix=suffix, public_key_body=public_body, credit_quota=credit_quota, allowed_networks=allowed_networks)
        secret_name = f"snowflake-key-{connection_id}"
        self._secrets.put(tenant_id, secret_name, base64.b64encode(private_der).decode("ascii"))
        del private_der
        try:
            record = ConnectionRecord(
                tenant_id=tenant_id, connection_id=connection_id, engine="snowflake", host=target.host, account=target.account,
                user=names.user, role=names.role, warehouse=names.warehouse, database=scope.database,
                default_schema=scope.schemas[0], allowed_tables=scope.allowed_tables, secret_name=secret_name, status="pending",
            )
            self._store.add(record)
            recorder = SetupRecorder(self._chain, tenant_id=tenant_id, connection_id=connection_id, admin_id=admin_id)
            recorder.consent_shown(script.statements, engine="snowflake", host=target.host)
            request = self._gate.present(script, admin_id=admin_id, requested_by=requested_by)
        except Exception:
            self._secrets.delete(tenant_id, secret_name)
            self._discard_record(tenant_id, connection_id)
            raise
        self._recorders[setup_id] = recorder
        return PendingSetup(setup_id, tenant_id, connection_id, admin_id, scope, names, script, request, secret_name, target.host, target.account)

    def cancel(self, pending: PendingSetup) -> None:
        self._gate.cancel(pending.request.request_id)
        self._cleanup(pending, self._recorders.pop(pending.setup_id, None))

    # -- phase 2 ---------------------------------------------------------------------------------------------------

    def run(
        self,
        pending: PendingSetup,
        *,
        connect: Callable[[str], Any],
        token: AdminToken,
        connection_test: Callable[[ConnectionRecord], tuple[bool, Iterable[str]]] | None = None,
    ) -> SetupResult:
        recorder = self._recorders.get(pending.setup_id)
        if recorder is None:
            raise ApprovalError("setup_not_pending")
        conn: Any = None
        created: list[str] = []
        steps = 0
        try:
            # Nothing has run yet: a missing approval or a dead token leaves the request pending (and wipes the token).
            value = token.reveal()
            recorder.watch(value)
            approval = self._gate.take(pending.request.request_id, admin_id=pending.admin_id)
            # From here every outcome ends the setup: it succeeds, or it rolls back and deletes the credential.
            del self._recorders[pending.setup_id]
            try:
                recorder.approved(approval.digest)
                recorder.require_approved(pending.script.statements)
                started = self._clock()
                try:
                    conn = connect(value)
                except Exception as exc:  # noqa: BLE001 - the type only; a driver message could carry the token.
                    return self._fail(pending, recorder, f"admin_connect_failed:{type(exc).__name__}"[:60], conn, created)
                finally:
                    del value
                with_policy = any("NETWORK POLICY" in statement for statement in pending.script.statements)
                if self._preflight(conn, pending.names, with_policy):
                    return self._fail(pending, recorder, "name_taken", conn, created)
                for statement in pending.script.statements:
                    if self._clock() - started > DEADLINE_SECONDS:
                        return self._fail(pending, recorder, "deadline_exceeded", conn, created, steps)
                    _exec(conn, statement, f"setup_step_{steps + 1}")
                    steps += 1
                    kind = _kind_of(statement)
                    if kind:
                        created.append(kind)
                report = verify_grants(_exec(conn, f"SHOW GRANTS TO ROLE {pending.names.role}", "verify_role"), pending.scope, pending.names)
                if report.ok:
                    report = verify_user_roles(_exec(conn, f"SHOW GRANTS TO USER {pending.names.user}", "verify_user"), pending.names)
                if not report.ok:
                    return self._fail(pending, recorder, report.code or "grants_unverified", conn, created, steps)
                recorder.executed(steps=steps, outcome="ok")
            except ProvisioningError as exc:
                return self._fail(pending, recorder, exc.code, conn, created, steps)
            except Exception as exc:  # noqa: BLE001 - unknown failure: roll back, never leave a half setup. Type only.
                return self._fail(pending, recorder, f"internal:{type(exc).__name__}"[:60], conn, created, steps)
            # Setup is done: the admin session and token are finished with before anything else happens.
            token.wipe()
            self._close(conn)
            conn = None
            return self._finish(pending, recorder, steps=steps, created=tuple(created), connection_test=connection_test)
        finally:
            token.wipe()
            self._close(conn)
            recorder.close()

    # -- internals -------------------------------------------------------------------------------------------------

    def _preflight(self, conn: Any, names: Names, with_policy: bool) -> list[str]:
        """Names that already exist. Exact match in Python: `SHOW ... LIKE` treats `_` as a wildcard."""
        taken = []
        checks = [("role", f"SHOW ROLES LIKE '{names.role}'", names.role), ("user", f"SHOW USERS LIKE '{names.user}'", names.user),
                  ("warehouse", f"SHOW WAREHOUSES LIKE '{names.warehouse}'", names.warehouse),
                  ("monitor", f"SHOW RESOURCE MONITORS LIKE '{names.monitor}'", names.monitor)]
        if with_policy:
            checks.append(("network policy", "SHOW NETWORK POLICIES", names.network_policy))
        for kind, show, name in checks:
            if any(str(row.get("name", "")).upper() == name for row in _exec(conn, show, f"check_{kind.replace(' ', '_')}")):
                taken.append(kind)
        return taken

    def _rollback(self, conn: Any, pending: PendingSetup, created: list[str]) -> tuple[list[str], int]:
        """Drop only what this run created. Every drop is attempted; what cannot be dropped is reported."""
        left, dropped = [], 0
        for drop in pending.script.teardown:
            kind = next((k for k, prefix in _DROPS.items() if drop.startswith(prefix)), None)
            if kind not in created:
                continue
            try:
                _exec(conn, drop, "rollback")
                dropped += 1
            except ProvisioningError:
                left.append(f"{kind} {drop.split()[-1]}")
        return left, dropped

    def _fail(self, pending, recorder, code, conn, created, steps=0) -> SetupResult:
        left: list[str] = []
        if conn is not None and created:
            left, dropped = self._rollback(conn, pending, created)
            recorder.torn_down(dropped=dropped)
        recorder.failed(code)
        self._cleanup(pending, recorder)
        return SetupResult(False, code, steps, tuple(created), tuple(left), pending.connection_id)

    def _cleanup(self, pending: PendingSetup, recorder: SetupRecorder | None) -> None:
        """The credential is deleted and the record removed on any failure or cancel."""
        try:
            receipt = self._secrets.delete(pending.tenant_id, pending.secret_name)
            if recorder is not None:
                recorder.credential_deleted(receipt)
        except KeyError:
            pass
        self._discard_record(pending.tenant_id, pending.connection_id)

    def _discard_record(self, tenant_id: str, connection_id: str) -> None:
        try:
            self._store.set_status(tenant_id, connection_id, "removed")
        except Exception:  # noqa: BLE001 - nothing to discard
            pass

    def _finish(self, pending, recorder, *, steps, created, connection_test) -> SetupResult:
        teardown_digest = digest_statements(pending.script.teardown)
        if connection_test is None:
            return SetupResult(True, None, steps, created, (), pending.connection_id, teardown_digest)
        try:
            ok, failed = connection_test(self._store.get(pending.tenant_id, pending.connection_id))
            failed = tuple(sorted(failed))
        except Exception as exc:  # noqa: BLE001 - the type only
            ok, failed = False, (f"test_error:{type(exc).__name__}",)
        recorder.verified(ok=bool(ok), failed_checks=failed)
        if ok:
            self._store.set_status(pending.tenant_id, pending.connection_id, "active")
            return SetupResult(True, None, steps, created, (), pending.connection_id, teardown_digest)
        # The objects stay (they are the customer's to remove with the teardown script); the connection is suspended.
        self._store.set_status(pending.tenant_id, pending.connection_id, "suspended")
        return SetupResult(False, "connection_test_failed", steps, created, (), pending.connection_id, teardown_digest, failed)

    @staticmethod
    def _close(conn: Any) -> None:
        try:
            if conn is not None:
                conn.close()
        except Exception:  # noqa: BLE001
            pass
