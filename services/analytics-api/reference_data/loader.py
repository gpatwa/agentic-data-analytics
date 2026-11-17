"""Load the reference tables into an engine, and emit the DDL for each dialect (used by the tests now, by the seeder in stage R4)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from reference_data import generator as g

_TYPES = {
    "postgres": {"text": "text", "timestamp": "timestamptz", "date": "date", "decimal": "numeric(12, 2)", "integer": "integer"},
    "duckdb": {"text": "VARCHAR", "timestamp": "TIMESTAMPTZ", "date": "DATE", "decimal": "DECIMAL(12, 2)", "integer": "INTEGER"},
    "snowflake": {"text": "VARCHAR", "timestamp": "TIMESTAMP_TZ", "date": "DATE", "decimal": "NUMBER(12, 2)", "integer": "NUMBER(5, 0)"},
    "databricks": {"text": "STRING", "timestamp": "TIMESTAMP", "date": "DATE", "decimal": "DECIMAL(12, 2)", "integer": "INT"},
}


def _quote(dialect: str, identifier: str) -> str:
    # Snowflake: quoted lower case, because the Snowflake compiler quotes and keeps case. Databricks: backticks.
    if dialect == "snowflake":
        return f'"{identifier}"'
    if dialect == "databricks":
        return f"`{identifier}`"
    return identifier


def create_table_sql(dialect: str, table: str, schema: str | None = None) -> str:
    columns = ", ".join(
        f"{_quote(dialect, c.name)} {_TYPES[dialect][c.logical_type]}{'' if c.nullable else ' NOT NULL'}" for c in g.SCHEMAS[table]
    )
    name = _quote(dialect, table)
    return f"CREATE TABLE {schema + '.' if schema else ''}{name} ({columns})"


def _literal(value: Any) -> str:
    """A SQL literal for one value. DuckDB gets plain SQL text and no bound parameters: converting each bound Python value costs a
    failing optional-package import (numpy, pandas, pytz) on a clean CI image, which made loading 25,000 rows take over an hour."""
    if isinstance(value, datetime):
        return "'" + value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00") + "'"
    if isinstance(value, (Decimal, int)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def load_duckdb(conn: Any, chunk: int = 500) -> dict[str, int]:
    """Create and fill the three tables in a DuckDB connection (timestamps in UTC), from literal SQL in chunks."""
    conn.execute("SET TimeZone = 'UTC'")
    counts: dict[str, int] = {}
    for table in g.SCHEMAS:
        conn.execute(create_table_sql("duckdb", table))
        rows = g.table(table)
        for i in range(0, len(rows), chunk):
            values = ", ".join("(" + ", ".join(_literal(v) for v in row) + ")" for row in rows[i : i + chunk])
            conn.execute(f"INSERT INTO {table} VALUES {values}")
        counts[table] = len(rows)
    return counts


def load_postgres(connection: Any, schema: str) -> dict[str, int]:
    """Create `schema` and fill the three tables through a psycopg2-style connection, then commit."""
    cursor = connection.cursor()
    cursor.execute(f"CREATE SCHEMA {schema}")
    cursor.execute("SET LOCAL TIME ZONE 'UTC'")
    counts: dict[str, int] = {}
    for table in g.SCHEMAS:
        cursor.execute(create_table_sql("postgres", table, schema))
        rows = g.table(table)
        marks = ", ".join("%s" for _ in g.SCHEMAS[table])
        cursor.executemany(f"INSERT INTO {schema}.{table} VALUES ({marks})", rows)
        counts[table] = len(rows)
    connection.commit()
    return counts
