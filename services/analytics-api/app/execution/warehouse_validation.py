"""Live validation of the Snowflake and Databricks sessions against a SANDBOX warehouse (stage S6, docs/WAREHOUSE_S6_LIVE_VALIDATION_PLAN.md).

    cd services/analytics-api
    SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PRIVATE_KEY_PATH=... SNOWFLAKE_WAREHOUSE=... SNOWFLAKE_DATABASE=... \\
    SNOWFLAKE_SCHEMA=... SNOWFLAKE_ROLE=... \\
        python3 -m app.execution.warehouse_validation snowflake --allow-live --expect-target <account> --out report.md

With `--provision` there is nothing to set up by hand: it signs an admin in through the browser, creates the sandbox and a
read-only identity whose credential lives only in memory, runs the checks, and removes everything again in `finally`
(`warehouse_provisioning.py`). `--expect-target` is then the Snowflake account or the Databricks workspace host:

    python3 -m app.execution.warehouse_validation snowflake --allow-live --provision --admin-user <you> --expect-target <account> --out r.md
    python3 -m app.execution.warehouse_validation databricks --allow-live --provision --expect-target <host> --out r.md

This file never runs by itself and has not met a live warehouse. It refuses without `--allow-live` and without
`--expect-target` naming the one account or host it may use; a different one is refused before any connection and
neither value is printed. It reads credentials from the environment only. It runs eight checks in order and stops at the
first one that fails, because a failure is the finding and is reported, not retried. The report is numbers and codes only:
no query text, value, credential or host.

Statements are capped (`--max-statements`, default 30: each session also spends two on its settings). The slow statements
used for the timeout and cancel checks are fixed constants, read-only and never built from input.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.compiler import DatabricksCompilerAdapter, SnowflakeCompilerAdapter
from app.execution.gateway import CostLimitExceeded, ExecutionError, ExecutionLimits, ExecutionTimeout
from app.execution.warehouse_gateway import (
    DatabricksGateway,
    SnowflakeGateway,
    WarehouseCancelled,
    WarehouseSession,
    WarehouseTimeout,
)
from app.execution.warehouse_provisioning import (
    DatabricksProvisioner,
    ProvisioningError,
    SnowflakeProvisioner,
    connect_databricks_admin,
    connect_snowflake_admin,
    pick_warehouse,
)
from app.execution.warehouse_sessions import (
    DatabricksConfig,
    DatabricksSession,
    SnowflakeConfig,
    SnowflakeSession,
    WarehouseConfigError,
    WarehouseDriverMissing,
)
from app.metadata.warehouse_providers import DatabricksMetadataProvider, SnowflakeMetadataProvider
from app.semantic_registry import SemanticRegistry
from packages.platform_contracts.analytics_intent import AnalyticalIntent

REGISTRY_ROOT = Path(__file__).resolve().parents[2] / "semantic_registry"
TABLE = "sales_orders"
COLUMNS = {"id", "amount", "status", "created_at"}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_MARKER = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)\b")
_GRANTED = {"USAGE", "SELECT"}
_DATABRICKS_GRANTED = {"SELECT", "USE SCHEMA", "USE CATALOG", "BROWSE"}

# The same certified intent the dialect conformance tests compile: paid revenue by month, January to March 2024.
INTENT: dict[str, Any] = {
    "query_id": "live-validation-sales-monthly",
    "tenant_id": "tenant-a",
    "dataset_id": "orders",
    "semantic_contract": {"contract_id": "sales-core", "contract_version": "v1"},
    "metrics": [{"metric_id": "revenue"}],
    "group_by": [{"dimension_id": "created_at", "time_granularity": "month"}],
    "time_range": {"dimension_id": "created_at", "start": "2024-01-01T00:00:00Z", "end": "2024-03-31T23:59:59Z"},
    "filters": [{"field_id": "orders.status", "operator": "equals", "values": ["paid"]}],
    "sort": [{"target_kind": "metric", "target_id": "revenue", "direction": "desc"}],
    "limit": 25,
}

# Fixed, read-only and slow. Unverified on the live engines: the run reports whether each really stops.
SLOW_SQL = {
    "snowflake": "SELECT SYSTEM$WAIT(60)",
    "databricks": "SELECT count(*) FROM range(10000000000) WHERE sha2(CAST(id AS STRING), 256) LIKE 'ffffff%'",
}


class StopRule(BaseException):  # noqa: N818 - not an Exception on purpose: the gateway's catch-all must not swallow it
    """A stop rule fired. `code` is a short fixed token; the message never carries a value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    code: str = ""
    numbers: Mapping[str, float | int] = field(default_factory=dict)


