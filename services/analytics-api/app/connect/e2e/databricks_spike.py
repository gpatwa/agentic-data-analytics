"""The Databricks Connect-mode spike (docs/design/CONNECT_DATABRICKS_SPIKE_PLAN.md): a throwaway, owner-approved driver that asks
seven workspace-only questions about option A and records only outcomes.

A dev tool, never imported by the app (a test enforces that). Nothing runs without `--allow-live`, an expected workspace host,
and the owner's approval of the gates. Every object is made under a fresh run suffix, tagged, and removed by recorded id; the
setup principal `S` and the connection principal `T` get secrets that live in this process's memory only.

Outcomes are `allowed`, `denied` or `error:<type>`. A denial is a result, not a failure. Messages from the API are read only to
tell a denial from another error and are never stored or printed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.connect.e2e.leak import LeakScanner

TAG = "compass-connect-spike"
CATALOG_PREFIX = "compass_connect_spike_"
PRINCIPAL_PREFIX = "compass-spike-"
SCHEMA = "data"
INSIDE = ("orders", "customers")
OUTSIDE = "payroll_outside"
ROWS = 30
SECRET_LIFETIME = "1800s"
QUESTIONS = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7")
_SUFFIX = re.compile(r"^[0-9a-f]{8}$")
_HOST = re.compile(r"^[a-z0-9.-]+\.(cloud\.databricks\.com|azuredatabricks\.net|gcp\.databricks\.com)$")
_DENIED = ("permission_denied", "permissiondenied", "insufficient_permissions", "insufficient privileges", "does not have", "not authorized",
           "unauthorized", "forbidden", "403", "not allowed", "access denied", "permission denied")


class SpikeStop(RuntimeError):
    """Stop and report: something unexpected, or a rule would be broken. `code` is a short fixed word."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Names:
    suffix: str
    catalog: str
    setup: str  # S, the delegated setup principal
    conn: str  # T, the connection principal S would create
    decoy: str  # the pre-existing principal with the planned name for Q7
    decoy_planned: str

    @classmethod
    def for_run(cls, suffix: str) -> Names:
        if not _SUFFIX.match(suffix or ""):
            raise ValueError("the run suffix must be 8 lower-case hex characters")
        return cls(suffix, f"{CATALOG_PREFIX}{suffix}", f"{PRINCIPAL_PREFIX}setup-{suffix}", f"{PRINCIPAL_PREFIX}conn-{suffix}",
                   f"{PRINCIPAL_PREFIX}conn2-{suffix}", f"{PRINCIPAL_PREFIX}conn2-{suffix}")

    def table(self, name: str) -> str:
        return f"`{self.catalog}`.`{SCHEMA}`.`{name}`"


# ---- the statements (one source for the run and the reviewed document) ---------------------------------------------------------


def fixture(n: Names) -> tuple[str, ...]:
    c = f"`{n.catalog}`"
    return (
        f"CREATE CATALOG {c} COMMENT '{TAG}'",
        f"CREATE SCHEMA {c}.`{SCHEMA}` COMMENT '{TAG}'",
        f"CREATE TABLE {n.table('orders')} (id BIGINT, amount DECIMAL(12, 2))",
        f"CREATE TABLE {n.table('customers')} (id BIGINT, region STRING)",
        f"CREATE TABLE {n.table(OUTSIDE)} (id BIGINT, salary BIGINT)",
        f"INSERT INTO {n.table('orders')} SELECT id, (id + 1) * 7 FROM range({ROWS})",
        f"INSERT INTO {n.table('customers')} SELECT id, IF(id % 2 = 0, 'EMEA', 'AMER') FROM range({ROWS})",
        f"INSERT INTO {n.table(OUTSIDE)} SELECT id, 1000 + id FROM range({ROWS})",
    )


