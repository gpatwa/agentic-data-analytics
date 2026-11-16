"""Connector contract and conformance suite (plan C1, docs/WAREHOUSE_CONNECTOR_AND_ONBOARDING_PLAN.md).

This is the connector framework: a driver-neutral session contract (`WarehouseSession`, in `warehouse_gateway.py`), a
**capability record** per engine, and eight checks that any engine's session factory must pass. Frameworks and vendor drivers
supply transport; this suite decides whether a connector is acceptable. It has no live-run guards, no provisioning and no
command line of its own: `warehouse_validation.py` wraps it for a live sandbox, and the tests run it against fakes.

Adding an engine means: a session that implements the contract, a gateway and compiler for its dialect, a capability record
(declared here, and shown in docs/WAREHOUSE_CONNECTOR_CAPABILITIES.md, which is generated and tested against this file), and a
passing run of these checks on fakes and then, with approval, on a sandbox.

Capabilities are claims the suite holds the connector to. Reading the grants (read-only proof) and a cost estimate are mandatory
for every engine: no estimate, no run. An engine must be able to stop a statement, by a statement timeout or by cancel; a
capability it does not claim is reported "not claimed" and its check is skipped, and a claimed one must pass.

Everything here follows the live run of 2026-11-15; nothing in this module contacts a warehouse by itself.
"""

from __future__ import annotations

import argparse
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

from app.execution.gateway import CostLimitExceeded, ExecutionError, ExecutionLimits, ExecutionTimeout
from app.execution.warehouse_gateway import WarehouseCancelled, WarehouseSession, WarehouseTimeout
from app.execution.warehouse_sessions import WarehouseConfigError
from app.semantic_registry import SemanticRegistry
from packages.platform_contracts.analytics_intent import AnalyticalIntent

REGISTRY_ROOT = Path(__file__).resolve().parents[2] / "semantic_registry"
TABLE = "sales_orders"
COLUMNS = {"id", "amount", "status", "created_at"}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_MARKER = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)\b")

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


STEP_SECONDS = 120.0  # a hard wall-clock bound on every check: a hung call is reported, never waited on
SLOW_WAIT_SECONDS = 20.0  # how long the timeout and cancel checks wait for a slow statement to stop


