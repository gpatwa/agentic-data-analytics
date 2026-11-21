"""G5 loaders, offline: plans stay inside one schema, the verification SQL's logic matches the answer keys, the executor stops where it must, and Online Retail II converts exactly."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import duckdb
import pytest
import sqlglot
from reference_stack.loaders import online_retail as orl
from reference_stack.loaders import verify
from reference_stack.loaders.__main__ import main as cli
from reference_stack.loaders.engines import DIALECTS, DUCKDB, PlanError, dialect
from reference_stack.loaders.plan import ALLOWED_VERBS, Check, LoadPlan, Step, execute, guard, render_dry_run
from reference_stack.loaders.synthetic import synthetic_plan
from reference_stack.synthetic.config import GeneratorConfig
from reference_stack.synthetic.food_delivery import generate


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("loader_synth")
    generate(GeneratorConfig(tier="T"), out)
    return out


ENGINES = ("databricks", "snowflake")


# ---- plans ------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_every_statement_is_an_allowed_verb_inside_the_one_schema_and_nothing_drops(data, engine):
    plan = synthetic_plan(data, engine, "cmp_ref", "synth_fd")
    sql_steps = [s for s in plan.steps if s.kind == "sql"]
    assert sql_steps and all(any(s.text.upper().startswith(v) for v in ALLOWED_VERBS) for s in sql_steps)
    for s in sql_steps:
        guard(plan, s.text)
    assert not [s for s in plan.steps if "DROP" in s.text.upper() or "DELETE" in s.text.upper() or "ALTER" in s.text.upper() or "TRUNCATE" in s.text.upper()]
    assert len(plan.cleanup) == 1 and plan.cleanup[0].startswith("DROP SCHEMA") and "CASCADE" in plan.cleanup[0] and "synth_fd" in plan.cleanup[0].lower()


@pytest.mark.parametrize("engine", ENGINES)
def test_the_guard_refuses_anything_outside_the_schema_or_the_allowed_verbs(data, engine):
    plan = synthetic_plan(data, engine, "cmp_ref", "synth_fd")
    d = dialect(engine)
    for bad in (f"DROP TABLE {d.qn('cmp_ref', 'synth_fd', 'orders')}", f"CREATE TABLE {d.qn('cmp_ref', 'other', 'orders')} (a INT)",
                f"CREATE TABLE {d.qn('another', 'synth_fd', 'orders')} (a INT)", f"INSERT INTO {d.qn('cmp_ref', 'synth_fd', 'orders')} VALUES (1)",
                "CREATE TABLE orders (a INT)", f"COMMENT ON TABLE {d.qn('cmp_ref', 'synth_fd', 'a')} IS 'x'; DROP SCHEMA {d.qn('cmp_ref', 'prod')}"):
        with pytest.raises(PlanError):
            guard(plan, bad)


def test_identifiers_are_checked():
    for bad in ("x; DROP", "a b", "", "1abc", "a" * 200):
        with pytest.raises(PlanError):
            synthetic_plan(Path("."), "databricks", bad, "s")  # fails on the identifier before reading anything
    with pytest.raises(PlanError):
        dialect("oracle")


def test_names_follow_each_engines_convention(data):
    dbx = render_dry_run(synthetic_plan(data, "databricks", "cmp_ref", "synth_fd"))
    sf = render_dry_run(synthetic_plan(data, "snowflake", "cmp_ref", "synth_fd"))
    assert "`cmp_ref`.`synth_fd`.`orders_flat`" in dbx and '"CMP_REF"."SYNTH_FD"."ORDERS_FLAT"' in sf
    assert "table_schema = 'SYNTH_FD'" in sf and "table_schema = 'synth_fd'" in dbx
    assert "CONSTRAINT `orders_flat_pk` PRIMARY KEY (`row_id`)" in dbx and 'PRIMARY KEY ("ROW_ID")' in sf and "`row_id` BIGINT NOT NULL" in dbx


def test_columns_are_typed_from_the_parquet_and_money_stays_exact(data):
    dbx = render_dry_run(synthetic_plan(data, "databricks", "c", "s"))
    sf = render_dry_run(synthetic_plan(data, "snowflake", "c", "s"))
    assert "`total_amount` DECIMAL(12,2)" in dbx and '"TOTAL_AMOUNT" DECIMAL(12,2)' in sf and '"ORDER_TS" TIMESTAMP_NTZ' in sf and "`order_ts` TIMESTAMP_NTZ" in dbx and "`order_ts` TIMESTAMP," not in dbx


def test_every_table_and_the_schema_carry_the_synthetic_label(data):
    text = render_dry_run(synthetic_plan(data, "databricks", "c", "s"))
    assert text.count("COMMENT ON TABLE") == 7 and all("IS 'SYNTHETIC DATA: " in line for line in text.splitlines() if line.startswith("COMMENT ON TABLE"))
    assert "COMMENT 'SYNTHETIC DATA" in text and "nothing was executed" in text


def test_a_directory_that_is_not_marked_synthetic_is_refused(tmp_path, data):
    for name in ("manifest.json", "truth.json"):
        (tmp_path / name).write_text((data / name).read_text())
    m = json.loads((tmp_path / "manifest.json").read_text())
    m["synthetic"] = False
    (tmp_path / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(PlanError):
        synthetic_plan(tmp_path, "databricks", "c", "s")


# ---- the verification SQL ------------------------------------------------------------------------------------------------------------------


def test_the_verification_logic_gives_the_answer_keys_values_when_run_on_duckdb(data):
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"CREATE VIEW orders_flat AS SELECT * FROM read_parquet('{(data / 'orders_flat.parquet').as_posix()}')")
    truth = json.loads((data / "truth.json").read_text())
    values = {k: int(con.execute(sql).fetchone()[0] or 0) for k, sql in verify.food_delivery_values(DUCKDB, "orders_flat").items()}
    values["net_value_usd_cents"] = values["gross_value_usd_cents"] - values["refunds_usd_cents"]
    assert values == truth["values"]
    for name, sql in verify.food_delivery_maps(DUCKDB, "orders_flat").items():
        assert {str(k): int(n) for k, n in con.execute(sql).fetchall()} == truth["maps"][name], name


@pytest.mark.parametrize("engine", ENGINES)
def test_every_generated_statement_parses_in_its_engines_dialect(data, engine):
    plan = synthetic_plan(data, engine, "cmp_ref", "synth_fd")
    texts = [c.sql for c in plan.checks if c.sql] + [plan.preflight] + [s.text for s in plan.steps if s.kind == "sql" and s.text.startswith(("CREATE TABLE", "COMMENT ON"))]
    assert len(texts) > 30
    for sql in texts:
        assert sqlglot.parse_one(sql, read=engine) is not None, sql[:80]


def test_the_dialects_differ_only_where_the_engines_differ():
    assert set(DIALECTS) == {"databricks", "snowflake", "duckdb"}
    assert "convert_timezone" in dialect("databricks").local("a", "b") and "CONVERT_TIMEZONE" in dialect("snowflake").local("a", "b")


# ---- the executor ---------------------------------------------------------------------------------------------------------------------------


class Fake:
    def __init__(self, plan, *, tables=0, fail_at=None, wrong=None):
        self.answers = {c.sql: c.expected for c in plan.checks if c.sql}
        self.tables, self.fail_at, self.wrong, self.ran, self.queries = tables, fail_at, wrong, [], []

    def run(self, sql):
        if self.fail_at and self.fail_at in sql:
            raise RuntimeError("PERMISSION_DENIED for customer 4417")
        self.ran.append(sql)

    def query(self, sql):
        self.queries.append(sql)
        if "information_schema" in sql:
            return [(self.tables,)]
        got = self.answers[sql]
        if self.wrong and self.wrong in sql:
            got = {k: v + 1 for k, v in got.items()} if isinstance(got, dict) else got + 1
        return list(got.items()) if isinstance(got, dict) else [(got,)]


@pytest.mark.parametrize("engine", ENGINES)
def test_a_clean_run_executes_every_step_in_order_and_every_check_passes(data, engine):
    plan = synthetic_plan(data, engine, "cmp_ref", "synth_fd")
    fake, uploads = Fake(plan), []
    report = execute(plan, fake, lambda local, remote: uploads.append(remote))
    assert report.ok and report.stopped == "" and not report.failures
    assert fake.ran[0].startswith("CREATE SCHEMA") and sum(s.startswith("CREATE TABLE") for s in fake.ran) == 7
    assert all(not r.upper().startswith(("DROP", "DELETE", "INSERT")) for r in fake.ran)
    assert (len(uploads) == 7 and all(u.startswith("/Volumes/cmp_ref/synth_fd/staging/") for u in uploads)) if engine == "databricks" else not uploads
    assert "check:net_value_usd_cents" in report.done and "check:orders_by_city" in report.done


def test_a_schema_that_is_not_empty_stops_before_anything_runs(data):
    plan = synthetic_plan(data, "databricks", "cmp_ref", "synth_fd")
    fake = Fake(plan, tables=3)
    report = execute(plan, fake, lambda *a: None)
    assert report.stopped == "schema_not_empty" and fake.ran == []


def test_a_failed_statement_stops_the_run_and_leaks_no_driver_message(data):
    plan = synthetic_plan(data, "databricks", "cmp_ref", "synth_fd")
    fake = Fake(plan, fail_at="CREATE TABLE `cmp_ref`.`synth_fd`.`couriers`")
    report = execute(plan, fake, lambda *a: None)
    assert report.stopped.startswith("step_") and report.stopped.endswith("failed:RuntimeError") and "4417" not in report.stopped
    assert not any("customers" in r for r in fake.ran) and fake.queries == [plan.preflight]  # no later table and no check ran


def test_a_count_or_value_that_differs_is_reported_not_smoothed_over(data):
    plan = synthetic_plan(data, "databricks", "cmp_ref", "synth_fd")
    report = execute(plan, Fake(plan, wrong="COUNT(*) FROM `cmp_ref`.`synth_fd`.`payments`"), lambda *a: None)
    assert not report.ok and report.failures == ["rows:payments:mismatch"]
    wrong_value = execute(plan, Fake(plan, wrong="'refunded'"), lambda *a: None)
    assert "refunded_orders:mismatch" in wrong_value.failures
    wrong_map = execute(plan, Fake(plan, wrong="GROUP BY city"), lambda *a: None)
    assert "orders_by_city:mismatch" in wrong_map.failures


def test_a_statement_outside_the_schema_or_an_upload_outside_the_volume_is_refused_at_run_time(data):
    plan = synthetic_plan(data, "databricks", "cmp_ref", "synth_fd")
    evil = LoadPlan(plan.dialect, plan.catalog, plan.schema, plan.preflight, (Step("sql", "CREATE TABLE `cmp_ref`.`prod`.`t` (a INT)"),), (), ())
    assert execute(evil, Fake(plan), lambda *a: None).stopped == "step_1_refused:statement_outside_schema"
    up = LoadPlan(plan.dialect, plan.catalog, plan.schema, plan.preflight, (Step("upload", "x", "a", "/Volumes/other/s/x.parquet"),), (), ())
    assert execute(up, Fake(plan), lambda *a: None).stopped == "step_1_refused:upload_outside_schema"
    trav = LoadPlan(plan.dialect, plan.catalog, plan.schema, plan.preflight, (Step("upload", "x", "a", "/Volumes/cmp_ref/synth_fd/../prod/x"),), (), ())
    assert execute(trav, Fake(plan), lambda *a: None).stopped == "step_1_refused:upload_outside_schema"
    assert execute(up, Fake(plan), None).stopped.startswith("step_1_refused")


def test_the_derived_net_value_check_fails_when_its_inputs_differ(data):
    plan = synthetic_plan(data, "databricks", "cmp_ref", "synth_fd")
    refunds = "FROM `cmp_ref`.`synth_fd`.`orders_flat` WHERE is_reversal AND currency = 'USD'"
    report = execute(plan, Fake(plan, wrong=refunds), lambda *a: None)
    assert "refunds_usd_cents:mismatch" in report.failures and "net_value_usd_cents:mismatch" in report.failures


def test_nothing_in_the_loaders_connects_to_anything():
    text = " ".join(p.read_text() for p in (Path(__file__).resolve().parent.parent / "reference_stack/loaders").glob("*.py"))
    assert not re.search(r"\b(requests|urllib|httpx|snowflake\.connector|databricks\.sql|databricks\.sdk|sqlalchemy|complete_json)\b", text)


# ---- the command line ----------------------------------------------------------------------------------------------------------------


def test_the_command_line_prints_a_plan_and_refuses_to_execute_without_approval(data, capsys):
    args = ["synthetic", "--data", str(data), "--engine", "snowflake", "--catalog", "c", "--schema", "s"]
    assert cli([*args, "--dry-run"]) == 0 and "DRY RUN (snowflake)" in capsys.readouterr().out
    with pytest.raises(SystemExit):  # neither --dry-run nor --execute
        cli(args)
    capsys.readouterr()
    assert cli([*args, "--execute"]) == 2 and "needs --engine databricks" in capsys.readouterr().err  # snowflake, and no approval named
    dbx = ["synthetic", "--data", str(data), "--engine", "databricks", "--catalog", "c", "--schema", "s", "--execute", "--profile", "p", "--host", "h", "--warehouse-name", "w", "--approval-id", "nope"]
    assert cli(dbx) == 2 and "approval_not_recorded" in capsys.readouterr().err
    assert cli(["synthetic", "--data", "/nonexistent", "--engine", "databricks", "--catalog", "c", "--schema", "s", "--dry-run"]) == 2


# ---- Online Retail II -----------------------------------------------------------------------------------------------------------------------


def workbook(path, rows1, rows2, header=orl.HEADER):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    for ws, rows, name in ((wb.active, rows1, "Year 2009-2010"), (wb.create_sheet(), rows2, "Year 2010-2011")):
        ws.title = name
        ws.append(header)
        for r in rows:
            ws.append(r)
    wb.save(path)


ROWS1 = [
    ["489434", "85048", "15CM CHRISTMAS GLASS BALL 20 LIGHTS", 12, datetime(2009, 12, 1, 7, 45), 6.95, 13085.0, "United Kingdom"],
    ["489434", "22350", None, 24, datetime(2009, 12, 1, 7, 45), 0.42, 13085.0, "United Kingdom"],
    ["C489449", "22087", "PAPER BUNTING WHITE LACE", -12, datetime(2009, 12, 1, 10, 33), 2.55, 16321.0, "Australia"],
]
ROWS2 = [
    ["536365", "85123A", 'WHITE "HANGING" HEART, T-LIGHT HOLDER', 6, datetime(2010, 12, 1, 8, 26), 2.55, None, "United Kingdom"],
    ["C536379", "D", "Discount", -1, datetime(2010, 12, 1, 9, 41), 27.5, 14527, "United Kingdom"],
    ["536366", "22633", "HAND WARMER UNION JACK", 6, datetime(2010, 12, 1, 8, 28), 1.85, 17850, "United Kingdom"],
]


@pytest.fixture()
def retail(tmp_path):
    xlsx = tmp_path / "in" / "retail.xlsx"
    xlsx.parent.mkdir()
    workbook(xlsx, ROWS1, ROWS2)
    out = tmp_path / "out"
    return orl.convert(xlsx, out), out


def test_the_workbook_converts_to_typed_parquet_with_the_added_columns(retail):
    manifest, out = retail
    con = duckdb.connect()
    p = (out / "online_retail_ii.parquet").as_posix()
    types = dict(con.execute(f"SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet('{p}'))").fetchall())
    assert types["row_id"] == "BIGINT" and types["unit_price"] == "DECIMAL(12,4)" and types["line_amount"] == "DECIMAL(16,4)" and types["is_cancellation"] == "BOOLEAN"
    rows = con.execute(f"SELECT row_id, invoice_no, description, quantity, unit_price, customer_id, line_amount, is_cancellation, source_sheet FROM read_parquet('{p}') ORDER BY row_id").fetchall()
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5, 6] and rows[1][2] is None and rows[3][5] is None
    assert rows[0][5] == "13085" and rows[4][5] == "14527" and str(rows[0][6]) == "83.4000" and str(rows[2][6]) == "-30.6000"
    assert [r[7] for r in rows] == [False, False, True, False, True, False] and rows[3][2] == 'WHITE "HANGING" HEART, T-LIGHT HOLDER' and rows[5][8] == "Year 2010-2011"
    assert manifest["rows"] == 6 and manifest["disagreements"] == [] and "CC BY 4.0" in manifest["attribution"]


def test_the_ledger_and_the_recomputation_agree_and_match_hand_calculation(retail):
    _, out = retail
    truth = json.loads((out / "oracle.json").read_text())
    v = truth["values"]
    assert v["line_count"] == 6 and v["invoice_count"] == 5 and v["customer_count"] == 4 and v["lines_without_customer"] == 1 and v["cancellation_lines"] == 2
    assert v["sales_lines_value_cents"] == 8340 + 1008 + 1530 + 1110 and v["cancellation_value_cents"] == -(3060 + 2750)
    assert v["naive_net_value_cents"] == v["sales_lines_value_cents"] + v["cancellation_value_cents"] and v["quantity_total"] == 12 + 24 - 12 + 6 - 1 + 6
    assert truth["maps"]["lines_by_country"] == {"United Kingdom": 5, "Australia": 1}
    assert orl.recompute(out / "online_retail_ii.parquet") == truth


def test_a_changed_header_or_an_unexpected_price_precision_is_refused(tmp_path):
    bad = tmp_path / "bad.xlsx"
    workbook(bad, ROWS1, ROWS2, header=["Invoice", "Code", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"])
    with pytest.raises(PlanError, match="unexpected_header"):
        orl.convert(bad, tmp_path / "o1")
    odd = tmp_path / "odd.xlsx"
    workbook(odd, [["1", "x", "d", 1, datetime(2010, 1, 1), 0.123456, 1.0, "UK"]], [])
    with pytest.raises(PlanError, match="price_scale"):
        orl.convert(odd, tmp_path / "o2")


def test_a_missing_required_value_is_refused_not_guessed(tmp_path):
    bad = tmp_path / "m.xlsx"
    workbook(bad, [["1", "x", "d", None, datetime(2010, 1, 1), 1.0, 1.0, "UK"]], [])
    with pytest.raises(PlanError, match="missing_required_value"):
        orl.convert(bad, tmp_path / "o")


@pytest.mark.parametrize("engine", ENGINES)
def test_the_retail_plan_carries_the_attribution_and_passes_the_guard(retail, engine):
    _, out = retail
    plan = orl.retail_plan(out, engine, "cmp_ref", "retail_ref")
    text = render_dry_run(plan)
    assert "doi.org/10.24432/C5CG6D" in text and "CC BY 4.0" in text and text.count("COMMENT ON TABLE") == 1
    for s in plan.steps:
        if s.kind == "sql":
            guard(plan, s.text)
    for c in plan.checks:
        if c.sql:
            assert sqlglot.parse_one(c.sql, read=engine) is not None
    report = execute(plan, Fake(plan), lambda *a: None)
    assert report.ok


def test_a_converted_set_with_a_disagreement_or_the_wrong_licence_is_not_planned(retail):
    _, out = retail
    m = json.loads((out / "manifest.json").read_text())
    (out / "manifest.json").write_text(json.dumps({**m, "disagreements": ["line_count"]}))
    with pytest.raises(PlanError):
        orl.retail_plan(out, "databricks", "c", "s")
    (out / "manifest.json").write_text(json.dumps({**m, "licence": "unknown"}))
    with pytest.raises(PlanError):
        orl.retail_plan(out, "databricks", "c", "s")


def test_a_check_dataclass_carries_what_a_dry_run_prints():
    assert Check("a", "SELECT 1", 1).derived_from == ()


# ---- the curated view: what `order_count` should mean on this data ------------------------------------------------------------------------------


def test_the_curated_view_is_off_by_default_and_added_only_on_request(data):
    plain = synthetic_plan(data, "databricks", "cmp_ref", "synth_fd")
    assert not any("CREATE VIEW" in s.text for s in plain.steps) and not any("orders_current" in c.name for c in plain.checks)
    withv = synthetic_plan(data, "databricks", "cmp_ref", "synth_fd", with_curated_view=True)
    assert sum("CREATE VIEW" in s.text for s in withv.steps) == 1 and withv.steps[-1].text.startswith("CREATE VIEW")
    assert len(withv.checks) == len(plain.checks) + 6


@pytest.mark.parametrize("engine", ENGINES)
def test_the_view_statement_is_guarded_and_parses_in_its_dialect(data, engine):
    from reference_stack.loaders.plan import guard

    plan = synthetic_plan(data, engine, "cmp_ref", "synth_fd", with_curated_view=True)
    view = plan.steps[-1]
    guard(plan, view.text)  # an allowed verb, inside the one schema
    assert sqlglot.parse_one(view.text, read=engine) is not None
    for c in plan.checks:
        if "orders_current" in c.name.lower() or "ORDERS_CURRENT" in c.sql:
            assert sqlglot.parse_one(c.sql, read=engine) is not None


def test_the_view_gives_one_row_per_real_order_and_matches_the_answer_key_on_duckdb(data):
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS cat")
    con.execute("CREATE SCHEMA cat.sch")
    con.execute(f"CREATE VIEW cat.sch.orders_flat AS SELECT * FROM read_parquet('{(data / 'orders_flat.parquet').as_posix()}')")
    plan = synthetic_plan(data, "duckdb", "cat", "sch", with_curated_view=True)
    sql = plan.steps[-1].text.replace(" COMMENT = 'SYNTHETIC DATA: one row per real order, latest version, no reversals, no test orders'", "")
    con.execute(sql)
    checks = [c for c in plan.checks if "orders_current" in c.name]
    assert len(checks) == 6
    for c in checks:
        assert int(con.execute(c.sql).fetchone()[0]) == c.expected, c.name
    naive = con.execute("SELECT COUNT(*) FROM cat.sch.orders_flat").fetchone()[0]
    current = con.execute("SELECT COUNT(*) FROM cat.sch.orders_current").fetchone()[0]
    assert current < naive  # the raw table over-counts orders: duplicates, reversal rows and late versions


def test_executing_with_the_view_needs_an_approval_that_names_it(tmp_path):
    from reference_stack.loaders.approval import ApprovalError, check_approval

    f = tmp_path / "p.yaml"
    f.write_text("a:\n  g:\n    status: approved\n    approved_by: user\n    scope: 'dev.sch on https://h with Wh'\n")
    check_approval("g", "https://h", "dev", "sch", "Wh", 15, f)  # the tables alone are approved
    with pytest.raises(ApprovalError):
        check_approval("g", "https://h", "dev", "sch", "Wh", 15, f, also=("orders_current",))


# ---- view-only: the view on a schema that already holds the tables ---------------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_the_view_only_plan_is_one_statement_six_checks_and_a_view_absent_preflight(data, engine):
    plan = synthetic_plan(data, engine, "cmp_ref", "synth_fd", view_only=True)
    assert len(plan.steps) == 1 and plan.steps[0].text.startswith("CREATE VIEW") and not any(s.kind == "upload" for s in plan.steps)
    assert len(plan.checks) == 6 and 1 + len(plan.steps) + len(plan.checks) == 8  # the preflight, the CREATE VIEW and six checks: the approved statement cap
    assert "table_name" in plan.preflight.lower() and "orders_current" in plan.preflight.lower()  # the view must be ABSENT, the schema is not empty
    assert plan.cleanup == (f"DROP VIEW {plan.dialect.qn('cmp_ref', 'synth_fd', 'orders_current')}",)
    assert "CREATE TABLE" not in " ".join(s.text for s in plan.steps)


def test_a_view_only_run_executes_eight_statements_and_creates_nothing_else(data):
    plan = synthetic_plan(data, "databricks", "cmp_ref", "synth_fd", view_only=True)
    fake = Fake(plan)
    report = execute(plan, fake)
    assert report.ok and len(fake.ran) == 1 and fake.ran[0].startswith("CREATE VIEW") and len(fake.queries) == 7  # preflight and six checks
    assert not any(r.upper().startswith(("DROP", "DELETE", "INSERT", "CREATE TABLE", "CREATE SCHEMA", "GRANT")) for r in fake.ran)


def test_a_view_that_already_exists_stops_before_anything_runs(data):
    plan = synthetic_plan(data, "databricks", "cmp_ref", "synth_fd", view_only=True)
    fake = Fake(plan, tables=1)
    report = execute(plan, fake)
    assert report.stopped == "schema_not_empty" and fake.ran == []


def test_the_statement_cap_stops_the_session_on_the_ninth():
    import json as _json

    from reference_stack.loaders.databricks_cli import CapExceeded, DatabricksCliSession

    def ok(argv):
        return 0, _json.dumps({"statement_id": "s", "status": {"state": "SUCCEEDED"}, "result": {"data_array": [["0"]]}})

    session = DatabricksCliSession("p", "wh", 900, runner=ok, max_statements=8)
    for _ in range(8):
        session.query("SELECT 1")
    with pytest.raises(CapExceeded, match="statement_cap_reached"):
        session.query("SELECT 1")


# ---- the USD amount view: the currency rule the compiler cannot apply ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_the_usd_view_plan_is_one_statement_three_checks_and_guarded(data, engine):
    from reference_stack.loaders.plan import guard

    plan = synthetic_plan(data, engine, "cmp_ref", "synth_fd", usd_view_only=True)
    assert len(plan.steps) == 1 and plan.steps[0].text.startswith("CREATE VIEW") and len(plan.checks) == 3 and not any(s.kind == "upload" for s in plan.steps)
    guard(plan, plan.steps[0].text)
    assert sqlglot.parse_one(plan.steps[0].text, read=engine) is not None and "currency" in plan.steps[0].text.lower() and "'USD'" in plan.steps[0].text
    for c in plan.checks:
        assert sqlglot.parse_one(c.sql, read=engine) is not None


def test_the_usd_view_reproduces_the_answer_keys_gross_value_on_duckdb(data):
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS cat")
    con.execute("CREATE SCHEMA cat.sch")
    con.execute(f"CREATE VIEW cat.sch.orders_flat AS SELECT * FROM read_parquet('{(data / 'orders_flat.parquet').as_posix()}')")
    base = synthetic_plan(data, "duckdb", "cat", "sch", view_only=True)
    con.execute(base.steps[-1].text.replace(" COMMENT = 'SYNTHETIC DATA: one row per real order, latest version, no reversals, no test orders'", ""))
    plan = synthetic_plan(data, "duckdb", "cat", "sch", usd_view_only=True)
    con.execute(plan.steps[0].text.split(" COMMENT = ")[0] + " AS " + plan.steps[0].text.split(" AS ", 1)[1])
    for c in plan.checks:
        assert int(con.execute(c.sql).fetchone()[0]) == c.expected, c.name  # the key's gross USD value, unique orders, no cancelled


def test_the_usd_view_needs_an_approval_that_names_it(tmp_path):
    from reference_stack.loaders.approval import ApprovalError, check_approval

    f = tmp_path / "p.yaml"
    f.write_text("a:\n  g:\n    status: approved\n    approved_by: user\n    scope: 'dev.sch on https://h with Wh orders_current'\n")
    with pytest.raises(ApprovalError):
        check_approval("g", "https://h", "dev", "sch", "Wh", 15, f, also=("orders_usd_gross",))
