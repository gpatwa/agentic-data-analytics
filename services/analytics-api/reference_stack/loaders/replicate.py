"""Phase 2: copy a loaded tier inside the warehouse N times, to reach a scale the laptop cannot generate (design: docs/design/WAREHOUSE_SCALE_TEST_DESIGN.md, section 6).

Nothing is generated or uploaded. A new schema gets the three dimension tables once and the four fact tables N times, each copy with its own keys: `row_id` is shifted by a stride
(the source's own maximum plus one) and `order_id` and `payment_id` carry the copy number (copy 0 is the original, so its ids are unchanged). Foreign keys to restaurants, couriers and customers
are NOT changed, so the test restaurant stays excluded in every copy and the dimension tables are shared.

Because every measure is computed over the latest row of each order and every order lives in exactly one copy, **every answer in the answer key is exactly N times the source's**: counts, sums,
day-boundary counts and each group in a breakdown. That is proved offline on DuckDB (tests), not assumed. What it does NOT give: new skew, new values or new customers. Repeated data compresses
and caches better than real data, so a timing here flatters the warehouse. Distinct counts over the shared dimensions (customers, restaurants) are NOT multiplied and have no check here."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reference_stack.loaders import verify
from reference_stack.loaders.engines import Dialect, PlanError, dialect, ident
from reference_stack.loaders.plan import Check, LoadPlan, Step

DIMENSIONS = ("restaurants", "couriers", "customers")
FACTS = {"orders": ("row_id", "order_id"), "orders_flat": ("row_id", "order_id"), "order_items": ("order_id",), "payments": ("payment_id", "order_id")}
SHIFTED = {"row_id"}  # shifted by the stride; the others get the copy number appended
MAX_COPIES = 100


def scale_truth(truth: dict[str, Any], copies: int) -> dict[str, Any]:
    """Every value and every group count in the answer key, times N. The hidden key (the true tip total, not checkable by a query) is scaled too and not checked."""
    scale = lambda v: v * copies  # noqa: E731
    return {"values": {k: scale(v) for k, v in truth["values"].items()}, "maps": {m: {k: scale(v) for k, v in g.items()} for m, g in truth["maps"].items()},
            "hidden": {k: scale(v) for k, v in truth.get("hidden", {}).items()}}


def scaled_manifest(manifest: dict[str, Any], copies: int) -> dict[str, Any]:
    out = json.loads(json.dumps(manifest))
    for table in FACTS:
        out["tables"][table]["rows"] = manifest["tables"][table]["rows"] * copies
    out["n_orders"] = manifest["n_orders"] * copies
    out["notice"] = manifest["notice"] + f" Replicated {copies} times inside the warehouse; every answer is {copies} times the source's."
    return out


def write_scaled_keys(source_dir: Path, out_dir: Path, copies: int) -> None:
    """A directory with the scaled manifest and answer key (no Parquet), so the loader's `--view-only` and `--usd-view-only` modes can create and check the two views on the new schema."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((source_dir / "manifest.json").read_text())
    truth = json.loads((source_dir / "truth.json").read_text())
    (out_dir / "manifest.json").write_text(json.dumps(scaled_manifest(manifest, copies), indent=1))
    (out_dir / "truth.json").write_text(json.dumps(scale_truth(truth, copies), indent=1))


def _copies_table(copies: int) -> str:
    return "(VALUES " + ", ".join(f"({k})" for k in range(copies)) + ") AS r(k)"


def fact_sql(d: Dialect, catalog: str, source: str, target: str, table: str, columns: list[str], copies: int) -> str:
    src, dst = d.qn(catalog, source, table), d.qn(catalog, target, table)
    changed = FACTS[table]
    parts = []
    for c in columns:
        q = d.q(c)
        if c in changed and c in SHIFTED:
            parts.append(f"f.{q} + r.k * s.stride AS {q}")
        elif c in changed:
            parts.append(f"CASE WHEN r.k = 0 THEN f.{q} ELSE CONCAT(f.{q}, '.', CAST(r.k AS {d.column_type('VARCHAR')})) END AS {q}")
        else:
            parts.append(f"f.{q}")
    stride = f"(SELECT COALESCE(MAX({d.q('row_id')}), 0) + 1 AS stride FROM {src}) s" if any(c in SHIFTED for c in changed) else "(SELECT 0 AS stride) s"
    return f"CREATE TABLE {dst} AS SELECT {', '.join(parts)} FROM {src} f CROSS JOIN {_copies_table(copies)} CROSS JOIN {stride}"


