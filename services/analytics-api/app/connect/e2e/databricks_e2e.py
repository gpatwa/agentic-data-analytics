"""The Databricks pre-created-principal end-to-end check (docs/design/CONNECT_DATABRICKS_DESIGN.md, gate D3).

A dev tool, never imported by the app. As the signed-in owner it runs the product's generated registration script against a
sandbox catalog, verifies the real read-backs with the product's verifier, runs a connection test through the product's Databricks
session as the new principal, then removes everything it created (by recorded id) and sweeps. Secrets stay in memory.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.connect import databricks_registration as reg
from app.connect.e2e import databricks_spike as sp
from app.connect.e2e.leak import LeakScanner
from app.connect.grants import ConnectScope

ConnectionTest = Callable[[str, str], list[tuple[str, str, str]]]  # (app_id, secret) -> [(label, result, detail)]


@dataclass
class E2E:
    admin: Any
    connection_test: ConnectionTest
    warehouse_id: str
    warehouse_name: str
    names: sp.Names
    scanner: LeakScanner = field(default_factory=LeakScanner)
    outcomes: list[dict[str, str]] = field(default_factory=list)
    created: list[tuple[str, str]] = field(default_factory=list)
    stopped: str = ""
    teardown: dict[str, object] = field(default_factory=dict)
    sweep: dict[str, object] = field(default_factory=dict)
    _app: str = ""
    _pid: str = ""
    _catalog_created: bool = False

    def _note(self, step: str, result: str, detail: str = "") -> None:
        self.outcomes.append({"step": step, "result": result, "detail": detail})

    def _sql(self, statement: str) -> list[list[Any]]:
        sp.assert_ours(statement, self.names)
        r = self.admin.statement_execution.execute_statement(statement=statement, warehouse_id=self.warehouse_id, wait_timeout="50s")
        state = getattr(getattr(r.status, "state", None), "name", str(r.status.state))
        if state != "SUCCEEDED":
            raise RuntimeError(state)
        return list(getattr(getattr(r, "result", None), "data_array", None) or [])

    @property
    def ceiling(self) -> ConnectScope:
        return ConnectScope(self.names.catalog, tuple((sp.SCHEMA, t) for t in sp.INSIDE))

    def preflight(self) -> None:
        if any(str(r[0]).startswith(sp.CATALOG_PREFIX) for r in self._sql("SHOW CATALOGS")):
            raise sp.SpikeStop("leftovers_found")
        if any(str(getattr(p, "display_name", "")).startswith(reg.PRINCIPAL_PREFIX) for p in self.admin.service_principals.list()):
            raise sp.SpikeStop("leftovers_found")

    def fixture(self) -> None:
        for statement in sp.fixture(self.names):
            self._sql(statement)
            if statement.startswith("CREATE CATALOG"):
                self._catalog_created = True

    def run_script(self) -> None:
        suffix = self.names.suffix
        first = reg.generate(self.ceiling, suffix=suffix, warehouse_name=self.warehouse_name)
        secret = ""
        for step in first.steps:
            if step.op == "create_principal":
                p = self.admin.service_principals.create(display_name=step.arg)
                self._pid, self._app = str(p.id), str(p.application_id)
                self.created.append(("principal", self._pid))
            elif step.op == "create_secret":
                s = self.admin.service_principal_secrets_proxy.create(service_principal_id=self._pid, lifetime=step.arg)
                secret = str(s.secret)
                self.scanner.watch("principal_secret", secret)
                self.created.append(("secret", f"{self._pid}:{s.id}"))
            elif step.op == "warehouse_can_use":
                self.admin.warehouses.update_permissions(self.warehouse_id, access_control_list=[sp._sdk_acl(self._app)])
        script = reg.generate(self.ceiling, suffix=suffix, warehouse_name=self.warehouse_name, application_id=self._app)
        for step in script.steps:
            if step.op == "sql":
                self._sql(step.arg)
        self._note("script_ran", "allowed", f"digest={script.digest}")
        self._secret = secret

    def verify(self) -> None:
        c, a = f"`{self.names.catalog}`", f"`{self._app}`"
        securables = [f"CATALOG {c}", f"SCHEMA {c}.`{sp.SCHEMA}`", *(f"TABLE {self.names.table(t)}" for t in (*sp.INSIDE, sp.OUTSIDE))]
        rows: list[dict[str, str]] = []
        for securable in securables:
            for row in self._sql(f"SHOW GRANTS {a} ON {securable}"):
                rows.append({"actionType": str(row[1]), "objectType": str(row[2]), "objectKey": str(row[3])})
        acl = self.admin.warehouses.get_permissions(self.warehouse_id).access_control_list or []
        pairs = [(str(getattr(e, "service_principal_name", "")), str(getattr(getattr(p, "permission_level", ""), "value", getattr(p, "permission_level", ""))))
                 for e in acl for p in (getattr(e, "all_permissions", None) or [])]
        report = reg.verify_registration(rows, pairs, self.ceiling, self._app)
        self._note("verify_registration", "allowed" if report.ok else f"problem:{report.code}",
                   f"missing={len(report.missing)} unexpected={len(report.unexpected)} dangerous={len(report.dangerous)} warehouse={len(report.warehouse_problems)}")

    def test_connection(self) -> None:
        for label, result, detail in self.connection_test(self._app, self._secret):
            self._note(f"connection_{label}", result, detail)

    def remove(self) -> None:
        left, done = [], 0
        if self._app:
            for step in reg.generate(self.ceiling, suffix=self.names.suffix, warehouse_name=self.warehouse_name, application_id=self._app).teardown:
                if step.op == "sql":
                    try:
                        self._sql(step.arg)
                        done += 1
                    except Exception:  # noqa: BLE001
                        left.append("revoke")
        for kind, ident in reversed(self.created):
            try:
                if kind == "secret":
                    pid, sid = ident.split(":", 1)
                    self.admin.service_principal_secrets_proxy.delete(pid, sid)
                else:
                    self.admin.service_principals.delete(ident)
                done += 1
            except Exception:  # noqa: BLE001
                left.append(f"{kind}")
        if self._catalog_created:
            try:
                self._sql(sp.teardown_catalog(self.names))
                done += 1
            except Exception:  # noqa: BLE001
                left.append("catalog")
        self.teardown = {"removed": done, "left": left}

    def do_sweep(self) -> None:
        catalogs = [r for r in self._sql("SHOW CATALOGS") if str(r[0]).startswith(sp.CATALOG_PREFIX)]
        principals = [p for p in self.admin.service_principals.list() if str(getattr(p, "display_name", "")).startswith(reg.PRINCIPAL_PREFIX)]
        acl = self.admin.warehouses.get_permissions(self.warehouse_id).access_control_list or []
        entries = [e for e in acl if getattr(e, "service_principal_name", None) == self._app and self._app]
        self.sweep = {"catalogs": len(catalogs), "principals": len(principals), "warehouse_entries": len(entries)}

    def run(self) -> dict[str, object]:
        try:
            self.preflight()
            self.fixture()
            self.run_script()
            self.verify()
            self.test_connection()
        except sp.SpikeStop as stop:
            self.stopped = stop.code
        except Exception as exc:  # noqa: BLE001 - type only
            self.stopped = f"unexpected:{type(exc).__name__}"[:60]
        finally:
            self.remove()
            self.do_sweep()
        result: dict[str, object] = {"outcomes": self.outcomes, "stopped": self.stopped, "teardown": self.teardown, "sweep": self.sweep}
        self.scanner.scan("report", json.dumps(result))
        result["leaks"] = self.scanner.summary()
        return result


def live_connection_test(host: str, warehouse_id: str, catalog: str) -> ConnectionTest:
    """Through the product's Databricks session (OAuth M2M): read a ceiling table, then try the table outside the ceiling."""
    from app.execution.warehouse_sessions import DatabricksConfig, DatabricksSession

    def run(app_id: str, secret: str) -> list[tuple[str, str, str]]:
        config = DatabricksConfig(server_hostname=host, http_path=f"/sql/1.0/warehouses/{warehouse_id}", catalog=catalog, schema=sp.SCHEMA,
                                  client_id=app_id, client_secret=secret)
        names = sp.Names.for_run(catalog[len(sp.CATALOG_PREFIX):])
        out: list[tuple[str, str, str]] = []
        session = DatabricksSession(config)
        try:
            for label, table in (("read_orders", "orders"), ("read_customers", "customers"), ("read_outside", sp.OUTSIDE)):
                try:
                    stream = session.execute(f"SELECT count(*) FROM {names.table(table)}", {})
                    rows = stream.fetchmany(1)
                    stream.close()
                    out.append((label, "allowed", f"rows={rows[0][0]}"))
                except Exception as exc:  # noqa: BLE001
                    out.append((label, sp.classify(exc), ""))
        finally:
            session.close()
        return out

    return run


