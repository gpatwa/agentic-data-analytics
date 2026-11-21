"""`python -m app.ontology_draft.import_assets`: start a draft from a customer's existing Databricks metric view or Snowflake semantic view (E6).

Offline by construction: it reads files the caller already holds (a facts file, a metric view's YAML, the rows of `DESCRIBE SEMANTIC VIEW` as JSON,
an optional tags file), opens no connection and calls no model. The imported items pass the same validator as any draft. The result is a DRAFT
under the drafts directory with a "what we found" summary; a person reviews and certifies it, and nothing here is certified.

    python -m app.ontology_draft.import_assets --facts facts.json --contract-id c --tenant t --domain d \
        --metric-view orders_mv=orders_mv.yaml [--semantic-view sv=rows.json] [--tags tags.json] [--out DIR]

Reading the customer's metric views and tags from their workspace instead of from saved files (L4) is separate and opt-in. NAMED objects only, never a
listing or a wildcard, catalog metadata only. Without `--allow-live` the command PRINTS the statements it would run and runs none:

    ... --read-metric-view orders_mv=cat.sch.orders_mv --read-tags cat.sch.orders [--allow-live --session-factory pkg.mod:build]

`build()` returns a session (see `databricks_assets.py`); this repository opens no connection itself. The statements are not verified against a
workspace. A failed tag read refuses the whole run (exit 1): a missing restriction would loosen the draft.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.ontology_draft import databricks_assets as da
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


def _load_session_factory(spec: str) -> Callable[[], Any]:
    module, _, name = spec.partition(":")
    if not module or not name:
        raise ValueError("factory_must_be_module_colon_name")
    return getattr(importlib.import_module(module), name)


class _Refused(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _live_read(args: argparse.Namespace, session_factory: Callable[[], Any] | None) -> tuple[list[tuple[str, str]], list[tuple[str, str | None, str, str]]] | None:
    """Print the statements; with `--allow-live` run them through the injected session. Returns None when it only printed. Raises `_Refused`."""
    try:
        statements = da.planned_statements([str(fqn) for _, fqn in args.read_metric_view], args.read_tags)
    except Exception as exc:  # noqa: BLE001 - DraftError carries a fixed code; anything else is named by type
        raise _Refused(2, f"refused: {getattr(exc, 'code', type(exc).__name__)}") from None
    print("Statements (catalog metadata only, named objects only):")
    for statement in statements:
        print(f"  {statement}")
    if not args.allow_live:
        print("Nothing was run: add --allow-live to run them.")
        return None
    try:
        factory = session_factory or (_load_session_factory(args.session_factory) if args.session_factory else None)
        if factory is None:
            raise _Refused(2, "refused: no session factory; this command opens no connection of its own")
        session = factory()
    except (OSError, ValueError, ImportError, AttributeError, TypeError) as exc:
        raise _Refused(2, f"refused: {type(exc).__name__}") from None
    yamls: list[tuple[str, str]] = []
    tags: list[tuple[str, str | None, str, str]] = []
    try:
        for name, fqn in args.read_metric_view:
            text = da.read_metric_view_yaml(session, str(fqn))
            if text is None:
                print(f"could not read metric view {name}; it is skipped", file=sys.stderr)
            else:
                yamls.append((name, text))
        for fqn in args.read_tags:
            got = da.read_tag_rows(session, fqn)
            if got is None:
                raise _Refused(1, "could not read tags; refusing, because a missing restriction would loosen the draft")
            tags += got
    finally:
        close = getattr(session, "close", None)
        if close:
            close()
    return yamls, tags


def main(argv: list[str] | None = None, *, session_factory: Callable[[], Any] | None = None) -> int:
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
    p.add_argument("--read-metric-view", action="append", default=[], type=_pair, metavar="NAME=CATALOG.SCHEMA.VIEW",
                   help="L4: a named metric view to read from the workspace (runs only with --allow-live)")
    p.add_argument("--read-tags", action="append", default=[], metavar="CATALOG.SCHEMA.TABLE", help="L4: a named table whose Unity Catalog tags to read")
    p.add_argument("--allow-live", action="store_true", help="run the printed statements through a session; without it nothing is run")
    p.add_argument("--session-factory", help="module:callable returning a session; the deployment owns the connection")
    p.add_argument("--out", type=Path, default=DEFAULT_DRAFTS_DIR)
    args = p.parse_args(argv)
    if not (args.metric_view or args.semantic_view or args.read_metric_view):
        print("refused: name at least one --metric-view, --semantic-view or --read-metric-view", file=sys.stderr)
        return 2
    live_yaml: list[tuple[str, str]] = []
    live_tags: list[tuple[str, str | None, str, str]] = []
    if args.read_metric_view or args.read_tags:
        try:
            got = _live_read(args, session_factory)
        except _Refused as refusal:
            print(refusal.message, file=sys.stderr)
            return refusal.code
        if got is None:
            return 0
        live_yaml, live_tags = got
    try:
        facts = SchemaFacts.model_validate(json.loads(args.facts.read_text()))
        if args.tags:
            tags = {(t, c): tuple(v) for t, c, v in json.loads(args.tags.read_text())}
            facts = ea.apply_restrictions(facts, ea.restricted_columns(tags))
        if live_tags:
            facts = ea.apply_restrictions(facts, ea.restricted_columns(da.tags_by_column(facts, live_tags)))
        results = [ea.from_metric_view_yaml(path.read_text(), facts, view_name=name) for name, path in args.metric_view]
        results += [ea.from_metric_view_yaml(text, facts, view_name=name) for name, text in live_yaml]
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