def replication_plan(data_dir: Path, engine: str, catalog: str, source_schema: str, target_schema: str, copies: int, *, light_checks: bool = False) -> LoadPlan:
    ident(catalog), ident(source_schema), ident(target_schema)
    if not 2 <= copies <= MAX_COPIES:
        raise PlanError("copies_out_of_range")
    if source_schema.casefold() == target_schema.casefold():
        raise PlanError("source_and_target_are_the_same_schema")
    d = dialect(engine)
    manifest = json.loads((data_dir / "manifest.json").read_text())
    truth = scale_truth(json.loads((data_dir / "truth.json").read_text()), copies)
    if manifest.get("synthetic") is not True or "SYNTHETIC DATA" not in manifest.get("notice", ""):
        raise PlanError("not_marked_synthetic")
    shown = target_schema if d.name == "databricks" else target_schema.upper()
    preflight = f"SELECT COUNT(*) FROM {d.q(catalog)}.information_schema.tables WHERE table_schema = '{shown}'"
    comment = f"SYNTHETIC DATA: {source_schema} replicated {copies} times inside the warehouse"
    schema_sql = (f"CREATE SCHEMA IF NOT EXISTS {d.qn(catalog, target_schema)} COMMENT '{comment}'" if d.name == "databricks"
                  else f"CREATE SCHEMA IF NOT EXISTS {d.qn(catalog, target_schema)} COMMENT = '{comment}'")
    steps = [Step("sql", schema_sql)]
    for t in DIMENSIONS:
        steps.append(Step("sql", f"CREATE TABLE {d.qn(catalog, target_schema, t)} AS SELECT * FROM {d.qn(catalog, source_schema, t)}"))
    for t in FACTS:
        steps.append(Step("sql", fact_sql(d, catalog, source_schema, target_schema, t, manifest["tables"][t]["columns"], copies)))
    flat = d.qn(catalog, target_schema, "orders_flat")
    checks = [Check(f"rows:{t}", f"SELECT COUNT(*) FROM {d.qn(catalog, target_schema, t)}", manifest["tables"][t]["rows"] * (copies if t in FACTS else 1)) for t in (*DIMENSIONS, *FACTS)]
    checks.append(Check("key_unique:orders_flat", f"SELECT COUNT(*) - COUNT(DISTINCT row_id) FROM {flat}", 0))
    checks.append(Check("key_unique:orders", f"SELECT COUNT(*) - COUNT(DISTINCT row_id) FROM {d.qn(catalog, target_schema, 'orders')}", 0))
    checks.append(Check("key_unique:payments", f"SELECT COUNT(*) - COUNT(DISTINCT payment_id) FROM {d.qn(catalog, target_schema, 'payments')}", 0))
    values = verify.food_delivery_values(d, flat)
    keep = ("orders_distinct", "gross_value_usd_cents", "naive_row_count") if light_checks else tuple(values)
    checks += [Check(n, values[n], truth["values"][n]) for n in keep]
    if not light_checks:
        checks.append(Check("net_value_usd_cents", "", truth["values"]["net_value_usd_cents"], ("gross_value_usd_cents", "refunds_usd_cents")))
        checks += [Check(n, sql, truth["maps"][n]) for n, sql in verify.food_delivery_maps(d, flat).items()]
    notes = (f"Replication: {copies} copies of the four fact tables, the three dimension tables once. Every answer is exactly {copies} times the source's (proved on DuckDB in the tests).",
             "Repeated data has no new skew or values and compresses and caches better than real data: a timing here flatters the warehouse. Distinct customers and restaurants are not multiplied.",
             "SYNTHETIC DATA: generated, not real, not customer data.")
    return LoadPlan(d, catalog, target_schema, preflight, tuple(steps), tuple(checks), (f"DROP SCHEMA {d.qn(catalog, target_schema)} CASCADE",), notes, (source_schema,))