def main(argv: list[str] | None = None, *, sdk: Any | None = None) -> int:
    p = argparse.ArgumentParser(description="Databricks pre-created-principal end-to-end check (owner-approved, throwaway).")
    p.add_argument("--allow-live", action="store_true")
    p.add_argument("--expect-host", required=True)
    p.add_argument("--suffix", required=True)
    p.add_argument("--warehouse", required=True)
    p.add_argument("--report", default=None)
    args = p.parse_args(argv)
    if not args.allow_live:
        print("refused: pass --allow-live (only after the owner approved the gate)", file=sys.stderr)
        return 2
    if not sp._HOST.match(args.expect_host) or not sp._SUFFIX.match(args.suffix) or not args.warehouse.strip():
        print("refused: check --expect-host, --suffix and --warehouse", file=sys.stderr)
        return 2
    admin = sp.connect_admin(args.expect_host, sdk=sdk)
    wid = sp.pick_warehouse(admin, args.warehouse)
    names = sp.Names.for_run(args.suffix)
    result = E2E(admin, live_connection_test(args.expect_host, wid, names.catalog), wid, args.warehouse, names).run()
    if args.report:
        Path(args.report).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ("stopped", "teardown", "sweep")} | {"leaks_clean": result["leaks"]["clean"]}))  # type: ignore[index]
    ok = not result["stopped"] and not result["teardown"]["left"] and result["leaks"]["clean"]  # type: ignore[index]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
