"""A live-oracle check (E5 of `docs/design/SEMANTIC_TRUST_FOUNDATIONS_PLAN.md`): does the product's real answer match an independent statement?

A pinned expected number goes stale when a customer's data changes. An **oracle** is an independent statement, written by the contract's
owner, that returns the number a question should produce *now*. It runs through the **same gateway as every answer**, so it gets the same
read-only validation, the same table allow-list (the connection's ceiling), the same cost bound and the same timeout. The product's answer
comes from its real path, passed in as a callable, never re-implemented here.

What a match proves: the compiler, the definition as written and the data agree today. What it cannot prove: that the business meaning is
right. A match is never called "certified". A mismatch is reported, never hidden. A failure of the oracle itself is its own status, so it can
never read as a pass. Failures carry the exception type only: driver messages can quote data.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from app.contract_review.behaviour import EvalCase
from app.execution.gateway import ExecutionLimits

STATUSES = ("match", "mismatch", "oracle_failed", "answer_failed")


@dataclass(frozen=True)
class OracleResult:
    case_id: str
    status: str
    product_value: Decimal | None = None
    oracle_value: Decimal | None = None
    detail: str = ""


def _number(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise InvalidOperation("boolean")
    return Decimal(str(value))


def run_oracle_checks(cases: Iterable[EvalCase], *, answer: Callable[[EvalCase], Any], gateway: Any,
                      limits: ExecutionLimits | None = None) -> list[OracleResult]:
    """For each case that has an oracle: take the product's answer, run the oracle through `gateway`, compare. Cases without an oracle are skipped."""
    limits = limits or ExecutionLimits(max_rows=2)
    out: list[OracleResult] = []
    for case in cases:
        if case.oracle is None:
            continue
        try:
            product = _number(answer(case))
        except Exception as exc:  # noqa: BLE001
            out.append(OracleResult(case.case_id, "answer_failed", detail=type(exc).__name__))
            continue
        try:
            plan = SimpleNamespace(dialect=gateway.dialect, sql=case.oracle.sql, parameters=dict(case.oracle.parameters))
            result = gateway.execute(plan, limits=limits)
            if len(result.rows) != 1 or len(result.rows[0]) != 1 or result.truncated:
                raise ValueError("oracle_must_return_one_value")
            expected = _number(result.rows[0][0])
        except Exception as exc:  # noqa: BLE001 - the oracle's own failure is a status, never a pass
            out.append(OracleResult(case.case_id, "oracle_failed", product, None, type(exc).__name__))
            continue
        allowed = max(Decimal(str(case.oracle.tolerance_abs)), Decimal(str(case.oracle.tolerance_rel)) * abs(expected))
        status = "match" if abs(product - expected) <= allowed else "mismatch"
        out.append(OracleResult(case.case_id, status, product, expected))
    return out


def render_oracle_report(results: list[OracleResult]) -> str:
    tally = {s: sum(1 for r in results if r.status == s) for s in STATUSES}
    lines = ["# Oracle check", "", "Each case compared the product's real answer with an independent statement run through the same read-only gateway.",
             "Values are aggregates. A match means the compiler, the definition as written and the data agree today; it is not certification.", "",
             "Result: " + ", ".join(f"{tally[s]} {s.replace('_', ' ')}" for s in STATUSES), ""]
    for r in results:
        value = "" if r.product_value is None else f" product {r.product_value}" + ("" if r.oracle_value is None else f", oracle {r.oracle_value}")
        lines.append(f"- `{r.case_id}`: **{r.status}**{value}" + (f" ({r.detail})" if r.detail else ""))
    return "\n".join(lines) + "\n"


__all__ = ["OracleResult", "STATUSES", "render_oracle_report", "run_oracle_checks"]
