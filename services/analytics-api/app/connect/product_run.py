"""Product-run setup (CM5c): the customer registered Compass once; each connection afterwards is set up inside a ceiling,
after the admin approves the exact statements and signs in on Snowflake's own page.

This wires the pieces: the registration (CM5a) and its per-connection statements held inside the scope ceiling, the hosted
sign-in whose token waits in a single-use hand-off (CM5b), and the setup worker (CM3) that runs the approved statements once,
verifies the result, rolls back only what it created, and wipes the token. Two checks run **after sign-in and before anything
is created**: the token must belong to the registered account (T27) and must have selected the registered setup role, and the
setup role's grants must still equal exactly what the registration created (so a widened or narrowed ceiling stops the run).

The order is enforced: a setup is approved first, the admin signs in second, and only then does it run. `run` refuses without an
approval **before** touching the token, so a premature call cannot waste a sign-in.

Not verified against a live account: the connector's OAuth sign-in through this path (the spike used the same connector call
with a token from the same kind of integration, and it worked), and the full chain end to end. Limits: in-memory stores.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from app.connect.admin_sql import new_key_pair, run_statement
from app.connect.approval import ApprovalError, ApprovalGate
from app.connect.grants import ConnectScope, SetupScript, digest_statements
from app.connect.oauth import ConfigStore, OAuthClientConfig, TokenHandoff
from app.connect.registration import RegNames, per_connection_statements, verify_setup_role_grants
from app.connect.store import ConnectionRecord
from app.connect.targets import VENDOR_SUFFIXES, Resolver, system_resolver, validate_target
from app.connect.worker import PendingSetup, SetupCoordinator, SetupResult

_LOCATOR = re.compile(r"^[A-Z0-9_]{3,40}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class RegistrationNotFound(KeyError):
    """Absent or another tenant's: deliberately indistinguishable."""


