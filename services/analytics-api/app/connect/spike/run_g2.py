"""Gate G2 of the product-run spike: sign in over OAuth as the delegated setup role, run the per-setup statements, the
negative tests and the service-user checks, then tear down what the setup role created.

Live against the owner's Snowflake account only with `--allow-live` and a matching `--expect-account`, and only after the
owner approved G2 (docs/design/CONNECT_PRODUCT_RUN_SPIKE_PLAN.md). It never reads a client secret, never prints or stores a
token or key, and stops on the first thing it did not expect: a negative test that succeeds, an existing object that is not
ours, or a token about to expire. On a stop it drops what it created through the same session, best effort, and says what
is left for G3. The report holds names, outcomes and error codes only.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from app.connect.grants import ConnectScope, verify_grants
from app.connect.grants import Names as GrantNames
from app.connect.spike import statements as st
from app.connect.spike.oauth_flow import (
    FlowError,
    HeldToken,
    authorize_url,
    exchange_code,
    new_pkce,
    new_state,
    wait_for_callback,
)

MIN_TOKEN_SECONDS = 20.0


class StopRun(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Step:
    name: str
    question: str
    expected: str  # "succeed" | "refuse" | "info"
    observed: str = ""  # "succeeded" | "refused" | "error"
    ok: bool = False
    detail: str = ""


@dataclass
class Report:
    suffix: str
    steps: list[Step] = field(default_factory=list)
    stopped: str = ""
    left_for_g3: list[str] = field(default_factory=list)

    def add(self, step: Step) -> Step:
        self.steps.append(step)
        return step

    def as_dict(self) -> dict[str, Any]:
        failed = [s.name for s in self.steps if not s.ok]
        return {"suffix": self.suffix, "stopped": self.stopped, "all_as_expected": not failed and not self.stopped,
                "unexpected": failed, "left_for_g3": self.left_for_g3, "steps": [asdict(s) for s in self.steps]}


def _scrub(text: str, secrets_: Iterable[str]) -> str:
    for value in secrets_:
        if value:
            text = text.replace(value, "[redacted]")
    return text[:200]


class Session:
    """One live connection: runs a statement and reports success or a refusal as data. Errors are never raised past here."""

    def __init__(self, conn: Any, secrets_: Callable[[], list[str]]) -> None:
        self.conn, self._secrets = conn, secrets_

    def run(self, statement: str) -> tuple[bool, list[tuple], str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(statement)
            rows = list(cursor.fetchall()) if getattr(cursor, "description", None) else []
            columns = [str(d[0]).lower() for d in (cursor.description or [])]
            return True, [dict(zip(columns, row, strict=False)) for row in rows], ""
        except Exception as exc:  # noqa: BLE001 - a refusal is the data we want
            errno = getattr(exc, "errno", None)
            return False, [], _scrub(f"errno {errno}: {getattr(exc, 'msg', None) or type(exc).__name__}", self._secrets())
        finally:
            try:
                cursor.close()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass


class G2Runner:
    def __init__(
        self,
        state: dict[str, Any],
        *,
        account_base: str,
        client_id: str,
        connect_oauth: Callable[[str], Any],
        connect_key: Callable[[str, bytes, str, str], Any],
        authorize_sink: Callable[[str], None],
        key_pair: Callable[[], tuple[bytes, str]],
        exchange: Callable[..., Any] = exchange_code,
        wait: Callable[..., str] = wait_for_callback,
        callback_timeout: float = 300.0,
    ) -> None:
        self.state, self.account_base, self.client_id = state, account_base.rstrip("/"), client_id
        self.connect_oauth, self.connect_key, self.sink = connect_oauth, connect_key, authorize_sink
        self.key_pair, self.exchange, self.wait, self.callback_timeout = key_pair, exchange, wait, callback_timeout
        self.names = st.Names.for_run(state["suffix"])
        self.report = Report(state["suffix"])
        self._secret_values: list[str] = []
        self._created: list[str] = []

    def _expect(self, session: Session, step: Step, statement: str) -> bool:
        ok, _, detail = session.run(statement)
        step.detail = detail
        if step.expected == "succeed":
            step.observed, step.ok = ("succeeded" if ok else "refused"), ok
        else:
            step.observed, step.ok = ("succeeded" if ok else "refused"), not ok
        self.report.add(step)
        return ok

    def _budget(self, token: HeldToken) -> None:
        if token.seconds_left() < MIN_TOKEN_SECONDS:
            raise StopRun("token_expiring")

    def run(self) -> dict[str, Any]:
        n = self.names
        verifier, challenge = new_pkce()
        oauth_state = new_state()
        redirect = f"http://localhost:{self.state['port']}/callback"
        self.sink(authorize_url(self.account_base, self.client_id, redirect, oauth_state, challenge, n.setup_role))
        setup: Session | None = None
        token: HeldToken | None = None
        try:
            try:
                code = self.wait(self.state["port"], oauth_state, timeout=self.callback_timeout)
            except FlowError as exc:
                self.report.add(Step("sign in and consent", "Q8", "succeed", "refused", False, f"{exc.code} {exc.detail}".strip()))
                raise StopRun("no_authorization_code") from exc
            self._secret_values.append(code)
            try:
                result = self.exchange(f"{self.account_base}/oauth/token-request", self.client_id, code, redirect, verifier)
            except FlowError as exc:
                self.report.add(Step("code exchange, public client, PKCE, no secret", "Q6", "succeed", "refused", False, f"{exc.code} {exc.detail}".strip()))
                raise StopRun("token_exchange_failed") from exc
            token = result.token
            self.report.add(Step("code exchange, public client, PKCE, no secret", "Q6", "succeed", "succeeded", True))
            self.report.add(Step("access token lifetime", "Q9", "info", "succeeded", 540 <= result.expires_in <= 600,
                                 f"expires_in={int(result.expires_in)}s, refresh token issued={result.has_refresh_token}"))
            self.report.add(Step("no refresh token issued", "Q9", "succeed", "succeeded" if not result.has_refresh_token else "refused",
                                 not result.has_refresh_token))
            value = token.reveal()
            self._secret_values.append(value)
            setup = Session(self.connect_oauth(value), lambda: list(self._secret_values))
            del value
            ok, rows, detail = setup.run("SELECT CURRENT_ROLE() AS role, CURRENT_USER() AS usr")
            role = (rows[0]["role"] if ok and rows else "")
            self.report.add(Step("the token selected the delegated role", "Q8", "succeed", "succeeded" if ok else "refused",
                                 ok and role == n.setup_role, f"role={role}" if ok else detail))
            if not ok or role != n.setup_role:
                raise StopRun("wrong_role")

            existing = setup.run(f"SHOW ROLES LIKE '{n.ro_role}'")[1] + setup.run(f"SHOW USERS LIKE '{n.svc_user}'")[1]
            if existing:
                raise StopRun("object_exists_that_is_not_ours")

            der, public = self.key_pair()
            for label, statement in zip(("create role", "create user", "set user key", "usage on database", "usage on schema",
                                         "select on orders", "select on customers", "usage on warehouse", "grant role to user"),
                                        st.per_setup(n, public), strict=True):
                self._budget(token)
                if not self._expect(setup, Step(f"per-setup: {label}", "Q1/Q2/Q3", "succeed"), statement):
                    raise StopRun(f"per_setup_refused:{label}")
                if label in ("create role", "create user"):
                    self._created.append(label.split()[1])

            for label, statement in st.negative_tests(n):
                self._budget(token)
                if self._expect(setup, Step(f"negative: {label}", "Q5", "refuse"), statement):
                    raise StopRun(f"negative_test_succeeded:{label}")

            self._service_user_checks(der)

            for statement in st.setup_role_teardown(n):
                self._budget(token)
                self._expect(setup, Step(f"teardown by setup role: {statement.split()[1].lower()}", "Q10", "succeed"), statement)
            gone = not setup.run(f"SHOW ROLES LIKE '{n.ro_role}'")[1] and not setup.run(f"SHOW USERS LIKE '{n.svc_user}'")[1]
            self.report.add(Step("nothing the setup role created remains", "Q10", "succeed", "succeeded" if gone else "refused", gone))
            self._created.clear()
        except StopRun as stop:
            self.report.stopped = stop.reason
        except Exception as exc:  # noqa: BLE001 - anything unexpected is a stop, with the type only
            self.report.stopped = f"unexpected:{type(exc).__name__}"
        finally:
            if setup is not None:
                self._emergency_cleanup(setup)
                setup.close()
            if token is not None:
                token.wipe()
            self._secret_values.clear()
        return self.report.as_dict()

    def _service_user_checks(self, der: bytes) -> None:
        n = self.names
        try:
            conn = self.connect_key(n.svc_user, der, n.ro_role, n.warehouse)
        except Exception as exc:  # noqa: BLE001
            self.report.add(Step("connect as the new service user with its key", "Q10", "succeed", "refused", False, type(exc).__name__))
            raise StopRun("service_user_connect_failed") from exc
        svc = Session(conn, lambda: list(self._secret_values))
        try:
            self.report.add(Step("connect as the new service user with its key", "Q10", "succeed", "succeeded", True))
            for expected_ok, statement in st.service_user_checks(n):
                step = Step(f"service user: {statement[:60]}", "Q10/Q11", "succeed" if expected_ok else "refuse")
                ok, rows, detail = svc.run(statement)
                step.observed = "succeeded" if ok else "refused"
                step.ok = ok == expected_ok
                step.detail = detail if not ok else f"{len(rows)} row(s)"
                if statement.startswith("SHOW GRANTS TO ROLE") and ok:
                    scope = ConnectScope(n.db, ((n.schema, "orders"), (n.schema, "customers")))
                    names = GrantNames(role=n.ro_role, user=n.svc_user, warehouse=n.warehouse, monitor=n.monitor, network_policy="")
                    report = verify_grants(rows, scope, names)
                    step.name = "service user reads its own grants; the Connect-mode verifier accepts them"
                    step.ok = report.ok
                    step.detail = f"{len(rows)} row(s); {report.code or 'verified'}"
                self.report.add(step)
        finally:
            svc.close()

    def _emergency_cleanup(self, setup: Session) -> None:
        """Best effort, through the setup role: drop what this run created. Anything left is for G3."""
        for kind in ("user", "role"):
            if kind in self._created:
                statement = st.setup_role_teardown(self.names)[0 if kind == "user" else 1]
                ok, _, _ = setup.run(statement)
                if ok:
                    self._created.remove(kind)
        self.report.left_for_g3 = [f"{k} {self.names.svc_user if k == 'user' else self.names.ro_role}" for k in self._created]


def new_key_pair() -> tuple[bytes, str]:
    """A fresh 2048-bit RSA key: the private key as PKCS8 DER (memory only) and the public key body for Snowflake."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, base64.b64encode(public).decode("ascii")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--state", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--account-base", required=True, help="https://<locator>.<region>.<cloud>.snowflakecomputing.com")
    parser.add_argument("--account", required=True, help="the connector account, <locator>.<region>.<cloud>")
    parser.add_argument("--expect-account", required=True)
    parser.add_argument("--url-file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--callback-timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    if not args.allow_live:
        print("refused: pass --allow-live (only after the owner approved G2)", file=sys.stderr)
        return 2
    host = args.account_base.split("//", 1)[-1].lower()
    if not args.account_base.startswith("https://") or not host.endswith(".snowflakecomputing.com") or args.expect_account.lower() not in host:
        print("refused: the account base must be https, end in .snowflakecomputing.com and contain --expect-account", file=sys.stderr)
        return 2
    with open(args.state) as handle:
        state = json.load(handle)
    if state["account_locator"].lower() != args.expect_account.lower():
        print("refused: the state file is for a different account", file=sys.stderr)
        return 2

    import snowflake.connector as sf  # type: ignore[import-not-found]  # noqa: I001 - after the guards on purpose

    def connect_oauth(token: str) -> Any:
        return sf.connect(account=args.account, authenticator="oauth", token=token, login_timeout=60, network_timeout=60)

    def connect_key(user: str, der: bytes, role: str, warehouse: str) -> Any:
        return sf.connect(account=args.account, user=user, private_key=der, role=role, warehouse=warehouse, login_timeout=60, network_timeout=60)

    def sink(url: str) -> None:
        with open(args.url_file, "w") as handle:
            handle.write(url)

    runner = G2Runner(state, account_base=args.account_base, client_id=args.client_id, connect_oauth=connect_oauth, connect_key=connect_key,
                      authorize_sink=sink, key_pair=new_key_pair, callback_timeout=args.callback_timeout)
    report = runner.run()
    report["finished_at_epoch"] = int(time.time())
    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({"all_as_expected": report["all_as_expected"], "stopped": report["stopped"], "unexpected": report["unexpected"],
                      "left_for_g3": report["left_for_g3"], "steps": len(report["steps"])}))
    return 0 if report["all_as_expected"] else 1


if __name__ == "__main__":
    sys.exit(main())
