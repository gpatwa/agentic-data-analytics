"""The pinned generator report: one generated page per configuration, kept equal to its generator by a test. `python -m reference_stack.synthetic.report`."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from reference_stack.synthetic import truth as T
from reference_stack.synthetic.config import DEFECT_TEXT, DEFECTS, EVIDENCE, GENERATOR_VERSION, GeneratorConfig
from reference_stack.synthetic.food_delivery import generate

REPORT = Path(__file__).resolve().parents[3].parent / "docs/evaluation/synthetic-food-delivery-report.md"
RATE_OF = {"D3": "late_version", "D4": "duplicate", "D7": "delivery_sentinel", "D8": "spelling_drift", "D9": "test_order", "D10": "orphan"}


def render_report(cfg: GeneratorConfig | None = None) -> str:
    cfg = cfg or GeneratorConfig(tier="S")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        manifest = generate(cfg, out).body
        truth = json.loads((out / "truth.json").read_text())
        again = T.recompute(out)
        observed = T.observed_defects(out)
    bad = T.disagreements(truth, again)
    lines = ["# Synthetic food-delivery data: generator report", "",
             "SYNTHETIC DATA. Generated, not real, not customer data and not data from any named company. Regenerate this page with `python -m reference_stack.synthetic.report`.",
             "", f"- Generator `{GENERATOR_VERSION}`, tier `{cfg.tier}` ({cfg.n_orders:,} orders), seed `{cfg.seed}`, start {cfg.start_date}, {cfg.days} days",
             f"- Config digest `{manifest['config_digest'][:16]}`, logical digest `{manifest['logical_digest'][:16]}`",
             "- The rates are plausible guesses by the authors, not measured from any real platform.", "", "## Tables", "", "| Table | Rows |", "|---|---|"]
    lines += [f"| `{name}` | {info['rows']:,} |" for name, info in manifest["tables"].items()]
    lines += ["", "## Defects, by how well their real analogue is evidenced", "",
              "| ID | Evidence | Defect | Rate parameter | Visible in the output |", "|---|---|---|---|---|"]
    for d in sorted(DEFECTS, key=lambda d: (["reviewed", "partly_reviewed", "general"].index(EVIDENCE[d]), int(d[1:]))):
        rate = getattr(cfg.rates, RATE_OF[d]) if d in RATE_OF else "structural"
        lines.append(f"| {d} | {EVIDENCE[d].replace('_', ' ')} | {DEFECT_TEXT[d]} | {rate} | {observed[d]:,} |")
    lines += ["", "## Two answer keys", "",
              f"The generator's ledger (from the clean order table, in integer cents) and a DuckDB recomputation from the Parquet alone: **{len(bad)} disagreement(s)** over {len(truth['values'])} values and {len(truth['maps'])} breakdowns.",
              "", "| Definition | Unit | Value | What it says |", "|---|---|---|---|"]
    lines += [f"| `{k}` | {T.DEFINITIONS[k][0]} | {v:,} | {T.DEFINITIONS[k][1]} |" for k, v in sorted(truth["values"].items())]
    hidden = truth["hidden"]["tip_true_all_methods_usd_cents"]
    lines += ["", f"Hidden from the data by D2: tips really paid in USD on every payment method come to {hidden:,} cents, against {truth['values']['tip_card_usd_cents']:,} cents recorded on card orders alone.",
              "", "Neither key uses the product. A certified definition must choose among these (for example whether revenue includes reversals); the naive values show what an unexamined certification would report."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    REPORT.write_text(render_report())
    print(f"wrote {REPORT}")