def setup_grants(n: Names, app_id: str) -> tuple[str, ...]:
    """What the design's setup principal would hold: the path to the tables and `MANAGE` on the two ceiling tables."""
    who, c = f"`{app_id}`", f"`{n.catalog}`"
    return (
        f"GRANT USE CATALOG ON CATALOG {c} TO {who}",
        f"GRANT USE SCHEMA ON SCHEMA {c}.`{SCHEMA}` TO {who}",
        *(f"GRANT MANAGE ON TABLE {n.table(t)} TO {who}" for t in INSIDE),
    )


def s_grants_to_conn(n: Names, app_id: str) -> tuple[tuple[str, str], ...]:
    """What `S` tries to pass on to `T`: label, statement. The `denied` expectation is the plan's; the run records what happens."""
    who, c = f"`{app_id}`", f"`{n.catalog}`"
    return (
        ("select_orders", f"GRANT SELECT ON TABLE {n.table('orders')} TO {who}"),
        ("select_customers", f"GRANT SELECT ON TABLE {n.table('customers')} TO {who}"),
        ("select_outside", f"GRANT SELECT ON TABLE {n.table(OUTSIDE)} TO {who}"),
        ("use_catalog", f"GRANT USE CATALOG ON CATALOG {c} TO {who}"),
        ("use_schema", f"GRANT USE SCHEMA ON SCHEMA {c}.`{SCHEMA}` TO {who}"),
        ("select_schema", f"GRANT SELECT ON SCHEMA {c}.`{SCHEMA}` TO {who}"),
    )


def conn_reads(n: Names) -> tuple[tuple[str, str], ...]:
    """What `T` tries: label, statement, and below whether it should read, as the read-only design requires."""
    return (
        ("read_orders", f"SELECT count(*) FROM {n.table('orders')}"),
        ("read_customers", f"SELECT count(*) FROM {n.table('customers')}"),
        ("read_outside", f"SELECT count(*) FROM {n.table(OUTSIDE)}"),
        ("write_orders", f"INSERT INTO {n.table('orders')} VALUES (999, 1)"),
        ("create_table", f"CREATE TABLE `{n.catalog}`.`{SCHEMA}`.`should_not_exist` (id BIGINT)"),
        ("drop_orders", f"DROP TABLE {n.table('orders')}"),
    )


def revokes(n: Names, app_id: str) -> tuple[str, ...]:
    who = f"`{app_id}`"
    return tuple(f"REVOKE SELECT ON TABLE {n.table(t)} FROM {who}" for t in INSIDE)


def teardown_catalog(n: Names) -> str:
    return f"DROP CATALOG IF EXISTS `{n.catalog}` CASCADE"


def assert_ours(statement: str, n: Names) -> None:
    """Stop rule: a statement may only touch this run's catalog (or list catalogs). Nothing about users, groups, the metastore,
    an owner, settings or any other catalog can pass."""
    text = statement.strip()
    if text == "SHOW CATALOGS":
        return
    if f"`{n.catalog}`" not in text:
        raise SpikeStop("statement_outside_sandbox")
    if re.search(r"\b(METASTORE|OWNER|OWNERSHIP|ALL PRIVILEGES|EXTERNAL LOCATION|STORAGE CREDENTIAL|CONNECTION|SHARE|RECIPIENT)\b", text, re.I):
        raise SpikeStop("statement_not_allowed")
    others = re.findall(r"`(compass[a-z0-9_]*|dev|main|hive_metastore|samples|system)`\.", text)
    if any(o != n.catalog for o in others):
        raise SpikeStop("statement_outside_sandbox")


# ---- outcomes -----------------------------------------------------------------------------------------------------------------


@dataclass
class Outcome:
    question: str
    step: str
    result: str  # allowed | denied | error:<type> | note
    detail: str = ""  # a count or a short fixed word, never an API message


