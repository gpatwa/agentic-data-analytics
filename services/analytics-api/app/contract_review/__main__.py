"""`python -m app.contract_review --old <contract.json> --new <contract.json> [--old-eval f] [--new-eval f]`: the page a certifier reads before signing.

It prints the definition diff, the answer changes on recorded intents (no model is called) and any blockers, and exits 1 when a blocker is
present. It reads two files and writes nothing. It does not certify: certification is a person's decision, and a clean page is not a certification."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.contract_review.behaviour import EvalFile
from app.contract_review.summary import render_summary, review
from packages.platform_contracts.semantic import SemanticRegistryDocument


def _document(path: Path) -> SemanticRegistryDocument:
    return SemanticRegistryDocument.model_validate(json.loads(path.read_text()))


def _eval(path: Path | None) -> EvalFile | None:
    return EvalFile.model_validate(json.loads(path.read_text())) if path else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app.contract_review", description=__doc__)
    p.add_argument("--old", required=True, type=Path)
    p.add_argument("--new", required=True, type=Path)
    p.add_argument("--old-eval", type=Path)
    p.add_argument("--new-eval", type=Path)
    args = p.parse_args(argv)
    try:
        summary = review(_document(args.old), _document(args.new), old_eval=_eval(args.old_eval), new_eval=_eval(args.new_eval))
    except (OSError, ValueError, ValidationError) as exc:
        print(f"refused: {type(exc).__name__}", file=sys.stderr)  # the type only: a contract file may hold anything
        return 2
    sys.stdout.write(render_summary(summary))
    return 1 if summary.blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
