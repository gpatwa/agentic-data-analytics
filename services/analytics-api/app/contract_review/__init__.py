"""What does a contract change do? (E4 of `docs/design/SEMANTIC_TRUST_FOUNDATIONS_PLAN.md`.) Offline, deterministic, no model."""

from app.contract_review.behaviour import (
           CaseChange,
           EvalCase,
           EvalFile,
           Expect,
           Oracle,
           Verdict,
           behaviour_diff,
           verdict_for,
)
from app.contract_review.diff import Change, ContractDiff, diff_contracts
from app.contract_review.oracle import OracleResult, render_oracle_report, run_oracle_checks
from app.contract_review.summary import ReviewSummary, render_summary, review

__all__ = ["CaseChange", "Change", "ContractDiff", "EvalCase", "EvalFile", "Expect", "Oracle", "OracleResult", "ReviewSummary", "Verdict", "behaviour_diff",
           "diff_contracts", "render_oracle_report", "render_summary", "review", "run_oracle_checks", "verdict_for"]
