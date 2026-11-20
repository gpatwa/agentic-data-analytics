"""The food-delivery domain (docs/design/SYNTHETIC_DATA_GENERATOR_SPEC.md section 4). Everything here is synthetic and says so.

`generate` builds the tables in DuckDB from a keyed hash, writes Parquet, and returns the manifest. The clean order table `ob` (one row per
order, before any defect) is the source of the truth ledger in `truth.py`; the defects are applied only when rows are emitted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb

from reference_stack.synthetic import reference as ref
from reference_stack.synthetic.config import DEFECT_TEXT, DEFECTS, EVIDENCE, GENERATOR_VERSION, GeneratorConfig
from reference_stack.synthetic.engine import (
    connect,
    logical_digest,
    money,
    pick,
    read_parquet,
    sql_list,
    table_digest,
    u,
)

TABLES = ("restaurants", "couriers", "customers", "orders", "order_items", "payments", "orders_flat")
SYNTHETIC = "SYNTHETIC DATA"


@dataclass(frozen=True)
class Manifest:
    body: dict

    @property
    def logical_digest(self) -> str:
        return self.body["logical_digest"]


def _cum(weights: list[float]) -> list[float]:
    total, out, run = sum(weights), [], 0.0
    for w in weights:
        out.append(run / total)
        run += w
    return out


def _reference_tables(con: duckdb.DuckDBPyConnection, cfg: GeneratorConfig) -> None:
    weights = [1 / (rank + 1) ** 0.9 for rank in range(len(ref.CITIES))]
    cum = _cum(weights)
    con.execute("CREATE TABLE cities (city_id INTEGER, city VARCHAR, country VARCHAR, tz VARCHAR, currency VARCHAR, cum_lo DOUBLE)")
    con.executemany("INSERT INTO cities VALUES (?, ?, ?, ?, ?, ?)", [(i, c, k, tz, cur, cum[i]) for i, (c, k, tz, cur) in enumerate(ref.CITIES)])
    start = date.fromisoformat(cfg.start_date)
    days = [start + timedelta(days=d) for d in range(cfg.days)]
    dweights = [ref.WEEKDAY_FACTOR[d.weekday()] * (1.6 if (d.month, d.day) in ref.HOLIDAY_MONTH_DAY else 1.0) for d in days]
    dcum = _cum(dweights)
    con.execute("CREATE TABLE days (day_idx INTEGER, d DATE, cum_lo DOUBLE)")
    con.executemany("INSERT INTO days VALUES (?, ?, ?)", [(i, d, dcum[i]) for i, d in enumerate(days)])
    hcum = _cum([float(w) for w in ref.HOUR_WEIGHT])
    con.execute("CREATE TABLE hours (h INTEGER, cum_lo DOUBLE)")
    con.executemany("INSERT INTO hours VALUES (?, ?)", list(enumerate(hcum)))


def _located_entities(con: duckdb.DuckDBPyConnection, cfg: GeneratorConfig, table: str, prefix: str, count: int, extra: str) -> None:
    """Restaurants or couriers: each city gets at least one (the first 40 are placed by index); the rest follow the city weights."""
    s = cfg.seed
    con.execute(f"""
        CREATE TABLE {table} AS
        WITH r AS (SELECT rn, {u(s, table + '_city', 'rn')} AS uc FROM range({count}) t(rn)),
        placed AS (SELECT r.rn, coalesce(CASE WHEN r.rn < {len(ref.CITIES)} THEN r.rn END, c.city_id) AS city_id
                   FROM r ASOF LEFT JOIN cities c ON r.uc >= c.cum_lo)
        SELECT '{prefix}' || lpad(CAST(rn + 1 AS VARCHAR), 6, '0') AS {table[:-1]}_id, rn, city_id,
               row_number() OVER (PARTITION BY city_id ORDER BY rn) AS city_rank {extra}
        FROM placed
    """)


def generate(cfg: GeneratorConfig, out_dir: Path, *, database: str = ":memory:") -> Manifest:
    """Build every table, write Parquet under `out_dir`, write `manifest.json` and return it. `truth.json` is written by `truth.write_truth`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    con = connect(database)
    s, n, rates = cfg.seed, cfg.n_orders, cfg.rates
    r = rates.model_copy(update={
        "late_version": rates.late_version if cfg.on("D3") else 0.0, "duplicate": rates.duplicate if cfg.on("D4") else 0.0,
        "test_order": rates.test_order if cfg.on("D9") else 0.0, "orphan": rates.orphan if cfg.on("D10") else 0.0,
        "spelling_drift": rates.spelling_drift if cfg.on("D8") else 0.0, "delivery_sentinel": rates.delivery_sentinel if cfg.on("D7") else 0.0,
        "prep_sentinel": rates.prep_sentinel if cfg.on("D7") else 0.0,
    })
    _reference_tables(con, cfg)
    n_rest, n_cour, n_cust = max(80, n // 200), max(60, n // 500), max(100, n // 5)

    _located_entities(con, cfg, "restaurants", "R", n_rest,
                      f", {pick(s, 'rname_a', 'rn', ref.WORDS_A)} || ' ' || {pick(s, 'rname_b', 'rn', ref.WORDS_B)} AS restaurant_name, {pick(s, 'cuisine', 'rn', ref.CUISINES)} AS cuisine")
    _located_entities(con, cfg, "couriers", "C", n_cour,
                      f", '+1-555-01' || lpad(CAST(rn % 100 AS VARCHAR), 2, '0') AS courier_phone, {pick(s, 'vehicle', 'rn', ('bike', 'scooter', 'car'))} AS vehicle")
    con.execute(f"""
        CREATE TABLE customers AS
        SELECT 'U' || lpad(CAST(rn + 1 AS VARCHAR), 7, '0') AS customer_id,
               'user_' || lpad(CAST(rn + 1 AS VARCHAR), 7, '0') || '@example.invalid' AS customer_email,
               CAST(DATE '{cfg.start_date}' AS DATE) - CAST(floor({u(s, 'signup', 'rn')} * 700) AS INTEGER) AS signup_date
        FROM range({n_cust}) t(rn)
    """)
    con.execute("CREATE TABLE rest_counts AS SELECT city_id, count(*) AS n FROM restaurants GROUP BY 1")
    con.execute("CREATE TABLE cour_counts AS SELECT city_id, count(*) AS n FROM couriers GROUP BY 1")

    # --- the clean order, before any defect -------------------------------------------------------------------------------------------
    cancel_hi = r.cancelled
    refund_hi = cancel_hi + r.refunded
    partial_hi = refund_hi + r.partially_refunded
    con.execute(f"""
        CREATE TABLE ob0 AS
        WITH a AS (SELECT i, {u(s, 'city', 'i')} AS uc, {u(s, 'day', 'i')} AS ud, {u(s, 'hour', 'i')} AS uh FROM range({n}) t(i)),
        c AS (SELECT a.*, ci.city_id, ci.city, ci.country, ci.tz, ci.currency FROM a ASOF JOIN cities ci ON a.uc >= ci.cum_lo),
        d AS (SELECT c.*, dy.d FROM c ASOF JOIN days dy ON c.ud >= dy.cum_lo),
        h AS (SELECT d.*, hr.h FROM d ASOF JOIN hours hr ON d.uh >= hr.cum_lo)
        SELECT i, city_id, city, country, tz, {"currency" if cfg.on("D6") else "'USD'"} AS currency,
               CAST(h.d AS TIMESTAMP) + h.h * INTERVAL 1 HOUR + CAST(floor({u(s, 'sec', 'i')} * 3600) AS INTEGER) * INTERVAL 1 SECOND AS local_ts,
               'O' || lpad(CAST(i AS VARCHAR), 10, '0') AS order_id,
               CASE WHEN {u(s, 'status', 'i')} < {cancel_hi} THEN 'cancelled' WHEN {u(s, 'status', 'i')} < {refund_hi} THEN 'refunded'
                    WHEN {u(s, 'status', 'i')} < {partial_hi} THEN 'partially_refunded' ELSE 'delivered' END AS status,
               CASE WHEN {u(s, 'pay', 'i')} < 0.62 THEN 'card' WHEN {u(s, 'pay', 'i')} < 0.90 THEN 'wallet' ELSE 'cash' END AS payment_method,
               CASE WHEN {u(s, 'plat', 'i')} < 0.70 THEN 'app' WHEN {u(s, 'plat', 'i')} < 0.88 THEN 'web' ELSE 'partner' END AS platform,
               1 + CAST(floor(pow({u(s, 'nitems', 'i')}, 2) * 6) AS INTEGER) AS items_count,
               CAST(floor({n_cust} * pow({u(s, 'cust', 'i')}, 1.5)) AS BIGINT) AS cust_idx,
               {u(s, 'rest', 'i')} AS u_rest, {u(s, 'cour', 'i')} AS u_cour,
               {u(s, 'test', 'i')} < {r.test_order} AS is_test,
               {u(s, 'orph', 'i')} < {r.orphan} AS is_orphan
        FROM h
    """)
    con.execute("""
        ALTER TABLE ob0 ADD COLUMN order_ts TIMESTAMP;
        UPDATE ob0 SET order_ts = CAST(timezone(tz, local_ts) AT TIME ZONE 'UTC' AS TIMESTAMP);
    """)
    con.execute(f"""
        CREATE TABLE order_items AS
        SELECT order_id, j AS line_no, {pick(s, 'dish', 'i * 8 + j', ref.DISHES)} AS item_name,
               1 + CAST(floor(pow({u(s, 'iqty', 'i * 8 + j')}, 3) * 3) AS INTEGER) AS quantity,
               300 + CAST(floor({u(s, 'iprice', 'i * 8 + j')} * 2500) AS INTEGER) AS unit_cents, i
        FROM (SELECT i, order_id, unnest(generate_series(1, items_count)) AS j FROM ob0)
    """)
    con.execute("CREATE TABLE subtotals AS SELECT i, sum(quantity * unit_cents) AS subtotal FROM order_items GROUP BY i")
    tip_pct = lambda tag: f"(10 + CAST(floor({u(s, tag, 'i')} * 16) AS INTEGER))"  # noqa: E731
    con.execute("""
        CREATE TABLE ob1 AS
        SELECT o.*, CASE WHEN is_test THEN 1 ELSE st.subtotal END AS subtotal
        FROM ob0 o JOIN subtotals st USING (i)
    """)
    con.execute(f"""
        CREATE TABLE ob2 AS
        SELECT *,
               CASE WHEN is_test THEN 0 ELSE 99 + CAST(floor({u(s, 'dfee', 'i')} * 300) AS INTEGER) END AS delivery_fee,
               CASE WHEN is_test THEN 0 ELSE (subtotal * 15 + 50) // 100 END AS service_fee,
               CASE WHEN is_test THEN 0 ELSE (subtotal * 8 + 50) // 100 END AS tax,
               CASE WHEN is_test OR {u(s, 'promo', 'i')} >= {r.promo} THEN 0 ELSE 10 + 5 * CAST(floor({u(s, 'promop', 'i')} * 3) AS INTEGER) END AS promo_pct,
               CASE WHEN is_test OR status = 'cancelled' THEN 0
                    WHEN payment_method = 'cash' AND {u(s, 'tip', 'i')} < {r.tip_cash_true} THEN (subtotal * {tip_pct('tipp')} + 50) // 100
                    WHEN payment_method <> 'cash' AND {u(s, 'tip', 'i')} < {r.tip_card_or_wallet} THEN (subtotal * {tip_pct('tipp')} + 50) // 100
                    ELSE 0 END AS tip_true
        FROM ob1
    """)
    cash_hidden = "AND payment_method = 'cash'" if cfg.on("D2") else "AND false"
    con.execute(f"""
        CREATE TABLE ob3 AS
        SELECT *, (subtotal * promo_pct + 50) // 100 AS discount,
               CASE WHEN true {cash_hidden} THEN 0 ELSE tip_true END AS tip_rec,
               NOT (status = 'cancelled' AND {u(s, 'cbd', 'i')} < {r.cancelled_before_dispatch}) AS dispatched,
               CASE promo_pct WHEN 10 THEN '{ref.PROMOS[10]}' WHEN 15 THEN '{ref.PROMOS[15]}' WHEN 20 THEN '{ref.PROMOS[20]}' END AS promo_code
        FROM ob2
    """)
    con.execute(f"""
        CREATE TABLE ob4 AS
        SELECT *, subtotal + delivery_fee + service_fee + tax - discount + tip_rec AS total,
               CASE WHEN dispatched THEN 8 + CAST(floor({u(s, 'prep', 'i')} * 25) AS INTEGER) END AS prep_true,
               CASE WHEN dispatched AND status <> 'cancelled' THEN least(120, 10 + CAST(floor(-ln(1 - {u(s, 'dlv', 'i')} * 0.999) * 14) AS INTEGER)) END AS delivery_true,
               {u(s, 'psent', 'i')} < {r.prep_sentinel} AS prep_sent, {u(s, 'dsent', 'i')} < {r.delivery_sentinel} AS delivery_sent,
               status <> 'delivered' AND {u(s, 'late', 'i')} < {r.late_version} AS is_late,
               {u(s, 'dup', 'i')} < {r.duplicate} AS is_dup,
               {u(s, 'refpct', 'i')} AS u_ref
        FROM ob3
    """)
    d1 = cfg.on("D1")
    con.execute(f"""
        CREATE TABLE ob AS
        SELECT o.*, CASE WHEN o.status = 'refunded' THEN o.total
                         WHEN o.status = 'partially_refunded' THEN (o.total * (20 + CAST(floor(o.u_ref * 60) AS INTEGER)) + 50) // 100 ELSE 0 END AS refund,
               rr.restaurant_id AS true_restaurant_id, cc.courier_id AS true_courier_id,
               'U' || lpad(CAST(o.cust_idx + 1 AS VARCHAR), 7, '0') AS customer_id,
               CAST(floor({u(s, 'addrn', 'o.i')} * 999) AS INTEGER) + 1 AS addr_no, {pick(s, 'street', 'o.i', ref.STREETS)} AS addr_street
        FROM ob4 o
        JOIN rest_counts rc USING (city_id)
        JOIN restaurants rr ON rr.city_id = o.city_id AND rr.city_rank = 1 + CAST(floor(rc.n * pow(o.u_rest, 2)) AS BIGINT)
        JOIN cour_counts cn ON cn.city_id = o.city_id
        JOIN couriers cc ON cc.city_id = o.city_id AND cc.city_rank = 1 + CAST(floor(cn.n * o.u_cour) AS BIGINT)
    """)
    con.execute(f"""
        ALTER TABLE ob ADD COLUMN has_reversal BOOLEAN;
        UPDATE ob SET has_reversal = {'refund > 0' if d1 else 'false'};
    """)

    _emit_tables(con, cfg, r)
    from reference_stack.synthetic.truth import ledger  # after the clean table exists, before the connection closes

    truth = ledger(con, cfg)
    (out_dir / "truth.json").write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
    manifest = _write(con, cfg, out_dir)
    con.close()
    return manifest


def _emit_tables(con: duckdb.DuckDBPyConnection, cfg: GeneratorConfig, r) -> None:
    s = cfg.seed
    d1 = cfg.on("D1")
    final_total = "ob.total" if d1 else "ob.total - ob.refund"
    # restaurant id as recorded: a test order sits at the test restaurant, an orphan points at an id that does not exist
    rest_id = (f"CASE WHEN ob.is_test THEN '{ref.TEST_RESTAURANT[0]}' WHEN ob.is_orphan THEN 'R9' || lpad(CAST(CAST(floor({u(s, 'orphid', 'ob.i')} * 99999) AS INTEGER) AS VARCHAR), 5, '0') "
               "ELSE ob.true_restaurant_id END")
    base_cols = {
        "order_id": "ob.order_id", "order_ts": "ob.order_ts", "city": "ob.city", "country": "ob.country", "restaurant_id": rest_id,
        "platform": "ob.platform", "status": "ob.status", "subtotal": money("ob.subtotal"), "delivery_fee": money("ob.delivery_fee"),
        "service_fee": money("ob.service_fee"), "discount_amount": money("ob.discount"), "tax": money("ob.tax"), "tip_amount": money("ob.tip_rec"),
        "total_amount": money(final_total), "currency": "ob.currency", "payment_method": "ob.payment_method", "items_count": "ob.items_count",
        "prep_minutes": "CASE WHEN ob.prep_true IS NOT NULL AND ob.prep_sent THEN -1 ELSE ob.prep_true END",
        "delivery_minutes": "CASE WHEN ob.delivery_true IS NOT NULL AND ob.delivery_sent THEN -1 ELSE ob.delivery_true END",
        "courier_id": "CASE WHEN ob.dispatched THEN ob.true_courier_id END", "customer_id": "ob.customer_id", "promo_code": "ob.promo_code",
        "is_reversal": "false", "updated_at": "ob.order_ts + (5 + CAST(floor(" + u(s, "upd", "ob.i") + " * 10) AS INTEGER)) * INTERVAL 1 MINUTE",
        "delivery_address": "CAST(ob.addr_no AS VARCHAR) || ' ' || ob.addr_street || ' Street'",
    }

    def emit(slot: int, where: str, overrides: dict[str, str]) -> str:
        cols = {**base_cols, **overrides}
        return f"SELECT ob.i * 4 + {slot} AS row_id, " + ", ".join(f"{expr} AS {name}" for name, expr in cols.items()) + f" FROM ob WHERE {where}"

    late_status = {"status": "'delivered'", "total_amount": money("ob.total")}
    slot0 = emit(0, "true", {k: f"CASE WHEN ob.is_late THEN {v} ELSE {base_cols[k]} END" for k, v in late_status.items()})
    slot1 = emit(1, "ob.is_late", {"updated_at": "ob.order_ts + (1 + CAST(floor(" + u(s, "upd2", "ob.i") + " * 47) AS INTEGER)) * INTERVAL 1 HOUR"})
    zero = money("0")
    slot2 = emit(2, "ob.has_reversal", {
        "is_reversal": "true", "subtotal": zero, "delivery_fee": zero, "service_fee": zero, "discount_amount": zero, "tax": zero, "tip_amount": zero,
        "total_amount": money("-ob.refund"), "updated_at": "ob.order_ts + (24 + CAST(floor(" + u(s, "updr", "ob.i") + " * 48) AS INTEGER)) * INTERVAL 1 HOUR",
    })
    slot3_updated = ("CASE WHEN ob.is_late THEN ob.order_ts + (1 + CAST(floor(" + u(s, "upd2", "ob.i") + " * 47) AS INTEGER)) * INTERVAL 1 HOUR "
                     "ELSE ob.order_ts + (5 + CAST(floor(" + u(s, "upd", "ob.i") + " * 10) AS INTEGER)) * INTERVAL 1 MINUTE END")
    slot3 = emit(3, "ob.is_dup", {"updated_at": slot3_updated})
    con.execute(f"CREATE TABLE orders AS {slot0} UNION ALL {slot1} UNION ALL {slot2} UNION ALL {slot3}")

    drift = (f"CASE WHEN r.cuisine IS NULL THEN NULL WHEN {u(s, 'drift', 'o.row_id')} >= {r.spelling_drift} THEN r.cuisine ELSE "
             f"CASE CAST(floor({u(s, 'var', 'o.row_id')} * {ref.SPELLING_VARIANTS}) AS INTEGER) WHEN 0 THEN upper(r.cuisine) WHEN 1 THEN r.cuisine || ' ' "
             "WHEN 2 THEN r.cuisine || ' Place' ELSE lower(r.cuisine) END END")
    con.execute(f"""
        CREATE TABLE orders_flat AS
        SELECT o.row_id, o.order_id, o.order_ts, o.city, o.country, o.restaurant_id,
               CASE WHEN o.restaurant_id = '{ref.TEST_RESTAURANT[0]}' THEN '{ref.TEST_RESTAURANT[1]}' ELSE r.restaurant_name END AS restaurant_name,
               {drift} AS cuisine,
               o.platform, o.status, o.subtotal, o.delivery_fee, o.service_fee, o.discount_amount, o.tax, o.tip_amount, o.total_amount, o.currency,
               o.payment_method, o.items_count, o.prep_minutes, o.delivery_minutes, o.courier_id, o.customer_id,
               c.customer_email, k.courier_phone, o.delivery_address, o.promo_code, o.is_reversal, o.updated_at
        FROM orders o
        LEFT JOIN restaurants r ON r.restaurant_id = o.restaurant_id
        LEFT JOIN customers c ON c.customer_id = o.customer_id
        LEFT JOIN couriers k ON k.courier_id = o.courier_id
    """)
    con.execute(f"""
        CREATE TABLE payments AS
        SELECT 'P' || lpad(CAST(ob.i AS VARCHAR), 10, '0') || '-C' AS payment_id, ob.order_id, ob.order_ts AS event_ts, 'charge' AS kind,
               {money('ob.total')} AS amount, ob.currency, ob.payment_method
        FROM ob WHERE ob.status <> 'cancelled'
        UNION ALL
        SELECT 'P' || lpad(CAST(ob.i AS VARCHAR), 10, '0') || '-R', ob.order_id, ob.order_ts + INTERVAL 1 DAY, 'refund', {money('-ob.refund')}, ob.currency, ob.payment_method
        FROM ob WHERE ob.refund > 0
    """)
    con.execute("ALTER TABLE order_items DROP COLUMN i")
    con.execute("ALTER TABLE order_items RENAME COLUMN unit_cents TO unit_price_cents")


def _write(con: duckdb.DuckDBPyConnection, cfg: GeneratorConfig, out_dir: Path) -> Manifest:
    keep = {"restaurants": "restaurant_id, restaurant_name, cuisine, city_id", "couriers": "courier_id, courier_phone, vehicle, city_id",
            "customers": "customer_id, customer_email, signup_date", "orders": "*", "order_items": "*", "payments": "*", "orders_flat": "*"}
    order_by = {"restaurants": "restaurant_id", "couriers": "courier_id", "customers": "customer_id", "orders": "row_id",
                "order_items": "order_id, line_no", "payments": "payment_id", "orders_flat": "row_id"}
    tables: dict[str, dict] = {}
    for name, cols in keep.items():
        path = out_dir / f"{name}.parquet"
        con.execute(f"COPY (SELECT {cols} FROM {name} ORDER BY {order_by[name]}) TO '{path.as_posix()}' (FORMAT PARQUET)")
        tables[name] = table_digest(con, read_parquet(path))
    body = {
        "synthetic": True,
        "notice": f"{SYNTHETIC}: generated, not real. Not customer data and not data from any named company.",
        "generator_version": GENERATOR_VERSION, "config_digest": cfg.digest(), "seed": cfg.seed, "tier": cfg.tier, "n_orders": cfg.n_orders,
        "duckdb_version": duckdb.__version__, "start_date": cfg.start_date, "days": cfg.days,
        "rates": cfg.rates.model_dump(mode="json"), "rates_note": "Initial values are plausible guesses by the authors, not measured from any real platform.",
        "defects": {d: {"enabled": cfg.on(d), "evidence": EVIDENCE[d], "description": DEFECT_TEXT[d]} for d in DEFECTS},
        "tables": tables, "logical_digest": logical_digest(tables),
        "table_comments": {t: f"{SYNTHETIC}: {t}" for t in TABLES}, "schema_name": "compass_synthetic_food_delivery",
    }
    (out_dir / "manifest.json").write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    return Manifest(body)


__all__ = ["Manifest", "TABLES", "generate", "sql_list"]
