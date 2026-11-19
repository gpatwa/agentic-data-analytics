"""Business words for certified semantic objects: the one normaliser, and the rules a certified synonym must pass.

The contract validator, the intent-echo check and the drafter all import this module, so what was certified and what the runtime
matches cannot drift apart. Matching is lexical and conservative: lower case, alphanumeric tokens, a light plural fold.
"""
from __future__ import annotations

import re

MAX_SYNONYMS = 10
MAX_PROPOSED_SYNONYMS = 5  # what the drafter may propose per metric or dimension
SYNONYM_SHAPE = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9][A-Za-z0-9 \-]{0,58}[A-Za-z0-9]$")
TIME_WORDS = frozenset(
    "time date day days daily week weeks weekly month months monthly quarter quarters quarterly year years yearly annual annually hour hourly".split()
)
GROUPING_WORDS = frozenset("by per across each".split())  # the echo check reads these as the start of a grouping phrase


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def stem(token: str) -> str:
    """A light plural fold: statuses -> status, categories -> category, orders -> order; status and process stay as they are."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ses", "xes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def normalize_term(text: str) -> str:
    """The comparison form of a business term, an id (underscores and dots read as spaces) or a question fragment."""
    return " ".join(stem(t) for t in tokens(text))


def contains_phrase(outer: str, inner: str) -> bool:
    """`inner` appears in `outer` as whole words (both already normalised). Equal phrases contain each other."""
    return bool(inner) and f" {inner} " in f" {outer} "


def synonym_problem(text: str) -> str | None:
    """Why one synonym cannot be certified on its own, or None. The codes name rules, never the text."""
    if not isinstance(text, str) or not SYNONYM_SHAPE.match(text):
        return "synonym_shape"
    words = set(tokens(text))
    if words & GROUPING_WORDS or "for each" in " ".join(tokens(text)):
        return "synonym_grouping_word"
    if len(words) == 1 and words <= TIME_WORDS:
        return "synonym_time_word"
    return None