@dataclass
class ValidationReport:
    engine: str
    checks: list[CheckResult] = field(default_factory=list)
    not_run: list[str] = field(default_factory=list)
    statements: int = 0
    notes: list[str] = field(default_factory=list)  # provisioning outcome, codes and counts only
    provisioning_ok: bool = True

    @property
    def passed(self) -> bool:
        return self.provisioning_ok and not self.not_run and all(c.ok for c in self.checks)


# ------------------------------------------------------------------------------------------------ expected rows


def expected_monthly_revenue() -> list[tuple[tuple[int, int], Decimal]]:
    """Paid revenue by month from the seed formula alone (no database): orders 1..100, 20 hours apart from 2024-01-01 UTC."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 3, 31, 23, 59, 59, tzinfo=timezone.utc)
    totals: dict[tuple[int, int], Decimal] = {}
    for i in range(1, 101):
        stamp = start + timedelta(hours=20 * i)
        if i % 5 == 0 or not (start <= stamp <= end):
            continue
        key = (stamp.year, stamp.month)
        totals[key] = totals.get(key, Decimal(0)) + Decimal(i)
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


def _rows_match(rows: Sequence[Sequence[Any]]) -> int:
    """How many returned rows equal the expected ones, in order (the intent sorts by revenue, descending)."""
    expected = expected_monthly_revenue()
    matched = 0
    for got, want in zip(rows, expected, strict=False):
        moment = got[0]
        try:
            same = (moment.year, moment.month) == want[0] and Decimal(str(got[1])) == want[1]
        except (AttributeError, ArithmeticError, ValueError):
            same = False
        matched += 1 if same else 0
    return matched


# ------------------------------------------------------------------------------------------------ plumbing


class _Counter:
    def __init__(self, cap: int) -> None:
        self.cap, self.statements, self.executes = cap, 0, 0
        self._lock = threading.Lock()

    def spend(self, n: int = 1, *, execute: bool = False) -> None:
        with self._lock:
            if self.statements + n > self.cap:
                raise StopRule("statement_cap")
            self.statements += n
            self.executes += 1 if execute else 0


class _CountedSession:
    """Every statement a session issues is counted against the cap before it is sent."""

    def __init__(self, inner: WarehouseSession, counter: _Counter) -> None:
        self._inner, self._counter = inner, counter

    def prepare(self, *, timeout_seconds: float) -> None:
        self._counter.spend(2)
        self._inner.prepare(timeout_seconds=timeout_seconds)

    def explain_cost(self, sql: str, parameters: Mapping[str, Any]) -> float | None:
        self._counter.spend()
        return self._inner.explain_cost(sql, parameters)

    def execute(self, sql: str, parameters: Mapping[str, Any]) -> Any:
        self._counter.spend(execute=True)
        return self._inner.execute(sql, parameters)

    def cancel(self) -> None:
        self._inner.cancel()

    def close(self) -> None:
        self._inner.close()


def _translate(sql: str, parameters: Mapping[str, Any], style: str) -> tuple[str, dict[str, Any]]:
    if set(_MARKER.findall(sql)) != set(parameters):
        raise ValueError("markers do not match the values")
    if style == "named":
        return sql, dict(parameters)
    return _MARKER.sub(lambda m: f"%({m.group(1)})s", sql.replace("%", "%%")), dict(parameters)


def _rows(session: Any, sql: str, parameters: Mapping[str, Any] | None = None, style: str = "named") -> list[dict[str, Any]]:
    bound_sql, bound = _translate(sql, parameters or {}, style)
    stream = session.execute(bound_sql, bound)
    try:
        columns = [str(c).lower() for c in stream.columns]
        out: list[dict[str, Any]] = []
        while True:
            batch = stream.fetchmany(500)
            if not batch:
                return out
            out.extend(dict(zip(columns, row, strict=False)) for row in batch)
    finally:
        stream.close()


def session_query_runner(session: Any, style: str) -> Callable[[str, Mapping[str, Any]], list[tuple[Any, ...]]]:
    """The metadata providers' `QueryRunner` over a real session: `:name` markers in, rows out."""

    def run(sql: str, parameters: Mapping[str, Any]) -> list[tuple[Any, ...]]:
        return [tuple(row.values()) for row in _rows(session, sql, parameters, style)]

    return run


def _ident(value: str) -> str:
    if not _IDENT.match(value):
        raise WarehouseConfigError("a configured name is not a plain identifier")
    return value


# ------------------------------------------------------------------------------------------------ engines


