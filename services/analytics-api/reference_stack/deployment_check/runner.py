"""The registration runner: does by code what the owner's hand did (docs/design/SELF_SERVE_CONNECT_DESIGN.md, layer 1).

It takes a ceiling (a catalog and exact tables) and an admin client and: preflights (rights and names), runs the product's OWN generated registration script (the same
`databricks_registration.generate` a customer's admin runs), reads the grants back and verifies them with the product's verifier, hands out the new principal's credential IN MEMORY
ONLY, and removes everything it created. A crash leaves tagged objects but no usable secret.

Safety rules (each one from an earlier live run):
- It removes only what THIS tool recorded in its ledger, by id. It never matches by name prefix, so a customer's real `compass-conn-*` principal in the same workspace is never touched.
- The ledger holds ids, names and table names, never a secret. It is written after each creation, so a crash between steps is still recoverable with `sweep`.
- Every SQL statement must be a GRANT or REVOKE of USE CATALOG, USE SCHEMA or SELECT, or a SHOW GRANTS, and must name the ceiling's catalog. Nothing else can pass.
- It never changes a warehouse except adding `CAN_USE` for its own principal, and checks that entry is gone afterwards.
- Failure codes name the step only, never an API message.

Used by `reference_stack.deployment_check --provision` and `--sweep` only; nothing under `app/` imports it. Not run against a workspace: tested with fakes."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.connect import databricks_registration as reg
from app.connect.e2e.leak import LeakScanner
from app.connect.grants import ConnectScope

DEFAULT_LEDGER = Path.home() / ".compass" / "provision-ledger.json"
ALLOWED_SQL = re.compile(r"^(GRANT|REVOKE) (USE CATALOG|USE SCHEMA|SELECT) |^SHOW GRANTS ", re.IGNORECASE)
FORBIDDEN_SQL = re.compile(r"\b(WITH GRANT OPTION|ALL PRIVILEGES|MANAGE|OWNERSHIP|MODIFY|METASTORE|EXTERNAL LOCATION|STORAGE CREDENTIAL)\b", re.IGNORECASE)


class RunnerStop(Exception):
    """A step stopped the run. `code` names the step only."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Provisioned:
    """The new principal. The secret is in memory only and is left out of repr, so a stray print cannot show it."""

    application_id: str
    principal: str
    secret: str = field(repr=False)


class Ledger:
    """Ids and names of what a run created (never a secret), kept on disk so `sweep` can finish a crashed run."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".ledger-")
        with os.fdopen(fd, "w") as handle:
            json.dump(entries, handle, indent=1)
        os.replace(tmp, self.path)

    def put(self, entry: dict[str, Any]) -> None:
        others = [e for e in self.entries() if e.get("run_id") != entry["run_id"]]
        self._write([*others, entry])

    def drop(self, run_id: str) -> None:
        self._write([e for e in self.entries() if e.get("run_id") != run_id])


def _acl(app_id: str) -> Any:
    try:
        from databricks.sdk.service import sql

        return sql.WarehouseAccessControlRequest(service_principal_name=app_id, permission_level=sql.WarehousePermissionLevel.CAN_USE)
    except ImportError:
        return SimpleNamespace(service_principal_name=app_id, permission_level="CAN_USE")


def pick_warehouse(admin: Any, name: str) -> str:
    """The SQL warehouse, chosen BY NAME (never a default: the first listed could be a production warehouse)."""
    chosen = [w for w in admin.warehouses.list() if getattr(w, "name", None) == name]
    if len(chosen) != 1:
        raise RunnerStop("warehouse_not_found" if not chosen else "warehouse_name_ambiguous")
    return str(chosen[0].id)


def run_sql(admin: Any, warehouse_id: str, catalog: str, statement: str, step: str) -> list[list[Any]]:
    """One guarded statement: only GRANT/REVOKE of the three read privileges or SHOW GRANTS, and only naming the ceiling's catalog."""
    text = statement.strip()
    if not ALLOWED_SQL.match(text) or FORBIDDEN_SQL.search(text) or f"`{catalog}`" not in text:
        raise RunnerStop("statement_not_allowed")
    try:
        result = admin.statement_execution.execute_statement(statement=text, warehouse_id=warehouse_id, wait_timeout="50s")
        state = getattr(getattr(result.status, "state", None), "name", str(result.status.state))
    except Exception as exc:  # noqa: BLE001 - the step and the error type only
        raise RunnerStop(f"{step}:{type(exc).__name__[:40]}") from exc
    if state != "SUCCEEDED":
        raise RunnerStop(f"{step}:{state[:20]}")
    return [list(row) for row in (getattr(getattr(result, "result", None), "data_array", None) or [])]


