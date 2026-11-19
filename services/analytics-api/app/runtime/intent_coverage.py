"""A deterministic check that the interpreted request uses everything the question says (K1 of `docs/design/INTENT_COVERAGE_CHECK_PLAN.md`).

The intent-echo check asks whether the chosen metric is named in the question. It never asks the other direction: once a certified
synonym such as "how many orders" exists, a question that adds a constraint ("...were refunded", "...last year") or names a second
metric passes, and the answer silently drops what the person asked. This check accounts for every content word of the question:

- a word is **accounted** when the intent explains it: the matched phrase of a chosen metric, a grouped dimension or a filter's field,
  a filter's values, time words (only when the intent has a time range or a time grouping), sort and "top N" words (only when the intent
  has a sort or a limit it changed);
- a word is **neutral** when it is a generic function word from a small, reviewed, versioned list (never a domain word: those belong in a
  contract's certified synonyms);
- anything else is **unaccounted**, and the run is refused.

It can only make the pipeline stricter. It reports positions, never words: evidence and logs get a count and a hash, and the requester's
own response is rebuilt from their own question and those positions. Lexical, English only, conservative: it will refuse some good questions.
"""

from __future__ import annotations

import hashlib
import re

from app.runtime.intent_echo import _terms
from packages.platform_contracts.analytics_intent import AnalyticalIntent
from packages.platform_contracts.ontology import OntologySnapshot
from packages.platform_contracts.semantic_terms import GROUPING_WORDS, TIME_WORDS, normalize_term, stem, tokens

NEUTRAL_VERSION = "neutral-v1"
DEFAULT_LIMIT = 100
NEUTRAL_WORDS = frozenset(
    """a an the is are was were be been being am there here what which who whom how many much show me give tell us please can could would
    should will you i we our my your do does did done have has had having of in on at for to and or that this these those it its any all
    every some get got see list display find provide need want like also just only overall s t re ve ll d m""".split()
)
NEUTRAL_PHRASES = ("in total",)
TIME_MODIFIERS = frozenset(
    """last this next previous past current ago since until from between during within before after yesterday today tomorrow
    january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec
    monday tuesday wednesday thursday friday saturday sunday q1 q2 q3 q4 ytd mtd""".split()
)
FILTER_WORDS = frozenset("with without where whose equal equals not no greater less than above below over under more fewer at least most".split())
SORT_WORDS = frozenset("top bottom highest lowest largest smallest biggest most least best worst first rank ranked sorted sort order".split())
_NEUTRAL_STEMS = frozenset(stem(w) for w in NEUTRAL_WORDS)
_TIME_STEMS = frozenset(stem(w) for w in TIME_WORDS)
_TIME_MOD_STEMS = frozenset(stem(w) for w in TIME_MODIFIERS)
_SORT_STEMS = frozenset(stem(w) for w in SORT_WORDS)
_GROUP_STEMS = frozenset(stem(w) for w in GROUPING_WORDS)
_FILTER_STEMS = frozenset(stem(w) for w in FILTER_WORDS)


def unaccounted_positions(question: str | None, intent: AnalyticalIntent, ontology: OntologySnapshot) -> list[int]:
    """Positions (in `tokens(question)`) of content words the intent does not account for. Empty means the question is fully covered."""
    toks = tokens(question or "")
    stems = [stem(t) for t in toks]
    terms = _terms(ontology)
    covered: set[int] = set()

    def cover(phrase: str) -> None:
        words = phrase.split()
        for i in range(len(stems) - len(words) + 1):
            if words and stems[i : i + len(words)] == words:
                covered.update(range(i, i + len(words)))

    def cover_stems(wanted: frozenset[str]) -> None:
        covered.update(i for i, s in enumerate(stems) if s in wanted)

    for metric in intent.metrics:
        for phrase in terms.get(metric.metric_id, {normalize_term(metric.metric_id)}):
            cover(phrase)
    for group in intent.group_by:
        for phrase in terms.get(group.dimension_id, {normalize_term(group.dimension_id)}):
            cover(phrase)
        if group.time_granularity:
            cover_stems(_TIME_STEMS)
    for item in intent.filters:
        for phrase in terms.get(item.field_id, {normalize_term(item.field_id)}):
            cover(phrase)
        for value in item.values:
            cover(" ".join(stem(t) for t in tokens(str(value))))
    if intent.group_by:
        cover_stems(_GROUP_STEMS)
    if intent.filters:
        cover_stems(_FILTER_STEMS)
    if intent.time_range:
        cover_stems(_TIME_STEMS | _TIME_MOD_STEMS)
        covered.update(i for i, t in enumerate(toks) if re.fullmatch(r"(19|20)\d{2}", t))
    if intent.sort:
        cover_stems(_SORT_STEMS)
    if intent.limit != DEFAULT_LIMIT:
        cover_stems(frozenset({"top", "first", "bottom", str(intent.limit)}))
    for phrase in NEUTRAL_PHRASES:
        cover(phrase)
    return [i for i, s in enumerate(stems) if i not in covered and s not in _NEUTRAL_STEMS]


def coverage_reference(question: str | None, positions: list[int]) -> str:
    """What evidence keeps: how many words, where, and a hash of them. Never the words."""
    toks = tokens(question or "")
    digest = hashlib.sha256(" ".join(toks[i] for i in positions).encode()).hexdigest()[:12]
    return f"unaccounted_words:{len(positions)}@{','.join(str(i) for i in positions)}#{digest}"[:250]


def words_from_reference(question: str | None, reference: str) -> list[str]:
    """The requester's own words behind a reference, rebuilt from their own question. Anything malformed yields nothing."""
    match = re.match(r"^unaccounted_words:\d+@([0-9,]*)#[0-9a-f]{12}$", reference or "")
    toks = tokens(question or "")
    if not match:
        return []
    return [toks[int(i)] for i in match.group(1).split(",") if i and int(i) < len(toks)]


def explain_coverage(words: list[str]) -> str:
    shown = ", ".join(f'"{w}"' for w in words[:6]) or "some words"
    return f"The question includes words the interpreted request does not use: {shown}."


__all__ = ["NEUTRAL_VERSION", "NEUTRAL_WORDS", "coverage_reference", "explain_coverage", "unaccounted_positions", "words_from_reference"]