@dataclass(frozen=True)
class EngineSpec:
    name: str
    target: str  # account or host: compared to --expect-target and never printed
    new_session: Callable[[], WarehouseSession]
    gateway_cls: type
    compiler_cls: type
    style: str
    make_provider: Callable[[Callable[..., Any]], Any]
    grants_check: Callable[[Any], str | None]
    secrets: tuple[str, ...] = ()


def _snowflake_grants(config: SnowflakeConfig) -> Callable[[Any], str | None]:
    database = config.database.upper()

    def check(session: Any) -> str | None:
        rows = _rows(session, f"SHOW GRANTS TO ROLE {_ident(config.role)}", style="pyformat")
        if not rows:
            return "grants_not_visible"
        for row in rows:
            name = str(row.get("name", "")).upper()
            on = str(row.get("granted_on", "")).upper()
            inside = name == database or name.startswith(database + ".")
            if str(row.get("privilege", "")).upper() not in _GRANTED or not (on == "WAREHOUSE" or inside):
                return "extra_privilege"
        return None

    return check


def _databricks_grants(config: DatabricksConfig) -> Callable[[Any], str | None]:
    catalog, schema = _ident(config.catalog), _ident(config.schema)

    def check(session: Any) -> str | None:
        me = next(iter(_rows(session, "SELECT current_user() AS u")[0].values()))
        seen = 0
        for securable in (f"CATALOG `{catalog}`", f"SCHEMA `{catalog}`.`{schema}`", f"TABLE `{catalog}`.`{schema}`.`{TABLE}`"):
            for row in _rows(session, f"SHOW GRANTS ON {securable}"):
                if str(row.get("principal", "")) != str(me):
                    continue
                seen += 1
                if str(row.get("actiontype", "")).upper() not in _DATABRICKS_GRANTED:
                    return "extra_privilege"
        return None if seen else "grants_not_visible"  # direct grants only: group inheritance is not visible from here

    return check


def build_spec(engine: str, env: Mapping[str, str] | None = None) -> EngineSpec:
    if engine == "snowflake":
        return spec_from_config(SnowflakeConfig.from_env(env))
    if engine == "databricks":
        return spec_from_config(DatabricksConfig.from_env(env))
    raise ValueError("engine must be snowflake or databricks")


def spec_from_config(config: SnowflakeConfig | DatabricksConfig) -> EngineSpec:
    if isinstance(config, SnowflakeConfig):
        sf = config
        return EngineSpec(
            "snowflake", sf.account, lambda: SnowflakeSession(sf), SnowflakeGateway, SnowflakeCompilerAdapter, "pyformat",
            lambda run: SnowflakeMetadataProvider(run, database=sf.database, schema=sf.schema), _snowflake_grants(sf),
            tuple(
                s for s in (sf.private_key_passphrase, sf.private_key_path, sf.account,
                            base64.b64encode(sf.private_key_der).decode() if sf.private_key_der else None) if s
            ),
        )
    if isinstance(config, DatabricksConfig):
        db = config
        return EngineSpec(
            "databricks", db.server_hostname, lambda: DatabricksSession(db), DatabricksGateway, DatabricksCompilerAdapter, "named",
            lambda run: DatabricksMetadataProvider(run, catalog=db.catalog, schema=db.schema), _databricks_grants(db),
            (db.access_token, db.server_hostname, db.http_path),
        )
    raise TypeError("unsupported config")


# ------------------------------------------------------------------------------------------------ the checks


def _compile(spec: EngineSpec) -> Any:
    document = SemanticRegistry(REGISTRY_ROOT).get_certified("sales-core", "v1")
    return spec.compiler_cls().compile(AnalyticalIntent.model_validate(INTENT), document.contract)