def classify(exc: BaseException) -> str:
    """Denied or another error. The message is read here to decide and is never kept."""
    haystack = f"{type(exc).__name__} {exc}".lower()
    return "denied" if any(word in haystack for word in _DENIED) else f"error:{type(exc).__name__[:40]}"


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)
    created: list[tuple[str, str]] = field(default_factory=list)  # (kind, id) recorded for teardown
    fallbacks: list[str] = field(default_factory=list)  # admin steps used because S was denied
    teardown: dict[str, object] = field(default_factory=dict)
    sweep: dict[str, object] = field(default_factory=dict)
    stopped: str = ""

    def add(self, question: str, step: str, result: str, detail: str = "") -> Outcome:
        outcome = Outcome(question, step, result, detail)
        self.outcomes.append(outcome)
        return outcome

    def as_dict(self, scanner: LeakScanner) -> dict[str, object]:
        return {
            "outcomes": [o.__dict__ for o in self.outcomes], "fallbacks": self.fallbacks, "teardown": self.teardown, "sweep": self.sweep,
            "stopped": self.stopped, "leaks": scanner.summary(),
        }


# ---- the driver ---------------------------------------------------------------------------------------------------------------


@dataclass
class Spike:
    """`admin` is a `WorkspaceClient`-shaped object signed in as the owner; `connect(client_id, secret)` returns one for `S` or `T`."""

    admin: Any
    connect: Callable[[str, str], Any]
    warehouse_id: str
    names: Names
    scanner: LeakScanner = field(default_factory=LeakScanner)
    report: Report = field(default_factory=Report)
    _s: Any = None
    _t: Any = None
    _s_app: str = ""
    _t_app: str = ""
    _t_id: str = ""
    _t_by_s: bool = False
    _t_secret_ok: bool = False
    _catalog_created: bool = False

    # -- plumbing

    def _sql(self, client: Any, statement: str) -> list[list[Any]]:
        assert_ours(statement, self.names)
        response = client.statement_execution.execute_statement(statement=statement, warehouse_id=self.warehouse_id, wait_timeout="50s")
        state = getattr(getattr(response.status, "state", None), "name", str(response.status.state))
        if state != "SUCCEEDED":
            error = getattr(response.status, "error", None)
            raise RuntimeError(f"{state} {getattr(error, 'error_code', '')} {getattr(error, 'message', '')}")
        return list(getattr(getattr(response, "result", None), "data_array", None) or [])

    def _try(self, question: str, step: str, call: Callable[[], Any], *, ok_detail: Callable[[Any], str] | None = None) -> bool:
        try:
            value = call()
        except SpikeStop:
            raise
        except Exception as exc:  # noqa: BLE001 - a denial is a result
            self.report.add(question, step, classify(exc))
            return False
        self.report.add(question, step, "allowed", ok_detail(value) if ok_detail else "")
        return True

    def _admin_sql(self, statement: str) -> list[list[Any]]:
        try:
            return self._sql(self.admin, statement)
        except SpikeStop:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SpikeStop(f"admin_step_failed:{type(exc).__name__}"[:60]) from None

    def _secret(self, client: Any, principal_id: str, label: str) -> tuple[str, str]:
        created = client.service_principal_secrets_proxy.create(service_principal_id=principal_id, lifetime=SECRET_LIFETIME)
        self.scanner.watch(label, str(created.secret))
        self.report.created.append(("secret", f"{principal_id}:{created.id}"))
        return str(created.id), str(created.secret)

    def _acl(self, client: Any, app_id: str) -> None:
        client.warehouses.update_permissions(self.warehouse_id, access_control_list=[_sdk_acl(app_id)])

    # -- the run

    def preflight(self) -> None:
        names = {str(row[0]) for row in self._admin_sql("SHOW CATALOGS")}
        if any(name.startswith(CATALOG_PREFIX) for name in names):
            raise SpikeStop("leftovers_found")
        leftovers = [p for p in self.admin.service_principals.list() if str(getattr(p, "display_name", "")).startswith(PRINCIPAL_PREFIX)]
        if leftovers:
            raise SpikeStop("leftovers_found")

    def setup(self) -> None:
        n = self.names
        for statement in fixture(n):
            self._admin_sql(statement)
            if statement.startswith("CREATE CATALOG"):
                self._catalog_created = True
        s = self.admin.service_principals.create(display_name=n.setup)
        self.report.created.append(("principal", str(s.id)))
        self._s_app = str(s.application_id)
        _, secret = self._secret(self.admin, str(s.id), "setup_secret")
        self._acl(self.admin, self._s_app)
        self.report.created.append(("acl", self._s_app))
        for statement in setup_grants(n, self._s_app):
            self._admin_sql(statement)
        self._s = self.connect(self._s_app, secret)

    def q7_decoy(self) -> None:
        """A principal with the planned name already exists, made by the owner. Does S see it, and does S leave it alone?"""
        n = self.names
        decoy = self.admin.service_principals.create(display_name=n.decoy)
        self.report.created.append(("principal", str(decoy.id)))
        decoy_id = str(decoy.id)
        seen: list[Any] = []

        def look() -> int:
            found = [p for p in self._s.service_principals.list() if str(getattr(p, "display_name", "")) == n.decoy_planned]
            seen.extend(found)
            return len(found)

        self._try("Q7", "s_lists_principals_by_name", look, ok_detail=lambda c: f"found={c}")
        before = {str(getattr(p, "id", "")) for p in seen}
        made: list[str] = []

        def create_same() -> str:
            created = self._s.service_principals.create(display_name=n.decoy_planned)
            made.append(str(created.id))
            self.report.created.append(("principal", str(created.id)))
            return str(created.id)

        self._try("Q7", "s_creates_same_name", create_same, ok_detail=lambda _v: "duplicate_name_accepted")
        self.report.add("Q7", "decoy_still_present", "allowed" if decoy_id in {str(getattr(p, "id", "")) for p in self.admin.service_principals.list()} else "error:missing",
                        f"decoy_seen_by_S={'yes' if decoy_id in before else 'no'}")

    def q1_create_conn(self) -> None:
        n = self.names
        holder: dict[str, str] = {}

        def create() -> str:
            created = self._s.service_principals.create(display_name=n.conn)
            holder["id"], holder["app"] = str(created.id), str(created.application_id)
            self.report.created.append(("principal", holder["id"]))
            return holder["id"]

        if self._try("Q1", "s_creates_connection_principal", create):
            self._t_id, self._t_app, self._t_by_s = holder["id"], holder["app"], True
            return
        created = self.admin.service_principals.create(display_name=n.conn)  # fallback so Q2 to Q6 can still be asked
        self._t_id, self._t_app = str(created.id), str(created.application_id)
        self.report.created.append(("principal", self._t_id))
        self.report.fallbacks.append("admin_created_connection_principal")

    def q2_secret(self) -> None:
        self._t_secret_ok = False
        holder: dict[str, str] = {}

        def make() -> str:
            _id, secret = self._secret(self._s, self._t_id, "conn_secret")
            holder["secret"] = secret
            return _id

        label = "s_creates_secret_for_own_principal" if self._t_by_s else "s_creates_secret_for_admin_made_principal"
        if self._try("Q2", label, make):
            self._t_secret_ok = True
            self._t = self.connect(self._t_app, holder["secret"])
            return
        _id, secret = self._secret(self.admin, self._t_id, "conn_secret")
        self.report.fallbacks.append("admin_created_connection_secret")
        self._t = self.connect(self._t_app, secret)

    def q3_grants(self) -> None:
        for label, statement in s_grants_to_conn(self.names, self._t_app):
            ok = self._try("Q3", f"s_grants_{label}", lambda statement=statement: self._sql(self._s, statement))
            if not ok and label in ("use_catalog", "use_schema", "select_orders", "select_customers"):
                self._admin_sql(statement)  # the path and the two ceiling tables are needed for Q6
                self.report.fallbacks.append(f"admin_granted_{label}")
        if any(o.result == "allowed" and o.step in ("s_grants_select_outside", "s_grants_select_schema") for o in self.report.outcomes):
            raise SpikeStop("setup_principal_exceeds_ceiling")  # a surprise: stop, record, tear down

    def q4_warehouse(self) -> None:
        ok = self._try("Q4", "s_gives_connection_principal_can_use", lambda: self._acl(self._s, self._t_app))
        if not ok:
            self._acl(self.admin, self._t_app)
            self.report.fallbacks.append("admin_gave_connection_principal_can_use")
        self.report.created.append(("acl", self._t_app))

    def q6_reads(self) -> None:
        for label, statement in conn_reads(self.names):
            self._try("Q6", f"t_{label}", lambda statement=statement: self._sql(self._t, statement),
                      ok_detail=lambda rows: f"rows={rows[0][0]}" if rows and rows[0] and str(rows[0][0]).isdigit() else "")
        forbidden = ("t_read_outside", "t_write_orders", "t_create_table", "t_drop_orders")
        if any(o.result == "allowed" and o.step in forbidden for o in self.report.outcomes):
            raise SpikeStop("connection_principal_not_read_only")

    def q5_rollback(self) -> None:
        for statement in revokes(self.names, self._t_app):
            self._try("Q5", "s_revokes_select", lambda statement=statement: self._sql(self._s, statement))
        self._try("Q5", "s_deletes_connection_principal", lambda: self._s.service_principals.delete(self._t_id))

    def questions(self) -> None:
        self.q7_decoy()
        self.q1_create_conn()
        self.q2_secret()
        self.q3_grants()
        self.q4_warehouse()
        self.q6_reads()
        self.q5_rollback()

    # -- teardown and sweep: created-only, by recorded id, always

    def teardown_all(self) -> None:
        left: list[str] = []
        done = 0
        for kind, ident in reversed(self.report.created):
            try:
                if kind == "secret":
                    principal_id, secret_id = ident.split(":", 1)
                    self.admin.service_principal_secrets_proxy.delete(principal_id, secret_id)
                elif kind == "principal":
                    self.admin.service_principals.delete(ident)
                else:
                    continue  # a warehouse entry goes with the principal; it is checked in the sweep
                done += 1
            except Exception as exc:  # noqa: BLE001 - an already-deleted object (S deleted T in Q5) is fine; report the rest
                if "notfound" in type(exc).__name__.lower() or "does not exist" in str(exc).lower():
                    done += 1
                else:
                    left.append(f"{kind}:{ident}")
        if self._catalog_created:
            try:
                self._sql(self.admin, teardown_catalog(self.names))
                done += 1
            except Exception:  # noqa: BLE001
                left.append(f"catalog:{self.names.catalog}")
        self.report.teardown = {"removed": done, "left": left}

    def sweep(self) -> None:
        catalogs = [str(r[0]) for r in self._sql(self.admin, "SHOW CATALOGS") if str(r[0]).startswith(CATALOG_PREFIX)]
        principals = [str(getattr(p, "display_name", "")) for p in self.admin.service_principals.list() if str(getattr(p, "display_name", "")).startswith(PRINCIPAL_PREFIX)]
        entries = []
        try:
            acl = self.admin.warehouses.get_permissions(self.warehouse_id).access_control_list or []
            known = {a for kind, a in self.report.created if kind == "acl"}
            entries = [a for a in acl if getattr(a, "service_principal_name", None) in known]
        except Exception:  # noqa: BLE001 - unreadable counts as a note, not a clean bill
            self.report.sweep = {"catalogs": len(catalogs), "principals": len(principals), "warehouse_entries": "unreadable"}
            return
        self.report.sweep = {"catalogs": len(catalogs), "principals": len(principals), "warehouse_entries": len(entries)}

    def run(self) -> dict[str, object]:
        try:
            self.preflight()
            self.setup()
            self.questions()
        except SpikeStop as stop:
            self.report.stopped = stop.code
        except Exception as exc:  # noqa: BLE001
            self.report.stopped = f"unexpected:{type(exc).__name__}"[:60]
        finally:
            self.teardown_all()
            self.sweep()
        result = self.report.as_dict(self.scanner)
        self.scanner.scan("report", json.dumps(result))
        result["leaks"] = self.scanner.summary()
        return result


