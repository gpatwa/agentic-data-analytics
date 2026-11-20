"""Write a draft as plain files for the semantic owners to review in Git (auto-ontology milestone 4a).

The files go to a drafts directory that is NOT the semantic registry root, so the registry never loads them. A person reviews them,
edits, and certifies by moving a contract into the registry as `certified` in a separate change. Nothing here certifies anything.

The review note holds counts, ids, codes and fingerprints. It holds no sample values, no prompts and no credentials.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from app.ontology_draft.evaluation import RunReport
from app.ontology_draft.validate import DraftResult

KEY_EVIDENCE_NOTES = {
    "declared": "a constraint recorded in the warehouse catalog. Primary keys on Snowflake and Databricks are not enforced, so this is a claim; confirm the grain is one row per key.",
    "profiled_full": "unique and never null across the whole table when it was profiled. A fact about today's rows, not a constraint; confirm the grain.",
    "none": "no key, so no metric can be drafted on this table.",
}
DEFAULT_DRAFTS_DIR = Path(__file__).resolve().parents[2] / "semantic_registry_drafts"


def write_draft(
    result: DraftResult,
    out_dir: Path,
    *,
    report: RunReport | None = None,
    skipped_columns: tuple[str, ...] = (),
    source_note: str = "",
) -> Path:
    """Write the draft contract, the candidate ontology and a review note under `out_dir/<contract id>/<version>/`."""
    if result.document is None:
        raise ValueError("nothing to write: the draft kept no metric or dimension")
    contract = result.document.contract
    target = out_dir / contract.id / contract.version
    target.mkdir(parents=True, exist_ok=True)
    (target / "contract.draft.json").write_text(result.document.model_dump_json(indent=2) + "\n")
    if result.ontology is not None:
        (target / "ontology.candidate.json").write_text(result.ontology.model_dump_json(indent=2) + "\n")
    (target / "REVIEW.md").write_text(_review_note(result, report, skipped_columns, source_note))
    return target


def _review_note(result: DraftResult, report: RunReport | None, skipped: tuple[str, ...], source_note: str) -> str:
    contract = result.document.contract  # type: ignore[union-attr]
    provenance = result.provenance
    drops = Counter(d.code for d in result.dropped)
    lines = [
        f"# Draft review: {contract.id} `{contract.version}`",
        "",
        "**Status: DRAFT. Not certified. It cannot execute**: the registry refuses a draft, and this directory is outside the",
        "registry root. A person must review, edit and certify it in Git.",
        "",
        f"- Source: {source_note or 'not stated'}",
        f"- Schema fingerprint: `{provenance['schema_fingerprint']}`",
        f"- Profile fingerprint (aggregate stats only): `{provenance.get('profile_fingerprint')}`",
        f"- Model: `{provenance['model_id']}`, prompt `{provenance['prompt_version']}`, drafter `{provenance['drafter_version']}`",
        "",
        "## Candidates kept (all `candidate`, none certified)",
        "",
    ]
    lines += [f"- metric `{m.id}`: {m.aggregation} of `{m.measure_field_id or 'rows'}` on `{m.dataset_id}`" for m in contract.metrics]
    lines += [f"- dimension `{d.id}`: `{d.field_id}` ({d.dimension_type})" for d in contract.dimensions]
    lines += ["", "## The key behind each table's metrics (what it proves)", ""]
    lines += [f"- `{table}`: key ({', '.join(info['columns']) or 'none'}), evidence **{info['evidence']}**: {KEY_EVIDENCE_NOTES[info['evidence']]}"
              for table, info in sorted(provenance.get("keys", {}).items())] or ["- no tables in use"]
    lines += ["", "## Terms you would be certifying (names and synonyms the model proposed)", "",
              "Certifying this contract certifies these words: a question that uses one of them will be matched to the metric or dimension. Remove any you would not want a colleague to use for it.", ""]
    worded = [(m.id, m.display_name, m.synonyms) for m in contract.metrics] + [(d.id, d.display_name, d.synonyms) for d in contract.dimensions]
    lines += [f"- `{i}`: " + (f"display name \"{name}\"; " if name else "") + ("synonyms " + ", ".join(f"\"{w}\"" for w in syn) if syn else "no synonyms") for i, name, syn in worded if name or syn] or ["- none proposed or kept"]
    lines += ["", "## Dropped, with the reason", ""]
    lines += [f"- {code}: {count}" for code, count in sorted(drops.items())] or ["- none"]
    if result.dropped:
        lines += ["", "Items: " + ", ".join(f"`{d.item}` ({d.code})" for d in result.dropped)]
    lines += ["", "## Drafted joins (never executable: the compiler has no join support)", ""]
    lines += [
        f"- `{j.from_table}`({', '.join(j.from_columns)}) -> `{j.to_table}`({', '.join(j.to_columns)}), {j.cardinality}, evidence: {j.evidence}"
        for j in result.candidate_joins
    ] or ["- none"]
    lines += ["", "## Columns skipped at introspection (type not recognised)", ""]
    lines += [f"- `{name}`" for name in skipped] or ["- none"]
    if report is not None:
        lines += [
            "",
            "## Checks run on this draft",
            "",
            f"- validator escapes: {', '.join(report.escapes) or 0}",
            f"- leaked secrets: {report.leaks}; injected canaries: {report.injections}",
        ]
        if report.metric_recall is not None:
            lines.append(f"- recall against the certified reference: metrics {report.metric_recall:.2f}, dimensions {report.dimension_recall:.2f}")
    lines += [
        "",
        "## To certify",
        "",
        "1. Read `contract.draft.json` and the descriptions on the nodes in `ontology.candidate.json`.",
        "2. Edit anything wrong. Check each metric's grain and measure against the business definition, and read every proposed term above.",
        "3. In a separate change, move the contract into the registry with `lifecycle: certified` and the metrics `certified`.",
        "   Certification is the semantic owners' decision, recorded in the program file; this draft is not one.",
        "",
    ]
    return "\n".join(lines)


__all__ = ["DEFAULT_DRAFTS_DIR", "write_draft"]
