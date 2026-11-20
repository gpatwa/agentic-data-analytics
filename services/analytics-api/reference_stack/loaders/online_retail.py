"""Online Retail II: a converter, two answer keys, and a load plan (docs/design/REALISTIC_DATA_SOURCE_REVIEW.md).

Source: Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository, https://doi.org/10.24432/C5CG6D. Licence CC BY 4.0: any page or
table that shows it must carry that credit. The workbook is untrusted input: read it in isolated mode (`python -I`), from a directory of its own.

`convert` streams the workbook once and keeps two independent keys: a Python ledger (exact Decimal arithmetic while reading) and a DuckDB
recomputation over the Parquet it wrote. They must agree. Added columns: `row_id` (sheet order, the declared key), `line_amount` (quantity
times price, added because the compiler cannot multiply columns) and `is_cancellation` (invoice number starts with "C").
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from reference_stack.loaders import verify
from reference_stack.loaders.engines import PlanError, dialect, ident
from reference_stack.loaders.plan import Check, LoadPlan
from reference_stack.loaders.tables import base_plan

ATTRIBUTION = "Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository, https://doi.org/10.24432/C5CG6D. Licence: CC BY 4.0."
HEADER = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]
TABLE = "online_retail_ii"
NULL = "\\N"
PRICE_SCALE = Decimal("0.0001")


def _text(value: Any) -> str:
    return NULL if value is None or str(value).strip() == "" else str(value).strip()


def _customer(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return NULL
    return str(int(float(value))) if isinstance(value, (int, float)) else str(value).strip()


def convert(xlsx: Path, out_dir: Path) -> dict[str, Any]:
    """Convert the workbook to `online_retail_ii.parquet` and write `manifest.json` and `oracle.json` under `out_dir`."""
    import openpyxl  # imported here: it is only needed for this one-off conversion

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "rows.csv"
    ledger = {"line_count": 0, "sales_lines_value": Decimal(0), "cancellation_value": Decimal(0), "cancellation_lines": 0,
              "lines_without_customer": 0, "quantity_total": 0}
    invoices: set[str] = set()
    customers: set[str] = set()
    countries: dict[str, int] = {}
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    row_id = 0
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for sheet in wb.worksheets:
            rows = sheet.iter_rows(values_only=True)
            header = [str(c).strip() if c is not None else "" for c in next(rows)][:8]
            if header != HEADER:
                raise PlanError("unexpected_header")
            for r in rows:
                if r is None or all(c is None for c in r[:8]):
                    continue
                invoice, code, desc, qty, when, price, cust, country = (list(r) + [None] * 8)[:8]
                if invoice is None or qty is None or when is None or price is None:
                    raise PlanError("missing_required_value")
                price_d = Decimal(repr(float(price))) if not isinstance(price, Decimal) else price
                if price_d != price_d.quantize(PRICE_SCALE):
                    raise PlanError("price_scale")
                qty_i = int(qty)
                amount = Decimal(qty_i) * price_d
                row_id += 1
                inv = str(invoice).strip()
                cancel = inv.upper().startswith("C")
                writer.writerow([row_id, inv, _text(code), _text(desc), qty_i, when.strftime("%Y-%m-%d %H:%M:%S"), format(price_d, "f"),
                                 _customer(cust), _text(country), format(amount, "f"), "true" if cancel else "false", sheet.title])
                ledger["line_count"] += 1
                ledger["quantity_total"] += qty_i
                invoices.add(inv)
                if cust is not None and str(cust).strip() != "":
                    customers.add(_customer(cust))
                else:
                    ledger["lines_without_customer"] += 1
                if cancel:
                    ledger["cancellation_lines"] += 1
                    ledger["cancellation_value"] += amount
                else:
                    ledger["sales_lines_value"] += amount
                key = _text(country)
                countries[key] = countries.get(key, 0) + 1
    wb.close()
    parquet = out_dir / f"{TABLE}.parquet"
    con = duckdb.connect()
    con.execute(f"""
        COPY (SELECT * FROM read_csv('{csv_path.as_posix()}', header = false, nullstr = '{NULL}', delim = ',', quote = '"', escape = '"',
              columns = {{'row_id': 'BIGINT', 'invoice_no': 'VARCHAR', 'stock_code': 'VARCHAR', 'description': 'VARCHAR', 'quantity': 'INTEGER',
                         'invoice_ts': 'TIMESTAMP', 'unit_price': 'DECIMAL(12,4)', 'customer_id': 'VARCHAR', 'country': 'VARCHAR',
                         'line_amount': 'DECIMAL(16,4)', 'is_cancellation': 'BOOLEAN', 'source_sheet': 'VARCHAR'}}) ORDER BY row_id)
        TO '{parquet.as_posix()}' (FORMAT PARQUET)
    """)
    csv_path.unlink()
    cents = lambda d: int((d * 100).to_integral_value())  # noqa: E731
    ledger_values = {
        "line_count": ledger["line_count"], "invoice_count": len(invoices), "customer_count": len(customers),
        "lines_without_customer": ledger["lines_without_customer"], "cancellation_lines": ledger["cancellation_lines"],
        "sales_lines_value_cents": cents(ledger["sales_lines_value"]), "cancellation_value_cents": cents(ledger["cancellation_value"]),
        "naive_net_value_cents": cents(ledger["sales_lines_value"] + ledger["cancellation_value"]), "quantity_total": ledger["quantity_total"],
    }
    again = recompute(parquet)
    truth = {"values": ledger_values, "maps": {"lines_by_country": countries}}
    disagreements = [k for k in ledger_values if ledger_values[k] != again["values"][k]] + (["lines_by_country"] if countries != again["maps"]["lines_by_country"] else [])
    columns = [(r[0], r[1]) for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet.as_posix()}')").fetchall()]
    manifest = {"source": "UCI Online Retail II", "attribution": ATTRIBUTION, "licence": "CC BY 4.0", "table": TABLE, "rows": ledger["line_count"],
                "columns": columns, "key": ["row_id"], "disagreements": disagreements,
                "added_columns": {"row_id": "sheet order, the declared key", "line_amount": "quantity times unit_price", "is_cancellation": "invoice starts with C"},
                "note": "Real data, converted. Duplicates and odd stock codes in the source are kept as they are."}
    (out_dir / "oracle.json").write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if disagreements:
        raise PlanError("answer_keys_disagree")
    return manifest


def recompute(parquet: Path) -> dict[str, Any]:
    """The DuckDB flavour of the verification queries, run over the Parquet alone."""
    con = duckdb.connect()
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{parquet.as_posix()}')")
    values = {k: int(con.execute(sql).fetchone()[0] or 0) for k, sql in verify.retail_values("t").items()}
    maps = {k: {a: int(b) for a, b in con.execute(sql).fetchall()} for k, sql in verify.retail_maps("t").items()}
    return {"values": values, "maps": maps}


def retail_plan(data_dir: Path, engine: str, catalog: str, schema: str) -> LoadPlan:
    ident(catalog), ident(schema)
    d = dialect(engine)
    manifest = json.loads((data_dir / "manifest.json").read_text())
    truth = json.loads((data_dir / "oracle.json").read_text())
    if manifest.get("licence") != "CC BY 4.0" or manifest.get("disagreements"):
        raise PlanError("not_ready_to_load")
    target = d.qn(catalog, schema, TABLE)
    checks = [Check("rows", f"SELECT COUNT(*) FROM {target}", manifest["rows"]), Check("key_unique", f"SELECT COUNT(*) - COUNT(DISTINCT row_id) FROM {target}", 0)]
    checks += [Check(k, sql, truth["values"][k]) for k, sql in verify.retail_values(target).items()]
    checks += [Check(k, sql, truth["maps"][k]) for k, sql in verify.retail_maps(target).items()]
    tables = {TABLE: (data_dir / f"{TABLE}.parquet", ("row_id",), f"Online Retail II. {ATTRIBUTION}")}
    return base_plan(d, catalog, schema, f"Reference data. {ATTRIBUTION}", tables, checks,
                     ("Credit line required by CC BY 4.0: any page or screenshot that shows this table must carry the attribution in the table comment.",
                      "The key is informational in the warehouse: a claim about a surrogate sheet-order id."))
