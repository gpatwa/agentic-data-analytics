"""Offline driver: draft an ontology from a named Databricks schema through an injected session and an injected model (approval D, preparation only).

    python -m app.ontology_draft.databricks_draft_run --allow-live --catalog dev --schema compass_synthetic_food_delivery \
        --tables orders_flat --contract-id fd-draft

It introspects and profiles through `databricks_source` (`introspect_session`, `infer_keys`, `profile_session`), drafts and validates
with the existing drafter and validator, and writes a DRAFT with `write_draft`, never a registry file and never a certified contract.

Safety, all enforced here and tested on fakes:
- It refuses to run without `--allow-live` (the run is a live read and a model call that need the owner's approval).
- The session and the model are injected. Nothing here reads, accepts or prints a credential; the live wiring that builds them is
  outside this module. A model built from the environment, if any, is built by the caller.
- Only the listed tables are read: every statement is checked before it reaches the session, and a table outside the list stops the run.
- Caps: at most 3 model calls, a reply token limit, and a wall-clock limit checked before every session statement and model call.
- Stop rules (a leak, a validator escape, an injected canary, a table outside the list, a failed EXPLAIN) write nothing and exit 1.
- Output goes to a drafts directory that is refused if it is, or sits under, a semantic registry root.
Output carries codes and counts only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ontology_draft.databricks_source import (
    databricks_explain_probe,
    infer_keys,
    introspect_session,
    profile_session,
    qualified_prefix,
)
from app.ontology_draft.draft_run import stop_reasons
from app.ontology_draft.drafter import MAX_REPLY_TOKENS, PROMPT_VERSION
from app.ontology_draft.emit import DEFAULT_DRAFTS_DIR, write_draft
from app.ontology_draft.evaluation import EvalCase, RunReport, run_case_full
from app.ontology_draft.proposal import DraftError
from app.ontology_draft.validate import DraftContext

MAX_MODEL_CALLS = 3
DEFAULT_WALL_CLOCK_SECONDS = 600.0
_REGISTRY_DIRS = ("semantic_registry", "semantic_registry_sandbox", "contracts")
_QUALIFIED = re.compile(r"`([^`]+)`\.`([^`]+)`\.`([^`]+)`")
_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,254}$")


@dataclass(frozen=True)
class Caps:
    max_model_calls: int = MAX_MODEL_CALLS
    max_reply_tokens: int = MAX_REPLY_TOKENS
    wall_clock_seconds: float = DEFAULT_WALL_CLOCK_SECONDS

    def __post_init__(self) -> None:
        if not 1 <= self.max_model_calls <= MAX_MODEL_CALLS:
            raise ValueError(f"max_model_calls must be 1 to {MAX_MODEL_CALLS}")
        if not 1 <= self.max_reply_tokens <= MAX_REPLY_TOKENS:
            raise ValueError(f"max_reply_tokens must be 1 to {MAX_REPLY_TOKENS}")
        if not 0 < self.wall_clock_seconds <= DEFAULT_WALL_CLOCK_SECONDS * 6:
            raise ValueError("wall_clock_seconds out of range")


class Budget:
    """Wall clock and model-call counter shared by the session guard and the model wrapper."""

    def __init__(self, caps: Caps, clock: Callable[[], float]) -> None:
        self.caps, self.clock, self.calls = caps, clock, 0
        self.deadline = clock() + caps.wall_clock_seconds

    def check_time(self) -> None:
        if self.clock() > self.deadline:
            raise DraftError("wall_clock_cap")


class CappedModel:
    """Counts calls and clamps the reply size. The call that would exceed the cap raises before the model is reached."""

    def __init__(self, model: Any, budget: Budget) -> None:
        self._model, self._budget = model, budget

    def complete_json(self, *, prompt: str, schema: dict, max_tokens: int) -> Any:
        self._budget.check_time()
        if self._budget.calls >= self._budget.caps.max_model_calls:
            raise DraftError("model_call_cap")
        self._budget.calls += 1
        return self._model.complete_json(prompt=prompt, schema=schema, max_tokens=min(max_tokens, self._budget.caps.max_reply_tokens))


class GuardedSession:
    """Passes statements through only if every qualified table is in the list, and only inside the wall clock."""

    def __init__(self, session: Any, catalog: str, schema: str, tables: Sequence[str], budget: Budget, read_log: list[str]) -> None:
        self._session, self._budget, self._log = session, budget, read_log
        self._allowed = {(catalog.lower(), schema.lower(), t.lower()) for t in tables}

    def execute(self, sql: str, params: dict) -> Any:
        self._budget.check_time()
        for found in _QUALIFIED.findall(sql):
            key = tuple(part.lower() for part in found)
            if key not in self._allowed:
                raise DraftError("table_outside_scope")
            if key[2] not in self._log:
                self._log.append(key[2])
        return self._session.execute(sql, params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


@dataclass
class DraftRunResult:
    code: str  # "written" or a fixed stop/error code, never a value
    ok: bool
    path: Path | None = None
    model_calls: int = 0
    tables_read: tuple[str, ...] = ()
    report: RunReport | None = None
    stop_reasons: tuple[str, ...] = field(default_factory=tuple)


def _check_out_dir(out_dir: Path) -> None:
    if any(part in _REGISTRY_DIRS for part in out_dir.resolve().parts):
        raise DraftError("out_dir_is_a_registry")


def run(*, allow_live: bool, catalog: str, schema: str, tables: Sequence[str], session_factory: Callable[[], Any], model: Any, out_dir: Path = DEFAULT_DRAFTS_DIR,
        contract_id: str = "auto-draft", model_id: str = "model", caps: Caps | None = None, clock: Callable[[], float] = time.monotonic,
        secrets: tuple[str, ...] = (), canaries: tuple[str, ...] = ()) -> DraftRunResult:
    """Draft from exactly `tables`. `secrets` and `canaries` are values that must never appear in the prompt or the output."""
    if not allow_live:
        return DraftRunResult("refused_not_live", False)
    caps = caps or Caps()
    budget = Budget(caps, clock)
    reads: list[str] = []
    if not tables or not all(_PLAIN.match(x or "") for x in (catalog, schema, *tables)) or len(set(tables)) != len(tables):
        return DraftRunResult("invalid_scope", False)
    try:
        _check_out_dir(out_dir)
        capped = CappedModel(model, budget)
        make = lambda: GuardedSession(session_factory(), catalog, schema, tables, budget, reads)  # noqa: E731
        session = make()
        try:
            intro = introspect_session(session, catalog=catalog, tables=[(schema, t) for t in tables])
            if not {t.name.lower() for t in intro.facts.tables} <= {t.lower() for t in tables}:
                raise DraftError("table_outside_scope")
            profile = profile_session(session, intro.facts, catalog=catalog, schema=schema)
        finally:
            session.close()
        facts = infer_keys(intro.facts, profile)
        ctx = DraftContext(tenant_id="tenant-draft", domain="draft", contract_id=contract_id, owner_id="team.semantic-owners", owner_name="Semantic owners",
                           model_id=model_id, prompt_version=PROMPT_VERSION, dialect="databricks", physical_prefix=qualified_prefix(catalog, schema))
        probe = databricks_explain_probe(make)
        report, result = run_case_full(EvalCase(contract_id, facts, profile, secrets=secrets, canaries=canaries), capped, ctx, explain=probe)
        budget.check_time()
        done = tuple(sorted(reads))
        reasons = tuple(stop_reasons(report, remote=True))
        if reasons:
            return DraftRunResult("stopped", False, model_calls=budget.calls, tables_read=done, report=report, stop_reasons=reasons)
        if result is None or result.document is None:
            return DraftRunResult(f"no_draft:{report.error or 'nothing_kept'}", False, model_calls=budget.calls, tables_read=done, report=report)
        where = write_draft(result, out_dir, report=report, skipped_columns=intro.skipped,
                            source_note=f"Databricks catalog `{catalog}`, schema `{schema}`, tables {', '.join(tables)} (via an injected session)")
        return DraftRunResult("written", True, where, budget.calls, done, report)
    except DraftError as exc:
        return DraftRunResult(exc.code, False, model_calls=budget.calls, tables_read=tuple(sorted(reads)),
                              stop_reasons=("table_outside_scope",) if exc.code == "table_outside_scope" else ())


def main(argv: list[str] | None = None, *, session_factory: Callable[[], Any] | None = None, model: Any | None = None,
         clock: Callable[[], float] = time.monotonic) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--allow-live", action="store_true", help="required: the run reads a warehouse and calls a model, which needs the owner's approval")
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--tables", nargs="+", required=True, help="an explicit list, never 'all'")
    p.add_argument("--contract-id", default="auto-draft")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_DRAFTS_DIR)
    p.add_argument("--max-model-calls", type=int, default=MAX_MODEL_CALLS)
    p.add_argument("--max-reply-tokens", type=int, default=MAX_REPLY_TOKENS)
    p.add_argument("--wall-clock-seconds", type=float, default=DEFAULT_WALL_CLOCK_SECONDS)
    args = p.parse_args(argv)
    if not args.allow_live:
        print("refused: pass --allow-live (only after the owner approved the run)", file=sys.stderr)
        return 2
    if session_factory is None or model is None:
        print("Not run. This module takes the session factory and the model by injection; no live wiring is part of it.", file=sys.stderr)
        return 2
    try:
        caps = Caps(args.max_model_calls, args.max_reply_tokens, args.wall_clock_seconds)
    except ValueError:
        print("refused: a cap is out of range", file=sys.stderr)
        return 2
    outcome = run(allow_live=True, catalog=args.catalog, schema=args.schema, tables=args.tables, session_factory=session_factory, model=model,
                  out_dir=args.out_dir, contract_id=args.contract_id, model_id=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "model"), caps=caps, clock=clock)
    if outcome.ok:
        r = outcome.report
        print(f"Draft written to {outcome.path}: kept {r.kept_metrics} metrics and {r.kept_dimensions} dimensions, model calls {outcome.model_calls}, "
              f"tables read {len(outcome.tables_read)}.")
        return 0
    print(f"Stopped: {outcome.code}" + (f" ({', '.join(outcome.stop_reasons)})" if outcome.stop_reasons else "") + ". Nothing written.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
