"""Shared pieces: a keyed hash that gives each (seed, tag, number) a fixed uniform value, and the logical digest of a table."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb

TWO53 = 9007199254740992


def u(seed: int, tag: str, n: str) -> str:
    """SQL for a uniform value in [0, 1) that depends only on (seed, tag, n). MD5 is a fixed standard, so a row never depends on any other row,
    on the number of rows, or on the DuckDB version (DuckDB's own `hash` is not guaranteed stable across versions, so it is not used)."""
    return f"((md5_number_lower('{seed}|{tag}|' || CAST(({n}) AS VARCHAR)) % {TWO53}) / {TWO53}.0)"


def sql_list(items: tuple[str, ...]) -> str:
    return "[" + ", ".join("'" + i.replace("'", "''") + "'" for i in items) + "]"


def pick(seed: int, tag: str, n: str, items: tuple[str, ...]) -> str:
    return f"list_extract({sql_list(items)}, 1 + CAST(floor({u(seed, tag, n)} * {len(items)}) AS BIGINT))"


def money(cents: str) -> str:
    """Integer cents as an exact DECIMAL(12,2)."""
    return f"CAST(CAST(({cents}) AS DECIMAL(14,0)) * CAST(0.01 AS DECIMAL(3,2)) AS DECIMAL(12,2))"


def connect(database: str = ":memory:") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database)
    con.execute("SET TimeZone = 'UTC'")
    con.execute("SET preserve_insertion_order = false")
    return con


def table_digest(con: duckdb.DuckDBPyConnection, source: str) -> dict[str, Any]:
    """An order-independent digest of every column of every row: the row count and two 64-bit hash sums. `source` is a table name or a
    `read_parquet(...)` expression. It is a logical digest, so it does not depend on the bytes the Parquet writer happens to produce."""
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]
    row = " || '|' || ".join(f"coalesce(CAST(\"{c}\" AS VARCHAR), '~NULL~')" for c in cols)
    count, lower, upper = con.execute(
        f"SELECT count(*), coalesce(sum(CAST(md5_number_lower({row}) AS HUGEINT)), 0), coalesce(sum(CAST(md5_number_upper({row}) AS HUGEINT)), 0) FROM {source}"
    ).fetchone()
    return {"rows": int(count), "columns": cols, "sum_lower": str(lower), "sum_upper": str(upper)}


def logical_digest(tables: dict[str, dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(tables, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_parquet(path: Path) -> str:
    return f"read_parquet('{path.as_posix()}')"
