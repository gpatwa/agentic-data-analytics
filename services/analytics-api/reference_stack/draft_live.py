"""The live wiring for `app.ontology_draft.databricks_draft_run`: the owner's own `databricks` profile as the read-only session and an Azure OpenAI deployment as the model.

    AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=... AZURE_OPENAI_DEPLOYMENT=... AZURE_OPENAI_API_VERSION=... AZURE_OPENAI_REASONING_EFFORT=... \
        python3 -m reference_stack.draft_live --allow-live --profile P --warehouse-name W --catalog C --schema S --tables T [--max-model-calls 1] ...

The key and endpoint come ONLY from the environment of the process: nothing here reads a file, prints a value or accepts a secret as an argument. The key is handed
to the driver as a `secret` so its leak scan refuses a run whose prompt or output would contain it. Statements go through the driver's guard (listed tables only)
and a statement and wall-clock cap. Nothing is written except a draft under the drafts directory."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from app.llm_structured import OpenAIStructuredClient
from app.ontology_draft.databricks_draft_run import Caps, run
from app.ontology_draft.emit import DEFAULT_DRAFTS_DIR
from reference_stack.loaders.databricks_cli import DatabricksCliSession, find_warehouse_id

REQUIRED = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")


class _Stream:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchmany(self, n: int) -> list[tuple[Any, ...]]:
        out, self._rows = self._rows[:n], self._rows[n:]
        return out

    def close(self) -> None:
        self._rows = []


class CliReadSession:
    """What the drafter's sources expect (`execute(sql, params)` returning a stream), over the owner's CLI session."""

    def __init__(self, session: DatabricksCliSession) -> None:
        self._s = session

    def execute(self, sql: str, params: dict | None = None) -> _Stream:  # noqa: ARG002
        return _Stream(self._s.query(sql))

    def prepare(self, timeout_seconds: float) -> None:  # noqa: ARG002
        """The sources ask a session to set a per-statement timeout. Here the bound is the driver's wall clock and the CLI session's own cap."""
        return None

    def close(self) -> None:
        return None


def model_from_environment() -> tuple[OpenAIStructuredClient, str]:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        raise SystemExit("refused: set " + ", ".join(missing) + " in the environment of this process")
    client = OpenAIStructuredClient.azure(
        endpoint=os.environ["AZURE_OPENAI_ENDPOINT"], api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"), deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        timeout_seconds=120.0, reasoning_effort=os.environ.get("AZURE_OPENAI_REASONING_EFFORT") or None)
    return client, os.environ["AZURE_OPENAI_API_KEY"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--allow-live", action="store_true", help="required: reads a warehouse and calls a model")
    p.add_argument("--profile", required=True)
    p.add_argument("--warehouse-name", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--tables", nargs="+", required=True)
    p.add_argument("--contract-id", default="auto-draft")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_DRAFTS_DIR)
    p.add_argument("--max-model-calls", type=int, default=1)
    p.add_argument("--max-reply-tokens", type=int, default=4_000)
    p.add_argument("--wall-clock-seconds", type=float, default=300.0)
    p.add_argument("--max-statements", type=int, default=40)
    args = p.parse_args(argv)
    if not args.allow_live:
        print("refused: pass --allow-live (only after the owner approved the run)", file=sys.stderr)
        return 2
    model, key = model_from_environment()
    warehouse = find_warehouse_id(args.profile, args.warehouse_name)
    session = DatabricksCliSession(args.profile, warehouse, args.wall_clock_seconds, max_statements=args.max_statements)
    outcome = run(allow_live=True, catalog=args.catalog, schema=args.schema, tables=args.tables, session_factory=lambda: CliReadSession(session), model=model,
                  out_dir=args.out_dir, contract_id=args.contract_id, model_id=os.environ["AZURE_OPENAI_DEPLOYMENT"],
                  caps=Caps(args.max_model_calls, args.max_reply_tokens, args.wall_clock_seconds), secrets=(key,))
    print(f"statements={session.statements} model_calls={outcome.model_calls} tables_read={len(outcome.tables_read)} code={outcome.code}")
    if outcome.ok:
        r = outcome.report
        print(f"Draft written to {outcome.path}: kept {r.kept_metrics} metrics and {r.kept_dimensions} dimensions.")
        return 0
    print("Stopped" + (f" ({', '.join(outcome.stop_reasons)})" if outcome.stop_reasons else "") + ". Nothing written.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
