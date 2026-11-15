"""Run the drafter evaluation with a real Azure OpenAI deployment (auto-ontology milestone 3). Started by the owner, not by CI.

    cd services/analytics-api
    AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=... AZURE_OPENAI_DEPLOYMENT=... \
        PYTHONPATH=.:../.. python3 -m app.ontology_draft.eval_run --runs 3 --out ../../docs/evaluation/auto-ontology-m3-report.md

Only the synthetic corpus in `eval_corpus.py` is sent to the model: invented schemas and invented values, with the redaction rule
applied. No database, warehouse or customer data is read. The report holds numbers only. Credentials are read from the environment,
never printed, never written to the report.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from app.llm_structured import OpenAIStructuredClient
from app.ontology_draft.drafter import PROMPT_VERSION
from app.ontology_draft.eval_corpus import build_cases
from app.ontology_draft.evaluation import render_markdown, run_suite
from app.ontology_draft.validate import DraftContext

REQUIRED = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3, help="runs per case (the model is not deterministic)")
    parser.add_argument("--out", type=Path, required=True, help="where to write the markdown report")
    args = parser.parse_args(argv)
    if not 1 <= args.runs <= 10:
        parser.error("--runs must be between 1 and 10")

    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        print("Not run. Set these environment variables (names only shown): " + ", ".join(missing), file=sys.stderr)
        return 2

    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
    model = OpenAIStructuredClient.azure(
        endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01"),
        deployment=deployment,
        timeout_seconds=120.0,
        reasoning_effort=os.environ.get("AZURE_OPENAI_REASONING_EFFORT") or None,
    )
    ctx = DraftContext(
        tenant_id="tenant-eval",
        domain="evaluation",
        contract_id="eval-draft",
        owner_id="team.eval",
        owner_name="Evaluation",
        model_id=deployment,
        prompt_version=PROMPT_VERSION,
    )
    reports = run_suite(build_cases(), model, ctx, runs=args.runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(reports, model_id=deployment, prompt_version=PROMPT_VERSION, runs=args.runs))
    errors = sum(1 for r in reports if r.error)
    print(f"Wrote {args.out}: {len(reports)} runs, {errors} with an error.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
