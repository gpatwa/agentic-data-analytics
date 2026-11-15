"""Auto-ontology drafter, milestones 1 and 2: contract, validators, introspection, profiling and redaction.

AI drafts, a person certifies, only certified definitions execute. This package emits `draft` documents and `candidate`
ontology nodes only. See docs/AUTO_ONTOLOGY_DESIGN.md. Introspection and profiling read only through an engine the caller passes
in (the read-only role); nothing here opens a connection by itself.
"""

from app.ontology_draft.drafter import (
    PROMPT_VERSION,
    DraftCache,
    InMemoryDraftCache,
    build_prompt,
    cache_key,
    draft_ontology,
    prompt_payload,
)
from app.ontology_draft.facts import (
    FactColumn,
    FactForeignKey,
    FactTable,
    SchemaFacts,
    is_sensitive_name,
    normalize_type,
)
from app.ontology_draft.introspect import Introspection, introspect_engine
from app.ontology_draft.profile import (
    ColumnProfile,
    ProfileLimits,
    SchemaProfile,
    TableProfile,
    profile_engine,
    redact_value,
    screen_proposal,
    select_samples,
)
from app.ontology_draft.proposal import DraftError, Drop
from app.ontology_draft.validate import CandidateJoin, DraftContext, DraftResult, ExplainProbe

__all__ = [
    "CandidateJoin",
    "ColumnProfile",
    "DraftCache",
    "InMemoryDraftCache",
    "Introspection",
    "ProfileLimits",
    "SchemaProfile",
    "TableProfile",
    "cache_key",
    "introspect_engine",
    "profile_engine",
    "redact_value",
    "screen_proposal",
    "select_samples",
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
