"""A backlog of what people asked and the product could not answer (E3 of `docs/design/SEMANTIC_TRUST_FOUNDATIONS_PLAN.md`).

The coverage check refuses a question that uses words the interpreted request does not account for. Counting those refusals by code says
nothing about *which* missing word or metric would turn the most refusals into answers. This module keeps that count without keeping the
words:

- each unaccounted word is reduced to its stem and then to a **salted hash** (a keyed hash, one salt per tenant); only the hash is counted;
- a run is counted once (a repeat read of the same run does not add to it);
- the owner-side report **resolves hashes against a vocabulary** (the words of the tenant's own contract), by hashing that vocabulary with
  the same salt. A hash nobody can name stays opaque and is reported as a count. Nothing is ever reversed from what is stored, and the
  question text is never stored.

It is a read model: it changes no answer, certifies nothing and edits no contract. It feeds a person's review of proposed synonyms.
Per-tenant raw-stem retention is deliberately not built (it is a separate privacy decision).
"""

from __future__ import annotations

import hmac
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any, Protocol

from sqlalchemy import Column, Engine, Integer, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.runtime.intent_coverage import NEUTRAL_WORDS
from packages.platform_contracts.semantic_terms import stem, tokens

_REFERENCE = re.compile(r"^unaccounted_words:\d+@([0-9,]*)#[0-9a-f]{12}$")
MIN_SALT_BYTES = 16
_NEUTRAL = frozenset(stem(w) for w in NEUTRAL_WORDS)


def tenant_salt(key: bytes, tenant_id: str) -> bytes:
    """One salt per tenant, derived from a deployment secret, so the salt is never stored and two tenants never share hashes."""
    if len(key) < 32:
        raise ValueError("backlog_key_too_short")
    return hmac.new(key, b"refusal-backlog-v1|" + tenant_id.encode(), sha256).digest()


def term_hash(salt: bytes, word_stem: str) -> str:
    if len(salt) < MIN_SALT_BYTES:
        raise ValueError("salt_too_short")
    return hmac.new(salt, word_stem.encode(), sha256).hexdigest()[:16]


def positions_from_reference(reference: str) -> list[int]:
    match = _REFERENCE.match(reference or "")
    return [int(i) for i in match.group(1).split(",") if i] if match else []


def stem_hashes(question: str | None, positions: Iterable[int], salt: bytes) -> list[str]:
    """Distinct salted hashes of the stems at `positions` of the question's tokens. The words themselves are not returned."""
    toks = tokens(question or "")
    return sorted({term_hash(salt, stem(toks[i])) for i in positions if 0 <= i < len(toks)})


class BacklogStore(Protocol):
    def add_once(self, run_id: str, tenant_id: str, day: str, code: str, hashes: list[str]) -> bool: ...

    def rows(self, tenant_id: str, since_day: str = "") -> list[tuple[str, str, int]]: ...


class InMemoryBacklogStore:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._counts: Counter[tuple[str, str, str, str]] = Counter()

    def add_once(self, run_id: str, tenant_id: str, day: str, code: str, hashes: list[str]) -> bool:
        if run_id in self._seen:
            return False
        self._seen.add(run_id)
        for h in hashes:
            self._counts[(tenant_id, day, code, h)] += 1
        return True

    def rows(self, tenant_id: str, since_day: str = "") -> list[tuple[str, str, int]]:
        return [(code, h, n) for (t, day, code, h), n in sorted(self._counts.items()) if t == tenant_id and day >= since_day]


_META = MetaData()
COUNTS = Table(
    "refusal_backlog_counts", _META,
    Column("tenant_id", String(255), primary_key=True), Column("day", String(10), primary_key=True),
    Column("code", String(100), primary_key=True), Column("term_hash", String(16), primary_key=True), Column("count", Integer, nullable=False),
)
SEEN = Table("refusal_backlog_seen", _META, Column("run_id", String(255), primary_key=True))  # mirrors migration 0014; a test keeps them equal


class SqlBacklogStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_once(self, run_id: str, tenant_id: str, day: str, code: str, hashes: list[str]) -> bool:
        with self._engine.begin() as conn:
            try:
                with conn.begin_nested():
                    conn.execute(insert(SEEN).values(run_id=run_id))
            except IntegrityError:
                return False
            for h in hashes:
                key = (COUNTS.c.tenant_id == tenant_id) & (COUNTS.c.day == day) & (COUNTS.c.code == code) & (COUNTS.c.term_hash == h)
                if conn.execute(update(COUNTS).where(key).values(count=COUNTS.c.count + 1)).rowcount:
                    continue
                try:
                    with conn.begin_nested():
                        conn.execute(insert(COUNTS).values(tenant_id=tenant_id, day=day, code=code, term_hash=h, count=1))
                except IntegrityError:  # another writer inserted it first
                    conn.execute(update(COUNTS).where(key).values(count=COUNTS.c.count + 1))
        return True

    def rows(self, tenant_id: str, since_day: str = "") -> list[tuple[str, str, int]]:
        with self._engine.connect() as conn:
            q = select(COUNTS.c.code, COUNTS.c.term_hash, COUNTS.c.count).where(COUNTS.c.tenant_id == tenant_id, COUNTS.c.day >= since_day)
            return [(r[0], r[1], int(r[2])) for r in conn.execute(q.order_by(COUNTS.c.day, COUNTS.c.code, COUNTS.c.term_hash))]


@dataclass(frozen=True)
class BacklogRecorder:
    """The hook the analyze service calls when a coverage refusal is returned."""

    store: BacklogStore
    salt_for: Callable[[str], bytes]
    today: Callable[[], date] = lambda: datetime.now(timezone.utc).date()

    def __call__(self, *, run_id: str, tenant_id: str, code: str, question: str | None, reference: str) -> bool:
        hashes = stem_hashes(question, positions_from_reference(reference), self.salt_for(tenant_id))
        return bool(hashes) and self.store.add_once(run_id, tenant_id, self.today().isoformat(), code, hashes)


def vocabulary_from_contract(contract: Any) -> set[str]:
    """Stems of the words in a contract's own text (ids, names, synonyms, descriptions). Digests and long tokens are skipped."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            found.update(stem(t) for t in tokens(value.replace("_", " ")) if 2 < len(t) <= 20 and not t.isdigit())
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(contract.model_dump(mode="json") if hasattr(contract, "model_dump") else contract)
    return found - _NEUTRAL


@dataclass(frozen=True)
class BacklogItem:
    term: str
    refusals: int
    codes: tuple[str, ...]


@dataclass(frozen=True)
class Backlog:
    resolved: tuple[BacklogItem, ...]
    unresolved_terms: int
    unresolved_refusals: int


def _inflections(word: str) -> set[str]:
    base = word[:-1] if word.endswith("e") else word
    return {word, word + "ed", word + "d", base + "ed", base + "ing", word + "er", word + "ers", word + "ly"}


def build_backlog(rows: Iterable[tuple[str, str, int]], vocabulary: Iterable[str], salt: bytes) -> Backlog:
    """Rank counted hashes by how many refusals they appear in, naming only those that match `vocabulary`."""
    names: dict[str, str] = {}
    for w in sorted(set(vocabulary)):  # a question word is hashed as written, so each vocabulary word is also hashed in its common inflections
        for form in _inflections(w):
            names.setdefault(term_hash(salt, form), w)
    totals: Counter[str] = Counter()
    codes: dict[str, set[str]] = {}
    for code, h, n in rows:
        totals[h] += n
        codes.setdefault(h, set()).add(code)
    resolved = sorted((BacklogItem(names[h], n, tuple(sorted(codes[h]))) for h, n in totals.items() if h in names), key=lambda i: (-i.refusals, i.term))
    opaque = [n for h, n in totals.items() if h not in names]
    return Backlog(tuple(resolved), len(opaque), sum(opaque))


def render_backlog(backlog: Backlog, *, tenant_id: str, since_day: str = "") -> str:
    lines = [f"# Refusal backlog for `{tenant_id}`" + (f" since {since_day}" if since_day else ""), "",
             "Words people used that the interpreted request did not account for, ranked by the number of refused questions they appear in.",
             "Only words that match this tenant's own contract vocabulary are named; the rest are counted and stay opaque.",
             "A word here is a prompt for a person to decide, never a certified synonym.", ""]
    lines += [f"- **{i.term}**: {i.refusals} refused question(s) ({', '.join(i.codes)}). A person can decide whether it is a synonym of an existing term or a missing concept."
              for i in backlog.resolved] or ["- no named words"]
    lines += ["", f"Not named: {backlog.unresolved_terms} distinct word(s) in {backlog.unresolved_refusals} refusal count(s)."]
    return "\n".join(lines) + "\n"


__all__ = ["Backlog", "BacklogItem", "BacklogRecorder", "BacklogStore", "InMemoryBacklogStore", "SqlBacklogStore", "build_backlog",
           "positions_from_reference", "render_backlog", "stem_hashes", "term_hash", "vocabulary_from_contract"]
