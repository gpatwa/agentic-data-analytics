"""The plain-Python oracle: the correct answer to every scripted question, computed from the generated rows alone.

No warehouse, no SQL and no product code is involved, so it can check any engine, and the product, independently. The
semantics mirror the certified definitions planned for stage R2: revenue is the sum of `amount` over paid orders.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache

from reference_data import generator as g

DIMENSIONS = ("month", "region", "channel", "category")


@lru_cache(maxsize=1)
def _orders() -> tuple[tuple, ...]:
    return tuple(g.orders())


@lru_cache(maxsize=1)
def _customers() -> tuple[tuple, ...]:
    return tuple(g.customers())


def _key(row: tuple, dims: tuple[str, ...]) -> tuple:
    created, region, channel, category = row[1], row[2], row[3], row[4]
    parts = {"month": (created.year, created.month), "region": region, "channel": channel, "category": category}
    return tuple(parts[d] for d in dims)


def _select(start: datetime | None, end: datetime | None, status: str | None):
    for row in _orders():
        if status is not None and row[5] != status:
            continue
        if start is not None and row[1] < start:
            continue
        if end is not None and row[1] > end:
            continue
        yield row


def revenue_by(dims: tuple[str, ...], start: datetime | None = None, end: datetime | None = None, status: str | None = "paid") -> list[tuple[tuple, Decimal]]:
    """Sum of `amount` per group, sorted by the group key."""
    totals: dict[tuple, Decimal] = defaultdict(Decimal)
    for row in _select(start, end, status):
        totals[_key(row, dims)] += row[6]
    return sorted(totals.items())


def order_count_by(dims: tuple[str, ...], start: datetime | None = None, end: datetime | None = None, status: str | None = "paid") -> list[tuple[tuple, int]]:
    counts: Counter = Counter(_key(row, dims) for row in _select(start, end, status))
    return sorted(counts.items())


def small_groups(dims: tuple[str, ...], min_cohort: int = g.MIN_COHORT, status: str | None = "paid") -> list[tuple[tuple, int]]:
    """Groups with fewer than `min_cohort` rows: the product must suppress these."""
    return [(k, n) for k, n in order_count_by(dims, status=status) if n < min_cohort]


def visible_groups(dims: tuple[str, ...], min_cohort: int = g.MIN_COHORT, status: str | None = "paid") -> list[tuple]:
    return [k for k, n in order_count_by(dims, status=status) if n >= min_cohort]


def customers_by(dim: str) -> list[tuple[str, int]]:
    index = {"plan": 2, "region": 3}[dim]
    return sorted(Counter(row[index] for row in _customers()).items())


def sample_emails(n: int = 3) -> list[str]:
    """Values the product must never return (scene 3)."""
    return [row[4] for row in _customers()[:n]]


def fingerprint(rows: list[tuple[tuple, Decimal]]) -> str:
    digest = hashlib.sha256()
    for key, value in rows:
        digest.update((repr(key) + "|" + format(value, "f") + "\n").encode())
    return digest.hexdigest()


def totals() -> dict[str, object]:
    """Whole-table figures: a quick sanity check that any loaded copy is complete."""
    paid = sum((r[6] for r in _orders() if r[5] == "paid"), Decimal(0))
    return {"orders": len(_orders()), "paid_orders": sum(1 for r in _orders() if r[5] == "paid"), "paid_revenue": paid, "customers": len(_customers())}


def day_range(year: int, start_month: int, end_month: int) -> tuple[datetime, datetime]:
    """From the first instant of `start_month` to the last second of `end_month`, inclusive."""
    last_day = (date(year + (end_month == 12), (end_month % 12) + 1, 1) - date(year, end_month, 1)).days
    return datetime(year, start_month, 1, tzinfo=timezone.utc), datetime(year, end_month, last_day, 23, 59, 59, tzinfo=timezone.utc)