class RegistrationRunner:
    def __init__(self, admin: Any, *, ceiling: ConnectScope, warehouse_id: str, warehouse_name: str, ledger: Ledger, suffix: str | None = None,
                 scanner: LeakScanner | None = None) -> None:
        self.admin, self.ceiling, self.warehouse_id, self.warehouse_name, self.ledger = admin, ceiling, warehouse_id, warehouse_name, ledger
        self.suffix = suffix or uuid.uuid4().hex[:8]
        self.run_id = f"run-{self.suffix}"
        self.scanner = scanner or LeakScanner()
        self._app = self._pid = self._sid = ""
        self._warehouse_entry = False
        self.teardown_result: dict[str, Any] = {}

    # ---- guarded calls ---------------------------------------------------------------------------------------------------

    def _sql(self, statement: str, step: str) -> list[list[Any]]:
        return run_sql(self.admin, self.warehouse_id, self.ceiling.database, statement, step)

    def _entry(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "suffix": self.suffix, "principal_id": self._pid, "application_id": self._app, "secret_id": self._sid,
                "catalog": self.ceiling.database, "tables": [list(t) for t in self.ceiling.tables], "warehouse_id": self.warehouse_id,
                "warehouse": self.warehouse_name, "created_at": int(time.time())}

    # ---- the run ---------------------------------------------------------------------------------------------------------

    def preflight(self) -> None:
        """Read-only. Stops before anything is created if a leftover exists, the name is taken, or the admin cannot read what verification needs."""
        if self.ledger.entries():
            raise RunnerStop("leftovers_found")
        name = reg.principal_name(self.suffix)
        try:
            principals = list(self.admin.service_principals.list())
        except Exception as exc:  # noqa: BLE001
            raise RunnerStop("cannot_list_principals") from exc
        if any(getattr(p, "display_name", None) == name for p in principals):
            raise RunnerStop("name_taken")
        try:
            self.admin.warehouses.get_permissions(self.warehouse_id)
        except Exception as exc:  # noqa: BLE001
            raise RunnerStop("cannot_read_warehouse_permissions") from exc
        self._sql(f"SHOW GRANTS ON CATALOG `{self.ceiling.database}`", "cannot_read_grants")

    def setup(self) -> Provisioned:
        """The product's generated script, step by step. The ledger is updated after each creation."""
        first = reg.generate(self.ceiling, suffix=self.suffix, warehouse_name=self.warehouse_name)
        secret = ""
        try:
            for step in first.steps:
                if step.op == "create_principal":
                    principal = self.admin.service_principals.create(display_name=step.arg)
                    self._pid, self._app = str(principal.id), str(principal.application_id)
                    self.ledger.put(self._entry())
                elif step.op == "create_secret":
                    created = self.admin.service_principal_secrets_proxy.create(service_principal_id=self._pid, lifetime=step.arg)
                    secret, self._sid = str(created.secret), str(created.id)
                    self.scanner.watch("principal_secret", secret)
                    self.ledger.put(self._entry())
                elif step.op == "warehouse_can_use":
                    self.admin.warehouses.update_permissions(self.warehouse_id, access_control_list=[_acl(self._app)])
                    self._warehouse_entry = True
            for step in reg.generate(self.ceiling, suffix=self.suffix, warehouse_name=self.warehouse_name, application_id=self._app).steps:
                if step.op == "sql":
                    self._sql(step.arg, "grant")
        except RunnerStop:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RunnerStop(f"setup:{type(exc).__name__[:40]}") from exc
        return Provisioned(self._app, reg.principal_name(self.suffix), secret)

    def verify(self) -> reg.Report:
        """The product's verifier on the real read-backs (the same ones the 2026-11-18 live check used)."""
        c, who = f"`{self.ceiling.database}`", f"`{self._app}`"
        securables = [f"CATALOG {c}", *(f"SCHEMA {c}.`{s}`" for s in self.ceiling.schemas), *(f"TABLE {c}.`{s}`.`{t}`" for s, t in self.ceiling.tables)]
        rows: list[dict[str, str]] = []
        for securable in securables:
            for row in self._sql(f"SHOW GRANTS {who} ON {securable}", "read_back"):
                rows.append({"actionType": str(row[1]), "objectType": str(row[2]), "objectKey": str(row[3])})
        acl = self.admin.warehouses.get_permissions(self.warehouse_id).access_control_list or []
        pairs = [(str(getattr(e, "service_principal_name", "")), str(getattr(getattr(p, "permission_level", ""), "value", getattr(p, "permission_level", ""))))
                 for e in acl for p in (getattr(e, "all_permissions", None) or [])]
        return reg.verify_registration(rows, pairs, self.ceiling, self._app)

    def teardown(self) -> dict[str, Any]:
        """Remove exactly what this run created, by id; report what is left (names of kinds only)."""
        return self._remove(self._entry() | {"warehouse_entry": self._warehouse_entry})

    def _remove(self, entry: dict[str, Any]) -> dict[str, Any]:
        app, pid, sid = entry.get("application_id") or "", entry.get("principal_id") or "", entry.get("secret_id") or ""
        ceiling = ConnectScope(entry["catalog"], tuple((s, t) for s, t in entry["tables"]))
        left: list[str] = []
        done = 0
        if app:
            for step in reg.generate(ceiling, suffix=entry["suffix"], warehouse_name=entry.get("warehouse", ""), application_id=app).teardown:
                if step.op == "sql":
                    try:
                        run_sql(self.admin, entry["warehouse_id"], entry["catalog"], step.arg, "revoke")
                        done += 1
                    except Exception:  # noqa: BLE001
                        left.append("grants")
        if sid and pid:
            try:
                self.admin.service_principal_secrets_proxy.delete(pid, sid)
                done += 1
            except Exception:  # noqa: BLE001
                left.append("secret")
        if pid:
            try:
                self.admin.service_principals.delete(pid)
                done += 1
            except Exception:  # noqa: BLE001
                left.append("principal")
        if app and entry.get("warehouse_id"):
            try:
                acl = self.admin.warehouses.get_permissions(entry["warehouse_id"]).access_control_list or []
                if any(getattr(e, "service_principal_name", None) == app for e in acl):
                    left.append("warehouse_entry")
            except Exception:  # noqa: BLE001
                left.append("warehouse_entry_unchecked")
        if not left and entry.get("run_id"):
            self.ledger.drop(entry["run_id"])
        return {"removed": done, "left": left}

    def sweep(self) -> dict[str, Any]:
        """Finish a crashed run: remove what the ledger records, by id. Never matches by name."""
        results = [self._remove(e) | {"run_id": e.get("run_id", "")} for e in self.ledger.entries()]
        return {"runs": len(results), "removed": sum(r["removed"] for r in results), "left": [x for r in results for x in r["left"]]}

    @contextmanager
    def provision(self) -> Iterator[Provisioned]:
        """Preflight, set up, and always tear down. A setup failure still removes whatever was created."""
        self.preflight()
        try:
            yield self.setup()
        finally:
            self.teardown_result = self.teardown()
