"""SQL dialect details for Databricks, Snowflake and DuckDB (DuckDB is used only to check the logic of the generated queries offline)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_DECIMAL = re.compile(r"^DECIMAL\((\d{1,2}),(\d{1,2})\)$")


class PlanError(Exception):
    """A plan could not be built or a statement is not allowed. The message is a fixed code, never data."""


def ident(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise PlanError("invalid_identifier")
    return name


@dataclass(frozen=True)
class Dialect:
    name: str
    quote_char: str
    types: dict[str, str]
    to_local: str  # a template with {ts} and {tz}
    month_fmt: str  # a template with {x}
    upper_names: bool = False  # Snowflake folds unquoted names to upper case, so objects are created in upper case and queries stay unquoted

    def q(self, name: str) -> str:
        shown = ident(name).upper() if self.upper_names else name
        return f"{self.quote_char}{shown}{self.quote_char}"

    def qn(self, *parts: str) -> str:
        return ".".join(self.q(p) for p in parts)

    def column_type(self, duck_type: str) -> str:
        m = _DECIMAL.match(duck_type)
        if m:
            return duck_type
        if duck_type not in self.types:
            raise PlanError("unsupported_column_type")
        return self.types[duck_type]

    def local(self, ts: str, tz: str) -> str:
        return self.to_local.format(ts=ts, tz=tz)

    def month(self, x: str) -> str:
        return self.month_fmt.format(x=x)


_BASE = {"BIGINT": "BIGINT", "INTEGER": "INT", "BOOLEAN": "BOOLEAN", "DATE": "DATE", "DOUBLE": "DOUBLE"}
DATABRICKS = Dialect("databricks", "`", {**_BASE, "VARCHAR": "STRING", "TIMESTAMP": "TIMESTAMP"}, "from_utc_timestamp({ts}, {tz})", "date_format({x}, 'yyyy-MM')")
SNOWFLAKE = Dialect("snowflake", '"', {**_BASE, "VARCHAR": "VARCHAR", "TIMESTAMP": "TIMESTAMP_NTZ", "DOUBLE": "FLOAT"},
                    "CAST(CONVERT_TIMEZONE('UTC', {tz}, {ts}) AS TIMESTAMP_NTZ)", "TO_CHAR({x}, 'YYYY-MM')", upper_names=True)
DUCKDB = Dialect("duckdb", '"', {**_BASE, "VARCHAR": "VARCHAR", "TIMESTAMP": "TIMESTAMP"}, "timezone({tz}, {ts} AT TIME ZONE 'UTC')", "strftime({x}, '%Y-%m')")
DIALECTS = {"databricks": DATABRICKS, "snowflake": SNOWFLAKE, "duckdb": DUCKDB}


def dialect(name: str) -> Dialect:
    if name not in DIALECTS:
        raise PlanError("unknown_engine")
    return DIALECTS[name]
