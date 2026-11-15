"""Draft an ontology from a real PostgreSQL with the real model (auto-ontology milestone 4a). Started by the owner or a task they set.

    cd services/analytics-api
    ONTOLOGY_PG_READONLY_URL=postgresql+psycopg2://... \
    AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=... AZURE_OPENAI_DEPLOYMENT=... \
        python3 -m app.ontology_draft.draft_run --schema public --tables demo_sales_orders

It introspects, profiles (redaction rule applied), drafts, dry-runs every candidate with EXPLAIN, scores the draft, and writes plain
files to the drafts directory for human review. It uses a read-only role's engine and never writes to the database.

The database host must be local (127.0.0.1, ::1 or localhost) unless `--allow-remote` is passed. That flag is the separate
approval milestone 4b needs (docs/AUTO_ONTOLOGY_M4_PLAN.md): do not pass it without that approval. Credentials and the database URL
come from the environment and are never printed or written.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import sqlalchemy as sa

from app.llm_structured import OpenAIStructuredClient
from app.ontology_draft.drafter import PROMPT_VERSION
from app.ontology_draft.emit import DEFAULT_DRAFTS_DIR, write_draft
from app.ontology_draft.eval_corpus import REFERENCE_PATH
from app.ontology_draft.evaluation import EvalCase, run_case_full
from app.ontology_draft.explain import explain_probe
from app.ontology_draft.introspect import introspect_engine
from app.ontology_draft.profile import profile_engine
from app.ontology_draft.proposal import DraftError
from app.ontology_draft.validate import DraftContext
from packages.platform_contracts.semantic import SemanticRegistryDocument

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
REQUIRED = ("ONTOLOGY_PG_READONLY_URL", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schema", default="public")
    parser.add_argument("--tables", nargs="+", required=True, help="the tables to draft from (an explicit list, never 'all')")
    parser.add_argument("--contract-id", default="auto-draft")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DRAFTS_DIR)
    parser.add_argument("--allow-remote", action="store_true", help="milestone 4b only; needs the owner's separate approval")
    args = parser.parse_args(argv)

    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        print("Not run. Set these environment variables (names only shown): " + ", ".join(missing), file=sys.stderr)
        return 2
    url = sa.engine.make_url(os.environ["ONTOLOGY_PG_READONLY_URL"])
    if url.host not in LOCAL_HOSTS and not args.allow_remote:
        print("Not run. The database host is not local; that is milestone 4b and needs --allow-remote and the owner's approval.", file=sys.stderr)
        return 2

    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
    engine = sa.create_engine(url)
    try:
        introspection = introspect_engine(engine, schema=args.schema, tables=args.tables)
        facts = introspection.facts
        profile = profile_engine(engine, facts, schema=args.schema)
        reference = None
        if [t.name for t in facts.tables] == ["demo_sales_orders"]:  # the one table with a certified reference
            reference = SemanticRegistryDocument.model_validate_json(REFERENCE_PATH.read_text()).contract
        case = EvalCase(args.contract_id, facts, profile, reference=reference)
        model = OpenAIStructuredClient.azure(
            endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01"),
            deployment=deployment,
            timeout_seconds=120.0,
            reasoning_effort=os.environ.get("AZURE_OPENAI_REASONING_EFFORT") or None,
        )
        ctx = DraftContext(
            tenant_id="tenant-draft",
            domain="draft",
            contract_id=args.contract_id,
            owner_id="team.semantic-owners",
            owner_name="Semantic owners",
            model_id=deployment,
            prompt_version=PROMPT_VERSION,
        )
        probe = explain_probe(engine, allowed_tables=[t.name for t in facts.tables])
        report, result = run_case_full(case, model, ctx, explain=probe)
    except DraftError as exc:
        print(f"Stopped: {exc.code}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    if result is None or result.document is None:
        print(f"No draft written. error={report.error} drops={report.drops}", file=sys.stderr)
        return 1
    where = write_draft(
        result,
        args.out_dir,
        report=report,
        skipped_columns=introspection.skipped,
        source_note=f"PostgreSQL schema `{args.schema}`, tables {', '.join(args.tables)} (host {'local' if url.host in LOCAL_HOSTS else 'remote'})",
    )
    print(
        f"Draft written to {where}: kept {report.kept_metrics} metrics and {report.kept_dimensions} dimensions, "
        f"drops {report.drops or 'none'}, escapes {len(report.escapes)}, leaks {report.leaks}, injections {report.injections}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