def run_validation(spec: EngineSpec, *, max_statements: int = 30, max_cost_mib: float = 10.0) -> ValidationReport:
    report = ValidationReport(spec.name)
    counter = _Counter(max_statements)

    def new_session() -> WarehouseSession:
        return _CountedSession(spec.new_session(), counter)  # type: ignore[return-value]

    gateway = spec.gateway_cls(new_session, allowed_tables={TABLE})
    plan = _compile(spec)
    state: dict[str, Any] = {}

    def check_grants() -> CheckResult:
        session = new_session()
        try:
            session.prepare(timeout_seconds=15)
            code = spec.grants_check(session)
        finally:
            session.close()
        return CheckResult("read_only_grants", code is None, code or "")

    def check_metadata() -> CheckResult:
        session = new_session()
        try:
            session.prepare(timeout_seconds=15)
            snapshot = spec.make_provider(session_query_runner(session, spec.style)).get_snapshot(TABLE)
        finally:
            session.close()
        names = {c.name.lower() for c in snapshot.assets[0].columns}
        return CheckResult("metadata_read", names == COLUMNS, "" if names == COLUMNS else "columns_differ", {"columns": len(names)})

    def check_query() -> CheckResult:
        try:
            result = gateway.execute(plan, limits=ExecutionLimits(timeout_seconds=30, max_cost_units=max_cost_mib))
        except CostLimitExceeded:
            raise StopRule("cost_over_cap") from None
        except ExecutionTimeout:
            raise StopRule("unexpected_timeout") from None
        except ExecutionError:
            raise StopRule("query_failed") from None
        state["result"] = result
        return CheckResult(
            "compiled_query", True, numbers={"rows": len(result.rows), "estimate_mib_x1000": round(result.estimated_cost_units * 1000), "elapsed_ms": result.elapsed_ms}
        )

    def check_equivalence() -> CheckResult:
        rows = state["result"].rows
        expected = len(expected_monthly_revenue())
        matched = _rows_match(rows)
        ok = matched == expected and len(rows) == expected
        return CheckResult("result_equivalence", ok, "" if ok else "result_mismatch", {"expected_rows": expected, "got_rows": len(rows), "matching": matched})

    def check_ceiling() -> CheckResult:
        estimate = state["result"].estimated_cost_units
        if not estimate > 0:
            return CheckResult("cost_ceiling", False, "estimate_zero")
        before = counter.executes
        try:
            gateway.execute(plan, limits=ExecutionLimits(timeout_seconds=30, max_cost_units=estimate / 2))
        except CostLimitExceeded:
            ok = counter.executes == before  # the refusal came before any query statement was submitted
            return CheckResult("cost_ceiling", ok, "" if ok else "query_submitted")
        return CheckResult("cost_ceiling", False, "not_refused")

    def check_timeout() -> CheckResult:
        session = new_session()
        started = time.monotonic()
        try:
            session.prepare(timeout_seconds=2)
            session.execute(SLOW_SQL[spec.name], {})
        except WarehouseTimeout:
            elapsed = time.monotonic() - started
            return CheckResult("timeout", elapsed < 25, "" if elapsed < 25 else "timeout_late", {"elapsed_ms": round(elapsed * 1000)})
        finally:
            session.close()
        return CheckResult("timeout", False, "no_timeout")

    def check_cancel() -> CheckResult:
        session = new_session()
        outcome: dict[str, Any] = {}

        def work() -> None:
            try:
                session.prepare(timeout_seconds=30)
                session.execute(SLOW_SQL[spec.name], {})
                outcome["end"] = "finished"
            except WarehouseCancelled:
                outcome["end"] = "cancelled"
            except StopRule as stop:
                outcome["end"] = stop.code
            except Exception as exc:  # noqa: BLE001 - the type only, never the message
                outcome["end"] = f"error:{type(exc).__name__}"[:60]

        thread = threading.Thread(target=work, daemon=True)
        started = time.monotonic()
        thread.start()
        time.sleep(1.5)
        try:
            session.cancel()
            thread.join(20)
        finally:
            session.close()
        elapsed = round((time.monotonic() - started) * 1000)
        end = outcome.get("end", "still_running")
        return CheckResult("cancel", end == "cancelled", "" if end == "cancelled" else end, {"elapsed_ms": elapsed})

    def check_leaks() -> CheckResult:
        rendered = render_markdown(report)
        found = sum(1 for text in (*spec.secrets, plan.sql, SLOW_SQL[spec.name]) if text and text in rendered)
        return CheckResult("no_leaks", found == 0, "" if found == 0 else "secret_in_report", {"found": found})

    steps = [
        ("read_only_grants", check_grants), ("metadata_read", check_metadata), ("compiled_query", check_query),
        ("result_equivalence", check_equivalence), ("cost_ceiling", check_ceiling), ("timeout", check_timeout),
        ("cancel", check_cancel), ("no_leaks", check_leaks),
    ]
    for index, (name, step) in enumerate(steps):
        try:
            result = step()
        except StopRule as stop:
            result = CheckResult(name, False, stop.code)
        except Exception as exc:  # noqa: BLE001 - classify by type; a driver message can carry SQL or values
            result = CheckResult(name, False, f"error:{type(exc).__name__}"[:60])
        report.checks.append(result)
        report.statements = counter.statements
        if not result.ok:
            report.not_run = [n for n, _ in steps[index + 1 :]]
            break
    return report


# ------------------------------------------------------------------------------------------------ report and CLI


