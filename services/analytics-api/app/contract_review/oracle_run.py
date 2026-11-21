"""`python -m app.contract_review.oracle_run --contract C.json --eval E.json --factory pkg.module:build [--out result.json]`: the recurring oracle check, as a
command a scheduler can call (see `docs/design/ORACLE_RECURRING_RUNBOOK.md`). No schedule exists in this repository.

It runs `run_oracle_checks` (`oracle.py`) for the eval file's cases that have an oracle, through a gateway and an answer callable that a **factory** supplies:
`build(document) -> (gateway, answer)`. This module opens no connection, calls no model and holds no credential; the deployment's factory does the wiring.
A run is bounded: at most `--max-statements` oracle statements and `--max-seconds` of wall-clock. A case that the bound stops is reported `not_run`, never
skipped silently. The result is a small JSON file of statuses and counts: no row value, no number, no driver message (exception types only).

Exit codes: 0 every case matched; 1 any mismatch, oracle or answer failure, case not run, or no case with an oracle; 2 refused (bad input, no factory).
A match is not certification; a mismatch is reported, never hidden.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.contract_review.behaviour import EvalFile
from app.contract_review.oracle import STATUSES, run_oracle_checks
from app.execution.gateway import ExecutionLimits
from packages.platform_contracts.semantic import SemanticRegistryDocument

DEFAULT_MAX_STATEMENTS = 20
DEFAULT_MAX_SECONDS = 300.0
RUN_STATUSES = (*STATUSES, "not_run")


class _CountingGateway:
    """Counts the statements sent through the real gateway; the loop checks the cap before each case, so this is the backstop that makes the cap a fact."""

    def __init__(self, inner: Any, max_statements: int) -> None:
        self._inner, self._max, self.statements = inner, max_statements, 0

    @property
    def dialect(self) -> str:
        return self._inner.dialect

    def execute(self, plan: Any, **kwargs: Any) -> Any:
        if self.statements >= self._max:
            raise RuntimeError("statement_cap_reached")
        self.statements += 1
        return self._inner.execute(plan, **kwargs)


def run_capped(cases: list, *, answer: Callable[[Any], Any], gateway: Any, max_statements: int, max_seconds: float,
               clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Run the oracle cases one at a time under the cap. Returns the result document (statuses only)."""
    counting = _CountingGateway(gateway, max_statements)
    started = clock()
    rows: list[dict[str, str]] = []
    capped = ""
    for index, case in enumerate(cases):
        if counting.statements >= max_statements:
            capped = "max_statements"
        elif clock() - started >= max_seconds:
            capped = "max_seconds"
        if capped:
            rows += [{"case_id": c.case_id, "status": "not_run", "detail": capped} for c in cases[index:]]
            break
        remaining = max(max_seconds - (clock() - started), 0.001)
        limits = ExecutionLimits(max_rows=2, timeout_seconds=min(30.0, remaining))
        [result] = run_oracle_checks([case], answer=answer, gateway=counting, limits=limits)
        rows.append({"case_id": result.case_id, "status": result.status, **({"detail": result.detail} if result.detail else {})})
    tally = {s: sum(1 for r in rows if r["status"] == s) for s in RUN_STATUSES}
    return {"cases": rows, "tally": tally, "statements_used": counting.statements, "max_statements": max_statements, "max_seconds": max_seconds,
            "elapsed_seconds": round(clock() - started, 3), "complete": not capped}


def _load_factory(spec: str) -> Callable[[SemanticRegistryDocument], tuple[Any, Callable[[Any], Any]]]:
    module, _, name = spec.partition(":")
    if not module or not name:
        raise ValueError("factory_must_be_module_colon_name")
    return getattr(importlib.import_module(module), name)


def main(argv: list[str] | None = None, *, factory: Callable[..., Any] | None = None, clock: Callable[[], float] = time.monotonic) -> int:
    p = argparse.ArgumentParser(prog="app.contract_review.oracle_run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--contract", required=True, type=Path, help="the certified contract document the answers are for")
    p.add_argument("--eval", required=True, type=Path, dest="eval_file", help="an eval file; cases with an oracle are checked")
    p.add_argument("--factory", help="module:callable returning (gateway, answer) for the contract; the deployment owns the wiring")
    p.add_argument("--max-statements", type=int, default=DEFAULT_MAX_STATEMENTS)
    p.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    p.add_argument("--out", type=Path, help="write the JSON result here (default: stdout)")
    args = p.parse_args(argv)
    if args.max_statements < 1 or args.max_seconds <= 0:
        print("refused: the caps must be positive", file=sys.stderr)
        return 2
    try:
        document = SemanticRegistryDocument.model_validate(json.loads(args.contract.read_text()))
        evals = EvalFile.model_validate(json.loads(args.eval_file.read_text()))
        if evals.contract_id != document.contract.id:
            print("refused: ContractMismatch", file=sys.stderr)
            return 2
        build = factory or (_load_factory(args.factory) if args.factory else None)
        if build is None:
            print("refused: no gateway factory; this command opens no connection of its own", file=sys.stderr)
            return 2
        gateway, answer = build(document)
    except (OSError, ValueError, ValidationError, ImportError, AttributeError, TypeError) as exc:
        print(f"refused: {type(exc).__name__}", file=sys.stderr)  # the type only: these files and a driver may hold anything
        return 2
    cases = [c for c in evals.cases if c.oracle is not None]
    result = run_capped(cases, answer=answer, gateway=gateway, max_statements=args.max_statements, max_seconds=args.max_seconds, clock=clock)
    result = {"contract_id": document.contract.id, "contract_version": document.contract.version, "certified": False, **result}
    ok = bool(cases) and result["tally"]["match"] == len(cases) and result["complete"]
    result["ok"] = ok
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    try:
        if args.out:
            args.out.write_text(text)
        else:
            sys.stdout.write(text)
    except OSError as exc:
        print(f"refused: {type(exc).__name__}", file=sys.stderr)
        return 2
    if not cases:
        print("no case has an oracle, so nothing was checked", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
