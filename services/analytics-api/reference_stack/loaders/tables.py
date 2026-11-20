"""Turning a directory of Parquet files plus a manifest into a plan: typed DDL taken from the Parquet schema, load statements, comments and keys."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import duckdb

from reference_stack.loaders.engines import Dialect, PlanError, ident
from reference_stack.loaders.plan import Check, LoadPlan, Step

STAGING = "staging"


def parquet_columns(path: Path) -> list[tuple[str, str]]:
    con = duckdb.connect()
    return [(r[0], r[1]) for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path.as_posix()}')").fetchall()]


def _comment(text: str) -> str:
    return text.replace("'", "''")


def table_steps(d: Dialect, catalog: str, schema: str, table: str, path: Path, key: tuple[str, ...], comment: str) -> list[Step]:
    cols = parquet_columns(path)
    names = {c for c, _ in cols}
    if not set(key) <= names:
        raise PlanError("key_column_missing")
    ddl_cols = [f"{d.q(c)} {d.column_type(t)}" + (" NOT NULL" if c in key else "") for c, t in cols]
    if key:
        ddl_cols.append("PRIMARY KEY (" + ", ".join(d.q(k) for k in key) + ")" if d.name == "snowflake" else "CONSTRAINT " + d.q(f"{table}_pk") + " PRIMARY KEY (" + ", ".join(d.q(k) for k in key) + ")")
    target = d.qn(catalog, schema, table)
    steps = [Step("sql", f"CREATE TABLE {target} (\n  " + ",\n  ".join(ddl_cols) + "\n)")]
    file = f"{table}.parquet"
    if d.name == "databricks":
        remote = f"/Volumes/{catalog}/{schema}/{STAGING}/{file}"
        steps.append(Step("upload", f"upload {path.name} to {remote}", local=str(path), remote=remote))
        steps.append(Step("sql", f"COPY INTO {target} FROM '{remote}' FILEFORMAT = PARQUET"))
    else:
        stage = d.qn(catalog, schema, STAGING)
        steps.append(Step("sql", f"PUT file://{path.as_posix()} @{stage} AUTO_COMPRESS = FALSE OVERWRITE = FALSE"))
        steps.append(Step("sql", f"COPY INTO {target} FROM @{stage}/{file} FILE_FORMAT = (TYPE = PARQUET) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE"))
    steps.append(Step("sql", f"COMMENT ON TABLE {target} IS '{_comment(comment)}'"))
    return steps


def base_plan(d: Dialect, catalog: str, schema: str, schema_comment: str, tables: Mapping[str, tuple[Path, tuple[str, ...], str]],
              checks: list[Check], notes: tuple[str, ...]) -> LoadPlan:
    ident(catalog), ident(schema)
    infoschema = f"{d.q(catalog)}.information_schema.tables"
    shown = schema if d.name == "databricks" else schema.upper()
    preflight = f"SELECT COUNT(*) FROM {infoschema} WHERE table_schema = '{shown}'"
    steps = [Step("sql", f"CREATE SCHEMA IF NOT EXISTS {d.qn(catalog, schema)} COMMENT '{_comment(schema_comment)}'" if d.name == "databricks"
                  else f"CREATE SCHEMA IF NOT EXISTS {d.qn(catalog, schema)} COMMENT = '{_comment(schema_comment)}'")]
    steps.append(Step("sql", f"CREATE VOLUME IF NOT EXISTS {d.qn(catalog, schema, STAGING)}" if d.name == "databricks"
                      else f"CREATE STAGE IF NOT EXISTS {d.qn(catalog, schema, STAGING)} FILE_FORMAT = (TYPE = PARQUET)"))
    for table, (path, key, comment) in tables.items():
        steps += table_steps(d, catalog, schema, table, path, key, comment)
    return LoadPlan(d, catalog, schema, preflight, tuple(steps), tuple(checks), (f"DROP SCHEMA {d.qn(catalog, schema)} CASCADE",), notes)
