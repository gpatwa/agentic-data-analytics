"""`python -m app.ontology_draft.import_assets`: start a draft from a customer's existing Databricks metric view or Snowflake semantic view (E6).

Offline by construction: it reads files the caller already holds (a facts file, a metric view's YAML, the rows of `DESCRIBE SEMANTIC VIEW` as JSON,
an optional tags file), opens no connection and calls no model. The imported items pass the same validator as any draft. The result is a DRAFT
under the drafts directory with a "what we found" summary; a person reviews and certifies it, and nothing here is certified.

    python -m app.ontology_draft.import_assets --facts facts.json --contract-id c --tenant t --domain d \
        --metric-view orders_mv=orders_mv.yaml [--semantic-view sv=rows.json] [--tags tags.json] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.ontology_draft import existing_assets as ea
from app.ontology_draft.emit import DEFAULT_DRAFTS_DIR, write_draft
from app.ontology_draft.facts import SchemaFacts
from app.ontology_draft.proposal import ParsedProposal
from app.ontology_draft.validate import DraftContext, validate_and_emit


def _pair(value: str) -> tuple[str, Path]:
    name, _, path = value.partition("=")
    if not name or not path:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    return name, Path(path)


def _merge(results: list[ea.ImportResult]) -> ParsedProposal:
    metrics = tuple(m for r in results for m in r.proposal.metrics)
    dimensions = tuple(d for r in results for d in r.proposal.dimensions)
    return ParsedProposal((), metrics, dimensions, (), (), ())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app.ontology_draft.import_assets", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--facts", required=True, type=Path, help="SchemaFacts as JSON")
    p.add_argument("--contract-id", required=True)
    p.add_argument("--tenant", required=True)
    p.add_argument("--domain", required=True)
    p.add_argument("--owner-id", default="team.semantic-owners")
    p.add_argument("--owner-name", default="Semantic owners")
    p.add_argument("--metric-view", action="append", default=[], type=_pair, metavar="NAME=YAML")
    p.add_argument("--semantic-view", action="append", default=[], type=_pair, metavar="NAME=ROWS_JSON")
    p.add_argument("--tags", type=Path, help='JSON: [["table", "column", ["tag", ...]], ...]; a restricting tag only tightens')
    p.add_argument("--out", type=Path, default=DEFAULT_DRAFTS_DIR)
    args = p.parse_args(argv)
    if not (args.metric_view or args.semantic_view):
        print("refused: name at least one --metric-view or --semantic-view", file=sys.stderr)
        return 2
    try:
        facts = SchemaFacts.model_validate(json.loads(args.facts.read_text()))
        if args.tags:
            tags = {(t, c): tuple(v) for t, c, v in json.loads(args.tags.read_text())}
            facts = ea.apply_restrictions(facts, ea.restricted_columns(tags))
        results = [ea.from_metric_view_yaml(path.read_text(), facts, view_name=name) for name, path in args.metric_view]
        results += [ea.from_semantic_view_rows(json.loads(path.read_text()), facts, view_name=name) for name, path in args.semantic_view]
        ctx = DraftContext(tenant_id=args.tenant, domain=args.domain, contract_id=args.contract_id, owner_id=args.owner_id,
                           owner_name=args.owner_name, model_id="imported-from-existing-views", prompt_version="import-v1")
        result = validate_and_emit(_merge(results), facts, ctx)
        kept_m = len(result.document.contract.metrics) if result.document else 0
        kept_d = len(result.document.contract.dimensions) if result.document else 0
        sys.stdout.write(ea.render_found(facts, results, kept_metrics=kept_m, kept_dimensions=kept_d))
        if result.document is None:
            print("nothing was kept, so no draft was written", file=sys.stderr)
            return 1
        target = write_draft(result, args.out, source_note="Imported from the customer's existing views; every item passed the same validator as a model's proposal.")
    except (OSError, ValueError, ValidationError, KeyError) as exc:
        print(f"refused: {type(exc).__name__}", file=sys.stderr)  # the type only: these files may hold anything
        return 2
    print(f"draft written: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