class RegistrationConflict(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Registration:
    tenant_id: str
    registration_id: str
    config: OAuthClientConfig
    account_locator: str  # CURRENT_ACCOUNT() on the customer's account; the sign-in must land there
    ceiling: ConnectScope
    names: RegNames
    setup_users: tuple[str, ...]

    def __post_init__(self) -> None:
        if (self.config.tenant_id, self.config.registration_id) != (self.tenant_id, self.registration_id):
            raise ValueError("the OAuth config belongs to a different registration")
        if self.config.setup_role != self.names.setup_role:
            raise ValueError("the OAuth config's role is not this registration's setup role")
        if not _LOCATOR.match(self.account_locator):
            raise ValueError("invalid account locator")
        if not 1 <= len(self.setup_users) <= 10:
            raise ValueError("between 1 and 10 setup users")


class RegistrationStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[tuple[str, str], Registration] = {}

    def add(self, registration: Registration) -> None:
        with self._lock:
            key = (registration.tenant_id, registration.registration_id)
            if key in self._items:
                raise RegistrationConflict("registration_id_taken")
            for other in self._items.values():
                if other.config.host == registration.config.host and other.tenant_id != registration.tenant_id:
                    raise RegistrationConflict("target_claimed")
            self._items[key] = registration

    def get(self, tenant_id: str, registration_id: str) -> Registration:
        try:
            return self._items[(tenant_id, registration_id)]
        except KeyError:
            raise RegistrationNotFound("registration not found") from None

    def list(self, tenant_id: str) -> list[Registration]:
        return [r for (t, _), r in self._items.items() if t == tenant_id]


def snowflake_oauth_connect(host: str, *, connector: Any | None = None) -> Callable[[str], Any]:
    """A function that opens a Snowflake session from an OAuth access token. The driver is imported only when it is called."""
    suffix = next(s for s in VENDOR_SUFFIXES["snowflake"] if host.endswith(s))
    account = host[: -len(suffix)]

    def connect(token: str) -> Any:
        driver = connector
        if driver is None:
            import snowflake.connector as driver  # type: ignore[no-redef]
        return driver.connect(account=account, authenticator="oauth", token=token, login_timeout=60, network_timeout=60)

    return connect


class ProductRunService:
    def __init__(
        self,
        registrations: RegistrationStore,
        oauth_configs: ConfigStore,
        coordinator: SetupCoordinator,
        gate: ApprovalGate,
        handoff: TokenHandoff,
        *,
        connect_factory: Callable[[str], Callable[[str], Any]] = snowflake_oauth_connect,
        key_pair: Callable[[], tuple[bytes, str]] = new_key_pair,
    ) -> None:
        self._registrations, self._configs, self._coordinator = registrations, oauth_configs, coordinator
        self._gate, self._handoff, self._connect_factory, self._key_pair = gate, handoff, connect_factory, key_pair
        self._setup_registration: dict[str, str] = {}

    # -- the one-time registration, after the admin ran the script -------------------------------------------------------

    def register(
        self,
        *,
        tenant_id: str,
        registration_id: str,
        reg_suffix: str,
        host: str,
        client_id: str,
        account_locator: str,
        ceiling: ConnectScope,
        setup_users: Iterable[str],
        redirect_uri: str,
        resolver: Resolver = system_resolver,
        allow_local_dev_redirect: bool = False,
    ) -> Registration:
        names = RegNames.for_registration(reg_suffix)
        config = OAuthClientConfig(tenant_id, registration_id, host, client_id, names.setup_role, redirect_uri, allow_local_dev_redirect)
        suffix = next(s for s in VENDOR_SUFFIXES["snowflake"] if host.endswith(s))
        validate_target("snowflake", host, expected_account=host[: -len(suffix)], resolver=resolver)  # a public Snowflake address, no private range
        registration = Registration(tenant_id, registration_id, config, account_locator.upper(), ceiling, names, tuple(dict.fromkeys(setup_users)))
        self._registrations.add(registration)
        try:
            self._configs.add(config)
        except ValueError:
            raise RegistrationConflict("registration_id_taken") from None
        return registration

    def registration(self, tenant_id: str, registration_id: str) -> Registration:
        return self._registrations.get(tenant_id, registration_id)

    def registrations(self, tenant_id: str) -> list[Registration]:
        return self._registrations.list(tenant_id)

    def registration_of(self, setup_id: str) -> str:
        return self._setup_registration.get(setup_id, "")

    # -- one connection ---------------------------------------------------------------------------------------------------

    def begin(self, *, tenant_id: str, user_id: str, registration_id: str, scope: ConnectScope) -> PendingSetup:
        """Plan the statements inside the ceiling and present them for approval. Nothing is created in the customer's account."""
        registration = self._registrations.get(tenant_id, registration_id)
        setup_id = self._coordinator.new_setup_id()
        private_der, public_body = self._key_pair()
        statements, names = per_connection_statements(
            registration.names, registration.ceiling, scope, connection_suffix=setup_id[:8], public_key_body=public_body
        )
        teardown = (f"DROP USER {names.user}", f"DROP ROLE {names.role}")
        summary = (
            f"Create one read-only role ({names.role}) and one service user with a key you can revoke, for {len(scope.tables)} table(s) "
            f"inside the ceiling you allowed at registration.",
            "Compass runs this as your setup role, once, after you sign in on Snowflake's own page. It cannot grant anything outside the ceiling.",
            "The new role cannot write, change or delete anything and cannot read any other table.",
        )
        script = SetupScript(statements, teardown, summary, digest_statements(statements))
        pending = self._coordinator.begin_planned(
            setup_id=setup_id, tenant_id=tenant_id, admin_id=user_id, requested_by=user_id, host=registration.config.host,
            account=registration.config.host[: -len(next(x for x in VENDOR_SUFFIXES["snowflake"] if registration.config.host.endswith(x)))],
            scope=scope, names=names, script=script, private_der=private_der,
            preflight=("role", "user"),
        )
        self._setup_registration[setup_id] = registration_id
        return pending

    def approve(self, pending: PendingSetup, *, user_id: str, digest: str) -> None:
        self._gate.approve(pending.request.request_id, admin_id=user_id, digest=digest)

    def state(self, pending: PendingSetup) -> tuple[bool, bool]:
        """(approved, signed_in) without consuming either."""
        return (
            self._gate.is_approved(pending.request.request_id, admin_id=pending.admin_id),
            self._handoff.holds(pending.tenant_id, pending.setup_id),
        )

    def _identity_check(self, registration: Registration) -> Callable[[Any], str | None]:
        def check(conn: Any) -> str | None:
            rows = run_statement(conn, "SELECT CURRENT_ACCOUNT() AS acct, CURRENT_ROLE() AS role", "identity_check")
            if not rows or str(rows[0].get("acct", "")).upper() != registration.account_locator:
                return "wrong_account"
            if str(rows[0].get("role", "")).upper() != registration.names.setup_role:
                return "wrong_role"
            grants = run_statement(conn, f"SHOW GRANTS TO ROLE {registration.names.setup_role}", "registration_check")
            report = verify_setup_role_grants(grants, registration.ceiling, registration.names, account=registration.account_locator)
            return None if report.ok else "registration_changed"

        return check

    def run(
        self,
        pending: PendingSetup,
        *,
        connection_test: Callable[[ConnectionRecord], tuple[bool, Iterable[str]]] | None = None,
    ) -> SetupResult:
        registration = self._registrations.get(pending.tenant_id, self._setup_registration.get(pending.setup_id, ""))
        # Approval first, and without touching the token: a premature call must not waste the admin's sign-in.
        if not self._gate.is_approved(pending.request.request_id, admin_id=pending.admin_id):
            raise ApprovalError("not_approved")
        token = self._handoff.take(pending.tenant_id, pending.setup_id)  # TokenHandoffError: no_token or token_expired
        return self._coordinator.run(
            pending, connect=self._connect_factory(registration.config.host), token=token, connection_test=connection_test,
            identity_check=self._identity_check(registration),
        )
