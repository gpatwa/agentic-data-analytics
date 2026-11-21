"""The scale-test statements: what the product itself would send, and the answer each must give.

The statements come from the deterministic compiler applied to the certified combined contract (not hand-written SQL). The expected answers are computed
independently, on DuckDB, from the locally generated tier's data: the same intent compiled for DuckDB runs over local copies of the two views. A statement's text is hashed for the
record and never logged."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from app.compiler import DatabricksCompilerAdapter, DuckDBCompilerAdapter
from app.semantic_registry import SemanticRegistry
from packages.platform_contracts.analytics_intent import AnalyticalIntent
from reference_stack import synthetic_views_contract as sv

TIME_DIMENSIONS = {"orders_current": "order_ts", "orders_usd_gross": "usd_order_time"}
_LIMIT = re.compile(r"\s+LIMIT\s+(\d+)\s*$", re.I)
MONEY_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class Statement:
    key: str
    shape: str
    metric_id: str
    dimension_ids: tuple[str, ...]
    grains: tuple[str | None, ...]
    sql: str
    text_hash: str
    expected: tuple[tuple[Any, ...], ...]  # every group, unlimited
    limit: int | None
    exact: bool  # counts compare exactly; money within a cent

    def public(self) -> dict[str, Any]:
        """What may be recorded: never the statement text, never a result value."""
        return {"key": self.key, "shape": self.shape, "metric": self.metric_id, "dimensions": list(self.dimension_ids), "grains": list(self.grains),
                "sql_sha256": self.text_hash, "groups": len(self.expected), "limit": self.limit}


def load_contract():
    return SemanticRegistry(sv.CERTIFIED.parents[1]).get_certified(sv.CONTRACT_ID, sv.VERSION).contract


def retarget(contract, catalog: str, schema: str):  # noqa: ANN001
    """The same certified contract pointed at another schema's copy of the views. Nothing on disk changes; no definition changes."""
    datasets = [d.model_copy(update={"physical_name": f"{catalog}.{schema}.{d.id}", "source_asset_id": f"{catalog}.{schema}.{d.id}"}) for d in contract.datasets]
    return contract.model_copy(update={"datasets": datasets})


def local_connection(data_dir: Path) -> duckdb.DuckDBPyConnection:
    """The two curated views over the generated data, named as the certified contract names them."""
    con = duckdb.connect()
    con.execute(f"CREATE VIEW f AS SELECT * FROM read_parquet('{(data_dir / 'orders_flat.parquet').as_posix()}')")
    con.execute("""CREATE VIEW cur AS SELECT * EXCLUDE (rn, is_reversal) FROM (SELECT f.*, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY updated_at DESC, row_id DESC) rn
                   FROM f WHERE NOT is_reversal AND restaurant_id <> 'R000000') WHERE rn = 1""")
    con.execute('CREATE VIEW "dev.compass_synthetic_food_delivery.orders_current" AS SELECT * FROM cur')
    con.execute("""CREATE VIEW "dev.compass_synthetic_food_delivery.orders_usd_gross" AS SELECT * FROM cur WHERE currency = 'USD' AND status <> 'cancelled'""")
    return con


def _intent(contract, metric, dims: list[tuple[str, str | None]]) -> AnalyticalIntent:  # noqa: ANN001
    body: dict[str, Any] = {"query_id": "scale", "tenant_id": "synthetic", "dataset_id": metric.dataset_id, "metrics": [{"metric_id": metric.id}],
                            "semantic_contract": {"contract_id": contract.id, "contract_version": contract.version}}
    if dims:
        body["group_by"] = [{"dimension_id": d, **({"time_granularity": g} if g else {})} for d, g in dims]
    return AnalyticalIntent.model_validate(body)


def _normal(cell: Any, time_grain: bool) -> Any:
    if cell is None:
        return None
    if time_grain:
        return str(cell.isoformat() if hasattr(cell, "isoformat") else cell)[:10]
    return str(cell)


def _number(cell: Any) -> Decimal:
    return Decimal(str(cell))