def _sdk_acl(app_id: str) -> Any:
    """`CAN_USE` on the warehouse for one principal: the SDK's request type live, a plain record in the tests."""
    try:
        from databricks.sdk.service import sql

        return sql.WarehouseAccessControlRequest(service_principal_name=app_id, permission_level=sql.WarehousePermissionLevel.CAN_USE)
    except ImportError:
        return SimpleNamespace(service_principal_name=app_id, permission_level="CAN_USE")


# ---- the reviewed document ----------------------------------------------------------------------------------------------------


def _block(title: str, lines: Any, lang: str = "sql") -> str:
    body = "\n".join(f"{line};" if lang == "sql" else str(line) for line in lines)
    return f"### {title}\n\n```{lang}\n{body}\n```\n"


def render_markdown(suffix: str = "a1b2c3d4", warehouse: str = "<SQL warehouse name>") -> str:
    """What the reviewer reads. A test keeps `docs/design/CONNECT_DATABRICKS_SPIKE_STATEMENTS.md` equal to this."""
    n = Names.for_run(suffix)
    s_app, t_app = "<S_APP_ID>", "<T_APP_ID>"
    intro = (
        "# Databricks spike: the exact statements and calls (generated)\n\n"
        "Generated by `services/analytics-api/app/connect/e2e/databricks_spike.py`; a test keeps this file equal to the generator. "
        f"`{suffix}` stands for the fresh 8-hex run suffix chosen at run time; `{s_app}` and `{t_app}` for the application ids of the setup "
        "principal `S` and the connection principal `T`, which exist only after they are created; the warehouse is chosen by name "
        f"(`{warehouse}`) and never edited. **Nothing here has been run.** Plan: `docs/design/CONNECT_DATABRICKS_SPIKE_PLAN.md`.\n\n"
        "Every statement below must mention this run's catalog; the driver refuses anything else (no users, groups, metastore, owner "
        "or other catalog). Plain `CREATE` is used throughout, never `IF NOT EXISTS` or `OR REPLACE`. Order of the questions at run "
        "time: Q7 (decoy), Q1, Q2, Q3, Q4, Q6, then Q5 (rollback), so the principal exists while the read checks run.\n\n"
        "**Not built:** the fallback of using a schema inside an existing catalog. If the workspace refuses `CREATE CATALOG`, the run stops "
        "at the first fixture statement with nothing created (teardown has nothing to remove) and the fallback needs a small change and "
        "your approval before another run. Granting `USE CATALOG` on a catalog you own would also need explicit revokes at teardown.\n"
    )
    admin_calls = (
        f"admin: service_principals.create(display_name='{n.setup}')            # S",
        "admin: service_principal_secrets_proxy.create(S, lifetime='1800s')      # S's secret: memory only",
        f"admin: warehouses.update_permissions({warehouse!r}, [S -> CAN_USE])",
        f"admin: service_principals.create(display_name='{n.decoy}')            # Q7: the pre-existing principal with a planned name",
    )
    s_calls = (
        f"S: service_principals.list()   # Q7: is the decoy ('{n.decoy_planned}') visible?",
        f"S: service_principals.create(display_name='{n.decoy_planned}')   # Q7: is a duplicate name accepted? (the decoy is never touched)",
        f"S: service_principals.create(display_name='{n.conn}')            # Q1: T",
        "S: service_principal_secrets_proxy.create(T, lifetime='1800s')     # Q2: T's secret: memory only",
        f"S: warehouses.update_permissions({warehouse!r}, [T -> CAN_USE])   # Q4",
        "S: service_principals.delete(T)                                    # Q5: rollback",
    )
    fallbacks = (
        "admin (only if S was denied): service_principals.create for T; secret for T; the same GRANT USE CATALOG, USE SCHEMA and "
        "SELECT statements S was denied; warehouses.update_permissions for T",
    )
    teardown = (
        "admin: service_principal_secrets_proxy.delete(...)   # every recorded secret, newest first",
        "admin: service_principals.delete(...)                 # every recorded principal, including the decoy, by id",
        "admin: warehouses.get_permissions(...)                # read: no entry for our principals remains",
    )
    sweep = ("SHOW CATALOGS", "service_principals.list()  # none named compass-spike-*", "warehouses.get_permissions()  # none of ours")
    return "\n".join([
        intro,
        _block("Preflight (read only): stops on anything under our prefixes", ("SHOW CATALOGS", "service_principals.list()  # any compass-spike-* stops the run"), "text"),
        _block("Fixture (as you, the signed-in admin)", fixture(n)),
        _block("Setup principal and its access (as you)", admin_calls, "text"),
        _block("What S holds (as you)", setup_grants(n, s_app)),
        _block("Q3: what S tries to pass on to T (as S)", (stmt for _l, stmt in s_grants_to_conn(n, t_app))),
        _block("Q1, Q2, Q4, Q5, Q7: what S tries (as S)", s_calls, "text"),
        _block("Q5: what S tries to revoke (as S)", revokes(n, t_app)),
        _block("Q6: what T tries (as T, OAuth M2M)", (stmt for _l, stmt in conn_reads(n))),
        _block("Fallbacks, only where S was denied", fallbacks, "text"),
        _block("Teardown (created-only, by recorded id)", [*teardown, teardown_catalog(n)], "text"),
        _block("Sweep (read only, zero expected)", sweep, "text"),
    ])


