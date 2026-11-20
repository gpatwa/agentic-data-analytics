"""The page a certifier reads before signing a new version: definition changes, answer changes, and what blocks."""

from __future__ import annotations

from dataclasses import dataclass

from app.contract_review.behaviour import CaseChange, EvalFile, behaviour_diff
from app.contract_review.diff import ContractDiff, diff_contracts, render_diff
from packages.platform_contracts.semantic import SemanticRegistryDocument


@dataclass(frozen=True)
class ReviewSummary:
    diff: ContractDiff
    changes: tuple[CaseChange, ...]
    blockers: tuple[str, ...]  # a new version cannot be certified while any is present
    notes: tuple[str, ...]


def review(old: SemanticRegistryDocument, new: SemanticRegistryDocument, *, old_eval: EvalFile | None, new_eval: EvalFile | None) -> ReviewSummary:
    """Blockers: a prior version that had an eval file requires one for the new version, and a case that met its expectation before must still
    meet it. A change that is not a regression is shown, not blocked: the certifier decides."""
    blockers: list[str] = []
    notes: list[str] = []
    cases = new_eval.cases if new_eval else ()
    if old_eval is not None and new_eval is None:
        blockers.append("eval_file_missing_for_new_version")
    if new_eval is not None and new_eval.contract_id != new.contract.id:
        blockers.append("eval_file_is_for_another_contract")
        cases = ()
    changes = tuple(behaviour_diff(old, new, cases)) if cases else ()
    blockers += [f"regression:{c.case_id}" for c in changes if c.expectation_met_old and not c.expectation_met_new]
    notes += [f"expectation_not_met_before_or_after:{c.case_id}" for c in changes if not c.expectation_met_old and not c.expectation_met_new]
    if new_eval is None and old_eval is None:
        notes.append("no_eval_file: answer changes were not checked")
    return ReviewSummary(diff_contracts(old.contract, new.contract), changes, tuple(blockers), tuple(notes))


def render_summary(s: ReviewSummary) -> str:
    lines = ["# Review of a new contract version", "", render_diff(s.diff).rstrip(), "", "## Answer changes (recorded intents, no model called)", ""]
    moved = [c for c in s.changes if c.changed]
    lines += [f"- `{c.case_id}` \"{c.question}\": {c.old.outcome}{':' + c.old.code if c.old.code else ''} -> {c.new.outcome}{':' + c.new.code if c.new.code else ''}"
              + (" (same outcome, the compiled statement changed)" if c.old.outcome == c.new.outcome == "answered" else "") for c in moved] or ["- none"]
    lines += ["", f"{len(s.changes)} case(s) checked, {len(moved)} changed.", "", "## Blockers", ""]
    lines += [f"- {b}" for b in s.blockers] or ["- none"]
    if s.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in s.notes]
    lines += ["", "This does not call a model and does not judge whether a definition is right for the business; certification is a person's decision."]
    return "\n".join(lines) + "\n"


__all__ = ["ReviewSummary", "render_summary", "review"]