def _bounded(step: Callable[[], Any], seconds: float) -> Any:
    """Run a check in a worker thread and stop waiting after `seconds`, so the run (and its teardown) stays in control."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = step()
        except BaseException as exc:  # noqa: BLE001 - re-raised below in the calling thread, including a StopRule
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise StopRule("check_hung")
    if "error" in box:
        raise box["error"]
    return box["result"]


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




# ------------------------------------------------------------------------------------------------ capabilities


@dataclass(frozen=True)
class ConnectorCapabilities:
    """What a connector claims. Mandatory for every engine: `cost_estimate` and `read_only_proof` (so they are text, not flags)."""

    engine: str
    param_style: str
    statement_timeout: bool  # a server-side statement timeout is set by the session and its error is mapped
    cancel: bool  # a running statement can be cancelled from another thread
    cost_estimate: str  # where the estimate comes from, and its unit
    read_only_proof: str  # how the suite shows the identity is read-only
    runtime_auth: tuple[str, ...]
    admin_sign_in: tuple[str, ...]
    transport_notes: str
    known_limits: tuple[str, ...]
    verified_live: str  # the recorded sandbox run, or "not verified live"

    def __post_init__(self) -> None:
        if not (self.statement_timeout or self.cancel):
            raise ValueError("an engine must be able to stop a statement: claim a statement timeout or cancel")
        if not self.cost_estimate.strip() or not self.read_only_proof.strip():
            raise ValueError("a cost estimate and a read-only proof are mandatory")


CAPABILITIES: dict[str, ConnectorCapabilities] = {
    "snowflake": ConnectorCapabilities(
        engine="snowflake",
        param_style="pyformat (client-side binding)",
        statement_timeout=True,
        cancel=True,
        cost_estimate="EXPLAIN USING JSON, GlobalStats.bytesAssigned, in MiB scanned",
        read_only_proof="SHOW GRANTS TO ROLE: only USAGE and SELECT inside the named database, plus USAGE on the warehouse",
        runtime_auth=("key pair",),
        admin_sign_in=("browser SSO (needs an identity provider; not available on every account)",),
        transport_notes="Async execution with abort by query id; the connector bundles its own certificates",
        known_limits=(
            "Column names appear only after the first fetch (handled)",
            "No per-session read-only switch: read-only rests on the role",
            "Result value normalization across engines is not done",
        ),
        verified_live="2026-11-15, sandbox, 8 of 8 checks (setup run from the signed-in console)",
    ),
    "databricks": ConnectorCapabilities(
        engine="databricks",
        param_style="named (native markers)",
        statement_timeout=True,
        cancel=True,
        cost_estimate="EXPLAIN COST text, sizeInBytes, in MiB scanned",
        read_only_proof="SHOW GRANTS on catalog, schema and table for the current principal (direct grants only; groups are not visible)",
        runtime_auth=("OAuth M2M (service principal client credentials; short-lived tokens refreshed by the SDK)", "access token (legacy)"),
        admin_sign_in=("OAuth browser sign-in",),
        transport_notes="Passes certifi's CA bundle and caps connection retries at 30 s; cancel waits for the driver's command id",
        known_limits=(
            "The access-token method is legacy and kept for compatibility; OAuth M2M is the default",
            "Grant listing sees direct grants only",
            "Result value normalization across engines is not done",
        ),
        verified_live="2026-11-16, sandbox, 8 of 8 checks with OAuth M2M (the default; fully automatic provisioning); 2026-11-15, 8 of 8 with an access token",
    ),
}

# The four capability-gated checks: a capability the connector does not claim makes its check "not claimed", not a failure.
OPTIONAL_CHECKS = {"timeout": "statement_timeout", "cancel": "cancel"}


def capability_table_markdown() -> str:
    """The generated text of docs/WAREHOUSE_CONNECTOR_CAPABILITIES.md. A test keeps the checked-in file equal to this."""
    lines = [
        "# Warehouse connector capabilities",
        "",
        "Generated by `python3 -m app.execution.connector_conformance --print-table` from `CAPABILITIES` in",
        "`services/analytics-api/app/execution/connector_conformance.py`. Do not edit by hand: a test fails if it drifts.",
        "A connector must pass the conformance suite for every capability it claims; see",
        "`docs/WAREHOUSE_CONNECTOR_AND_ONBOARDING_PLAN.md`.",
        "",
        "| Capability | " + " | ".join(c.engine for c in CAPABILITIES.values()) + " |",
        "|---|" + "---|" * len(CAPABILITIES),
    ]
    rows = [
        ("Parameter style", lambda c: c.param_style),
        ("Statement timeout", lambda c: "claimed" if c.statement_timeout else "not claimed"),
        ("Cancel", lambda c: "claimed" if c.cancel else "not claimed"),
        ("Cost estimate (mandatory)", lambda c: c.cost_estimate),
        ("Read-only proof (mandatory)", lambda c: c.read_only_proof),
        ("Runtime auth", lambda c: "; ".join(c.runtime_auth)),
        ("Admin sign-in", lambda c: "; ".join(c.admin_sign_in)),
        ("Transport", lambda c: c.transport_notes),
        ("Known limits", lambda c: "; ".join(c.known_limits)),
        ("Verified live", lambda c: c.verified_live),
    ]
    for label, get in rows:
        lines.append(f"| {label} | " + " | ".join(get(c).replace("|", "/") for c in CAPABILITIES.values()) + " |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------ the contract under test


@dataclass(frozen=True)
class ConformanceSpec:
    name: str
    target: str  # account or host: compared to --expect-target and never printed
    new_session: Callable[[], WarehouseSession]
    gateway_cls: type
    compiler_cls: type
    style: str
    make_provider: Callable[[Callable[..., Any]], Any]
    grants_check: Callable[[Any], str | None]
    secrets: tuple[str, ...] = ()
    capabilities: ConnectorCapabilities | None = None
    slow_sql: str = ""  # a fixed, read-only, slow statement; defaults to the engine's entry in SLOW_SQL


EngineSpec = ConformanceSpec  # the name used by the live runner and its tests


def _slow_sql(spec: ConformanceSpec) -> str:
    return spec.slow_sql or SLOW_SQL[spec.name]


# ------------------------------------------------------------------------------------------------ the checks


def _compile(spec: ConformanceSpec) -> Any:
    document = SemanticRegistry(REGISTRY_ROOT).get_certified("sales-core", "v1")
    return spec.compiler_cls().compile(AnalyticalIntent.model_validate(INTENT), document.contract)


def run_conformance(spec: ConformanceSpec, *, max_statements: int = 30, max_cost_mib: float = 10.0) -> ValidationReport:
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
        outcome: dict[str, Any] = {}

        def work() -> None:
            try:
                session.prepare(timeout_seconds=2)
                session.execute(_slow_sql(spec), {})
                outcome["end"] = "finished"
            except WarehouseTimeout:
                outcome["end"] = "timeout"
            except StopRule as stop:
                outcome["end"] = stop.code
            except Exception as exc:  # noqa: BLE001 - the type only
                outcome["end"] = f"error:{type(exc).__name__}"[:60]

        started = time.monotonic()
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(SLOW_WAIT_SECONDS)
        elapsed = round((time.monotonic() - started) * 1000)
        try:
            if thread.is_alive():  # the 2 second timeout did not stop it: stop it ourselves and say so
                session.cancel()
                thread.join(5)
                return CheckResult("timeout", False, "no_timeout", {"elapsed_ms": elapsed})
        finally:
            session.close()
        end = outcome.get("end", "")
        return CheckResult("timeout", end == "timeout", "" if end == "timeout" else ("no_timeout" if end == "finished" else end), {"elapsed_ms": elapsed})

    def check_cancel() -> CheckResult:
        session = new_session()
        outcome: dict[str, Any] = {}

        def work() -> None:
            try:
                session.prepare(timeout_seconds=30)
                session.execute(_slow_sql(spec), {})
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
            thread.join(SLOW_WAIT_SECONDS)
        finally:
            session.close()
        elapsed = round((time.monotonic() - started) * 1000)
        end = outcome.get("end", "still_running")
        return CheckResult("cancel", end == "cancelled", "" if end == "cancelled" else end, {"elapsed_ms": elapsed})

    def check_leaks() -> CheckResult:
        rendered = render_markdown(report)
        found = sum(1 for text in (*spec.secrets, plan.sql, _slow_sql(spec)) if text and text in rendered)
        return CheckResult("no_leaks", found == 0, "" if found == 0 else "secret_in_report", {"found": found})

    steps = [
        ("read_only_grants", check_grants), ("metadata_read", check_metadata), ("compiled_query", check_query),
        ("result_equivalence", check_equivalence), ("cost_ceiling", check_ceiling), ("timeout", check_timeout),
        ("cancel", check_cancel), ("no_leaks", check_leaks),
    ]
    for index, (name, step) in enumerate(steps):
        capability = OPTIONAL_CHECKS.get(name)
        if capability and spec.capabilities is not None and not getattr(spec.capabilities, capability):
            report.checks.append(CheckResult(name, True, "not_claimed"))  # the connector does not claim it: skipped, said so
            continue
        try:
            result = _bounded(step, STEP_SECONDS)
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
        result = "not claimed" if c.code == "not_claimed" else ("ok" if c.ok else "not ok")
        lines.append(f"| {c.name} | {result} | {'-' if c.code == 'not_claimed' else (c.code or '-')} | {numbers} |")
    lines += [f"| {name} | not run | - | - |" for name in report.not_run]
    lines += ["", *(f"Provisioning: {note}" for note in report.notes)]
    lines += [f"Statements used: {report.statements}", f"Overall: {'all checks ok' if report.passed else 'not all checks ok'}", ""]
    return "\n".join(lines)



run_validation = run_conformance  # the name used by the live runner and its tests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the generated connector capability table.")
    parser.add_argument("--print-table", action="store_true", required=True)
    parser.parse_args(argv)
    sys.stdout.write(capability_table_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