# ---- command line -------------------------------------------------------------------------------------------------------------


def connect_admin(host: str, *, sdk: Any | None = None) -> Any:
    """The owner's own browser sign-in (OAuth), as for the validation checks; the SDK keeps the token in memory. Kept local so the
    sandbox admin tool stays imported by nothing but its own command line (guarded by tests)."""
    if sdk is None:
        import databricks.sdk as sdk
    return sdk.WorkspaceClient(host=f"https://{host}", auth_type="external-browser")


def pick_warehouse(client: Any, name: str) -> str:
    """The SQL warehouse, chosen BY NAME. No default: the first one listed could be a production warehouse."""
    chosen = [w for w in client.warehouses.list() if w.name == name]
    if len(chosen) != 1:
        raise SpikeStop("warehouse_not_found" if not chosen else "warehouse_name_ambiguous")
    return str(chosen[0].id)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Databricks Connect-mode spike (owner-approved, throwaway).")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--expect-host", required=True, help="the workspace host this run is approved for")
    parser.add_argument("--suffix", required=True, help="the run suffix the reviewed statements were generated with")
    parser.add_argument("--warehouse", required=True, help="the SQL warehouse, chosen by name; never edited")
    parser.add_argument("--report", default=None, help="write the report here (outcomes only)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, sdk: Any | None = None) -> int:
    args = parse_args(argv)
    if not args.allow_live:
        print("refused: pass --allow-live (only after the owner approved the gates)", file=sys.stderr)
        return 2
    if not _HOST.match(args.expect_host) or not _SUFFIX.match(args.suffix) or not args.warehouse.strip():
        print("refused: check --expect-host, --suffix and --warehouse", file=sys.stderr)
        return 2
    admin = connect_admin(args.expect_host, sdk=sdk)
    warehouse_id = pick_warehouse(admin, args.warehouse)

    def connect(client_id: str, secret: str) -> Any:
        import databricks.sdk as dbx

        return dbx.WorkspaceClient(host=f"https://{args.expect_host}", client_id=client_id, client_secret=secret, auth_type="oauth-m2m")

    spike = Spike(admin, connect, warehouse_id, Names.for_run(args.suffix))
    result = spike.run()
    if args.report:
        Path(args.report).write_text(json.dumps(result, indent=2))
    print(json.dumps({"stopped": result["stopped"], "teardown": result["teardown"], "sweep": result["sweep"],
                      "leaks_clean": result["leaks"]["clean"]}))  # type: ignore[index]
    clean = (not result["stopped"]) and not result["teardown"]["left"] and result["leaks"]["clean"]  # type: ignore[index]
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
