"""Auto-ontology drafter, milestone 1: contract and validators on fixtures with a fake model.

AI drafts, a person certifies, only certified definitions execute. This package emits `draft` documents and `candidate`
ontology nodes only. See docs/AUTO_ONTOLOGY_DESIGN.md. It does not connect to any database.
"""

from app.ontology_draft.drafter import PROMPT_VERSION, build_prompt, draft_ontology, prompt_payload
from app.ontology_draft.facts import (
    FactColumn,
    FactForeignKey,
    FactTable,
    SchemaFacts,
    is_sensitive_name,
    normalize_type,
)
from app.ontology_draft.proposal import DraftError, Drop
from app.ontology_draft.validate import CandidateJoin, DraftContext, DraftResult, ExplainProbe

__all__ = [
    "CandidateJoin",
    "DraftContext",
    "DraftError",
    "DraftResult",
    "Drop",
    "ExplainProbe",
    "FactColumn",
    "FactForeignKey",
    "FactTable",
    "PROMPT_VERSION",
    "SchemaFacts",
    "build_prompt",
    "draft_ontology",
    "is_sensitive_name",
    "normalize_type",
    "prompt_payload",
]
