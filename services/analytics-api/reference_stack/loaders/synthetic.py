"""A plan for the synthetic food-delivery data, from the generator's output directory (manifest.json, truth.json and the Parquet files)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from reference_stack.loaders import verify
from reference_stack.loaders.engines import PlanError, dialect, ident
from reference_stack.loaders.plan import Check, LoadPlan, Step
from reference_stack.loaders.tables import base_plan

KEYS = {"restaurants": ("restaurant_id",), "couriers": ("courier_id",), "customers": ("customer_id",), "orders": ("row_id",),
        "order_items": ("order_id", "line_no"), "payments": ("payment_id",), "orders_flat": ("row_id",)}
ORDER = ("restaurants", "couriers", "customers", "orders", "order_items", "payments", "orders_flat")


VIEW = "orders_current"
VIEW_COMMENT = "SYNTHETIC DATA: one row per real order, latest version, no reversals, no test orders"


def curated_view_sql(d, catalog: str, schema: str, columns: list[str]) -> str:  # noqa: ANN001
    """What `order_count` should mean on this data: one row per real order (the latest recorded version; no reversal rows; no test restaurant).
    Currency conversion is NOT done here (mixed currencies stay as they are), so amount sums over this view must still be filtered by currency."""
    flat = d.qn(catalog, schema, "orders_flat")
    cols = ", ".join(d.q(c) for c in columns)
    comment = f"COMMENT '{VIEW_COMMENT}'" if d.name == "databricks" else f"COMMENT = '{VIEW_COMMENT}'"
    body = (f"SELECT {cols} FROM (SELECT f.*, ROW_NUMBER() OVER (PARTITION BY {d.q('order_id')} ORDER BY {d.q('updated_at')} DESC, {d.q('row_id')} DESC) AS rn "
            f"FROM {flat} f WHERE NOT {d.q('is_reversal')} AND {d.q('restaurant_id')} <> 'R000000') x WHERE rn = 1")
    return f"CREATE VIEW {d.qn(catalog, schema, VIEW)} {comment} AS {body}"


def _view_checks(d, catalog: str, schema: str, truth: dict) -> list[Check]:  # noqa: ANN001
    view = d.qn(catalog, schema, VIEW)
    v = truth["values"]
    checks = [Check(f"rows:{VIEW}", f"SELECT COUNT(*) FROM {view}", v["orders_distinct"]),
              Check(f"key_unique:{VIEW}", f"SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM {view}", 0)]
    checks += [Check(f"{VIEW}:{k}", f"SELECT COUNT(*) FROM {view} WHERE status = '{k.removesuffix('_orders')}'", v[k])
               for k in ("delivered_orders", "cancelled_orders", "refunded_orders", "partially_refunded_orders")]
    return checks


def _view_only_plan(d, catalog: str, schema: str, manifest: dict, truth: dict) -> LoadPlan:  # noqa: ANN001
    """Only the view, on a schema that already holds the tables: the preflight requires the view to be ABSENT (0 rows), not the schema empty.
    Eight statements in all: that preflight, the CREATE VIEW and six checks against the answer key. Cleanup is a manual DROP VIEW, never run by the loader."""
    shown = schema if d.name == "databricks" else schema.upper()
    view_name = VIEW if d.name == "databricks" else VIEW.upper()
    preflight = f"SELECT COUNT(*) FROM {d.q(catalog)}.information_schema.tables WHERE table_schema = '{shown}' AND table_name = '{view_name}'"
    step = Step("sql", curated_view_sql(d, catalog, schema, manifest["tables"]["orders_flat"]["columns"]))
    notes = ("View-only: the tables are not touched. The view reads orders_flat and counts are checked against the generator's answer key.",
             "Currency is not converted; amount sums over this view must still be filtered by currency.",
             "SYNTHETIC DATA: generated, not real, not customer data.")
    return LoadPlan(d, catalog, schema, preflight, (step,), tuple(_view_checks(d, catalog, schema, truth)), (f"DROP VIEW {d.qn(catalog, schema, VIEW)}",), notes)


def synthetic_plan(data_dir: Path, engine: str, catalog: str, schema: str, *, with_curated_view: bool = False, view_only: bool = False) -> LoadPlan:
    ident(catalog), ident(schema)  # checked before anything is read
    d = dialect(engine)
    manifest = json.loads((data_dir / "manifest.json").read_text())
    truth = json.loads((data_dir / "truth.json").read_text())
    if manifest.get("synthetic") is not True or "SYNTHETIC DATA" not in manifest.get("notice", ""):
        raise PlanError("not_marked_synthetic")
    tables = {t: (data_dir / f"{t}.parquet", KEYS[t], manifest["table_comments"][t]) for t in ORDER}
    flat = d.qn(catalog, schema, "orders_flat")
    checks = [Check(f"rows:{t}", f"SELECT COUNT(*) FROM {d.qn(catalog, schema, t)}", manifest["tables"][t]["rows"]) for t in ORDER]
    checks.append(Check("key_unique:orders_flat", f"SELECT COUNT(*) - COUNT(DISTINCT row_id) FROM {flat}", 0))
    values = verify.food_delivery_values(d, flat)
    for name, sql in values.items():
        checks.append(Check(name, sql, truth["values"][name]))
    checks.append(Check("net_value_usd_cents", "", truth["values"]["net_value_usd_cents"], ("gross_value_usd_cents", "refunds_usd_cents")))
    for name, sql in verify.food_delivery_maps(d, flat).items():
        checks.append(Check(name, sql, truth["maps"][name]))
    notes = ("The key on each table is informational in the warehouse: a claim the loader makes about data it generated.",
             "Exact Databricks and Snowflake syntax is not verified on a workspace; the dry run is the place to find out.",
             "SYNTHETIC DATA: generated, not real, not customer data.")
    if view_only:
        return _view_only_plan(d, catalog, schema, manifest, truth)
    plan = base_plan(d, catalog, schema, "SYNTHETIC DATA: food-delivery reference tables generated by Compass", tables, checks, notes)
    if not with_curated_view:
        return plan
    more = _view_checks(d, catalog, schema, truth)
    step = Step("sql", curated_view_sql(d, catalog, schema, manifest["tables"]["orders_flat"]["columns"]))
    return dataclasses.replace(plan, steps=(*plan.steps, step), checks=(*plan.checks, *more))