def render_markdown(report: ValidationReport) -> str:
    lines = [
        f"# Live validation: {report.engine} (sandbox)",
        "",
        "Numbers and codes only. No query text, value, credential or host is recorded here.",
        "",
        "| check | result | code | numbers |",
        "|---|---|---|---|",
    ]
    for c in report.checks:
        numbers = ", ".join(f"{k}={v}" for k, v in c.numbers.items()) or "-"
        lines.append(f"| {c.name} | {'ok' if c.ok else 'not ok'} | {c.code or '-'} | {numbers} |")
    lines += [f"| {name} | not run | - | - |" for name in report.not_run]
    lines += ["", *(f"Provisioning: {note}" for note in report.notes)]
    lines += [f"Statements used: {report.statements}", f"Overall: {'all checks ok' if report.passed else 'not all checks ok'}", ""]
    return "\n".join(lines)


def make_provisioner(args: argparse.Namespace) -> Any:
    """Sign the admin in through the browser and return the provisioner. Replaced in tests."""
    if args.engine == "snowflake":
        if not args.admin_user:
            raise WarehouseConfigError("--admin-user is required for snowflake with --provision")
        return SnowflakeProvisioner(connect_snowflake_admin(args.expect_target, args.admin_user), account=args.expect_target)
    client = connect_databricks_admin(args.expect_target)
    return DatabricksProvisioner(client, host=args.expect_target, warehouse_id=pick_warehouse(client, args.databricks_warehouse))


def _provisioned(args: argparse.Namespace) -> int:
    try:
        provisioner = make_provisioner(args)
    except (WarehouseConfigError, WarehouseDriverMissing, ProvisioningError) as exc:
        print(f"Not run. {getattr(exc, 'code', None) or exc}", file=sys.stderr)  # a code or a variable/flag name, never a value
        return 2
    report = ValidationReport(args.engine)
    notes: list[str] = []
    teardown_ok = True
    try:
        try:
            provisioned = provisioner.setup()
            notes.append(f"setup ok, {provisioned.steps} steps")
            report = run_validation(spec_from_config(provisioned.config), max_statements=args.max_statements, max_cost_mib=args.max_cost_mib)
        except ProvisioningError as exc:
            notes.append(f"setup failed: {exc.code}")
            report.provisioning_ok = False
    finally:
        try:
            notes.append(f"teardown ok, {provisioner.teardown()} steps")
        except ProvisioningError as exc:
            teardown_ok = False
            notes.append(f"TEARDOWN INCOMPLETE: {exc.code}")
        finally:
            provisioner.close()
    report.notes = notes
    report.provisioning_ok = report.provisioning_ok and teardown_ok
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(report))
    ok = sum(1 for c in report.checks if c.ok)
    print(f"{args.engine}: {ok} of {len(report.checks) + len(report.not_run)} checks ok, {report.statements} statements. {notes[-1]}. Report: {args.out}")
    if not teardown_ok:
        print("Teardown did not finish: remove the COMPASS_VALIDATION / compass_validation objects and the validation identity by hand.", file=sys.stderr)
    return 0 if report.passed else 1


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("engine", choices=("snowflake", "databricks"))
    parser.add_argument("--allow-live", action="store_true", help="required: this contacts a live sandbox warehouse")
    parser.add_argument("--expect-target", help="required: the one Snowflake account or Databricks host this run may use")
    parser.add_argument("--provision", action="store_true", help="create the sandbox and a read-only identity first, remove them after")
    parser.add_argument("--admin-user", help="Snowflake admin user for the browser sign-in (with --provision)")
    parser.add_argument("--databricks-warehouse", help="name of the SQL warehouse to use (with --provision); never edited")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-statements", type=int, default=30)
    parser.add_argument("--max-cost-mib", type=float, default=10.0)
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    if not args.allow_live or not args.expect_target:
        print("Not run. A live run needs --allow-live and --expect-target, and the owner's approval for this engine.", file=sys.stderr)
        return 2
    if not 1 <= args.max_statements <= 100 or not args.max_cost_mib > 0:
        print("Not run. --max-statements must be 1 to 100 and --max-cost-mib positive.", file=sys.stderr)
        return 2
    if args.provision:
        return _provisioned(args)
    try:
        spec = build_spec(args.engine, env)
    except WarehouseConfigError as exc:
        print(f"Not run. {exc}", file=sys.stderr)  # names variables only
        return 2
    if spec.target != args.expect_target:
        print("Not run. The configured target does not match --expect-target.", file=sys.stderr)  # neither value is printed
        return 2

    report = run_validation(spec, max_statements=args.max_statements, max_cost_mib=args.max_cost_mib)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(report))
    ok = sum(1 for c in report.checks if c.ok)
    print(f"{spec.name}: {ok} of {len(report.checks) + len(report.not_run)} checks ok, {report.statements} statements. Report: {args.out}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
