"""Command line for the DuckDB end-to-end runs (`reference_stack/e2e_duckdb.py`). Local only: nothing leaves this computer, no warehouse is contacted.

    python -m reference_stack.e2e_run retail --data DIR prompts OUT.json
    python -m reference_stack.e2e_run retail --data DIR score REPLIES.json [--report FILE] [--model-note TEXT]
    python -m reference_stack.e2e_run olist  --data CSV_DIR --work DIR prompts OUT.json
    python -m reference_stack.e2e_run olist  --data CSV_DIR --work DIR score REPLIES.json [--report FILE]
    python -m reference_stack.e2e_run retail --data DIR matrix REPLIES.json SYNONYMS.json --report FILE
    python -m reference_stack.e2e_run retail --data DIR record OUT.json     (a live model from the environment; sends prompts, never data)

`DIR` holds `online_retail_ii.parquet` (from `reference_stack.loaders.online_retail.convert`); `CSV_DIR` holds the Olist CSVs. A replies file maps a case id to
a model-shaped reply: a typed intent, or {"unsupported_request": true}."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb

from reference_stack import e2e_duckdb as e


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reference_stack.e2e_run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dataset", choices=("retail", "retail2", "olist", "synthetic"))
    p.add_argument("--data", required=True, type=Path)
    p.add_argument("--work", type=Path, help="olist: a directory for the prepared Parquet files")
    sub = p.add_subparsers(dest="action", required=True)
    pr = sub.add_parser("prompts")
    pr.add_argument("out", type=Path)
    sc = sub.add_parser("score")
    sc.add_argument("replies", type=Path)
    sc.add_argument("--report", type=Path)
    sc.add_argument("--model-note", default="a Claude agent standing in for the model, given the exact prompt the product builds and nothing else")
    rec = sub.add_parser("record", help="ask a live model (from the environment's settings) every question's product prompt and save its replies")
    rec.add_argument("out", type=Path)
    mx = sub.add_parser("matrix", help="the five setups side by side, from recorded model replies and recorded blind synonyms")
    mx.add_argument("replies", type=Path)
    mx.add_argument("synonyms", type=Path)
    mx.add_argument("--report", type=Path, required=True)
    mx.add_argument("--model-note", default="a Claude agent standing in for the model, given the exact prompt the product builds and nothing else")
    args = p.parse_args(argv)
    try:
        if args.dataset in ("retail", "retail2"):
            from reference_stack import e2e_online_retail as m

            dataset = m.build(args.data, 2 if args.dataset == "retail2" else 1)
        elif args.dataset == "synthetic":
            from reference_stack import e2e_synthetic as m

            if not args.work:
                print("refused: synthetic needs --work", file=sys.stderr)
                return 2
            dataset = m.build(args.data, args.work)
        else:
            from reference_stack import e2e_olist as m

            if not args.work:
                print("refused: olist needs --work", file=sys.stderr)
                return 2
            dataset = m.build(args.data, args.work)
        if args.action == "prompts":
            args.out.write_text(json.dumps(e.capture_prompts(dataset), indent=1))
            print(f"wrote {len(dataset.cases)} prompts")
            return 0
        if args.action == "record":
            from reference_stack import live_model

            client = live_model.build_client()
            replies = live_model.record_replies(client, e.capture_prompts(dataset))
            args.out.write_text(json.dumps(replies, indent=1))
            print(f"recorded {len(replies)} replies from {live_model.describe()}")
            return 0
        if args.action == "matrix":
            proposed = {k: v for k, v in e.load_json(args.synonyms).items() if k != "notes"}
            matrix = e.run_matrix(dataset, e.load_json(args.replies), proposed)
            args.report.write_text(e.render_matrix(dataset, matrix, model_note=args.model_note, internal_only=args.dataset == "olist"))
            print(f"wrote {args.report}")
            return 0
        results = e.score(dataset, e.load_json(args.replies))
        text = e.render_report(dataset, results, model_note=args.model_note)
        (args.report.write_text(text) if args.report else sys.stdout.write(text))
        bad = sum(r["status"] in ("wrong_answer", "wrongly_answered", "error") for r in results)
        return 1 if bad else 0
    except (OSError, ValueError, KeyError, duckdb.Error, RuntimeError) as exc:
        from reference_stack.live_model import LiveModelNotConfigured

        print(f"refused: {exc if isinstance(exc, LiveModelNotConfigured) else type(exc).__name__}", file=sys.stderr)  # a fixed message that names settings, never a value
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