def normalise(rows, grains: tuple[str | None, ...]):  # noqa: ANN001
    """Dimension cells to text (a time bucket to its date), the metric cell to a Decimal. The warehouse returns every cell as text; DuckDB returns typed values."""
    n = len(grains)
    return [tuple([_normal(r[i], grains[i] is not None) for i in range(n)] + [_number(r[n])]) for r in rows]


def compare(statement: Statement, actual) -> str | None:  # noqa: ANN001
    """None when the answer is right, else a short reason (no values in it). The product's compiled SQL carries a LIMIT with no ORDER BY, so above the limit
    the rows returned are an arbitrary subset: each must be a real group with the right value, and the count must equal the limit."""
    got = normalise(actual, statement.grains)
    expected = {r[:-1]: r[-1] for r in normalise(statement.expected, statement.grains)}
    tolerance = Decimal(0) if statement.exact else MONEY_TOLERANCE
    if statement.limit is None or len(expected) <= statement.limit:
        if len(got) != len(expected):
            return "row_count_differs"
    elif len(got) != statement.limit:
        return "row_count_differs_from_limit"
    seen = set()
    for row in got:
        group = row[:-1]
        if group in seen:
            return "group_repeated"
        seen.add(group)
        if group not in expected:
            return "unknown_group"
        if abs(expected[group] - row[-1]) > tolerance:
            return "value_differs"
    return None


def _categorical(contract, dataset: str) -> list[str]:  # noqa: ANN001
    time_dim = TIME_DIMENSIONS[dataset]
    return [d.id for d in contract.dimensions if d.field_id.split(".")[0] == dataset and d.id != time_dim]


def build(contract, con: duckdb.DuckDBPyConnection, catalog: str, schema: str) -> list[Statement]:  # noqa: ANN001
    """About 32 statements, fixed by the contract and the data (cardinality ranks the dimensions), so a rerun is identical."""
    duck, bricks = DuckDBCompilerAdapter(), DatabricksCompilerAdapter()
    target = retarget(contract, catalog, schema)
    cards: dict[str, int] = {}

    def groups(metric, dims):  # noqa: ANN001
        plan = duck.compile(_intent(contract, metric, dims), contract, None)
        sql = _LIMIT.sub("", plan.sql)
        return plan, con.execute(sql, plan.parameters or {}).fetchall()

    out: list[Statement] = []

    def add(shape, metric, dims):  # noqa: ANN001
        plan, expected = groups(metric, dims)
        limit = int(m.group(1)) if (m := _LIMIT.search(plan.sql)) else None
        sql = bricks.compile(_intent(target, metric, dims), target, None).sql
        key = f"{shape}:{metric.id}:" + ",".join(f"{d}{'@' + g if g else ''}" for d, g in dims)
        out.append(Statement(key, shape, metric.id, tuple(d for d, _ in dims), tuple(g for _, g in dims), sql, hashlib.sha256(sql.encode()).hexdigest(),
                             tuple(tuple(r) for r in expected), limit, metric.aggregation == "count"))

    for metric in contract.metrics:
        ranked = sorted(_categorical(contract, metric.dataset_id), key=lambda d: (cards.setdefault(d, _cardinality(contract, con, metric, d)), d))
        low, second, high = ranked[0], ranked[1], ranked[-1]
        add("no_dimension", metric, [])
        add("one_low_cardinality", metric, [(low, None)])
        add("one_high_cardinality", metric, [(high, None)])
        add("two_dimensions", metric, [(low, None), (second, None)])
    for name in ("order_count", "gross_order_value"):
        metric = next(m for m in contract.metrics if m.id == name)
        for grain in ("day", "month"):
            add("time_grain", metric, [(TIME_DIMENSIONS[metric.dataset_id], grain)])
    return out


def _cardinality(contract, con, metric, dimension: str) -> int:  # noqa: ANN001
    plan = DuckDBCompilerAdapter().compile(_intent(contract, metric, [(dimension, None)]), contract, None)
    return len(con.execute(_LIMIT.sub("", plan.sql), plan.parameters or {}).fetchall())
