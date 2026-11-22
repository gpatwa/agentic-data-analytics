"""Phase 2 replication, proved on DuckDB with a small tier: every answer in the answer key is exactly N times the source's, ids stay unique, and the guard lets a statement READ the source schema but never write it."""

from __future__ import annotations

import json
import re

import duckdb
import pytest
from reference_stack.loaders import replicate as rep
from reference_stack.loaders.engines import PlanError
from reference_stack.loaders.plan import DuckSession, execute, guard, render_dry_run
from reference_stack.synthetic.config import GeneratorConfig
from reference_stack.synthetic.food_delivery import generate

TABLES = ("restaurants", "couriers", "customers", "orders", "orders_flat", "order_items", "payments")


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("tier_t")
    generate(GeneratorConfig(tier="T"), out)
    return out


class Adapter(DuckSession):
    """DuckDB has no per-catalog information_schema preflight and no schema COMMENT: the preflight is answered as an empty schema, and the comment is dropped."""

    def run(self, sql):
        super().run(re.sub(r" COMMENT = '[^']*'", "", sql))

    def query(self, sql):
        return [(0,)] if "information_schema" in sql else super().query(sql)


def source_connection(data):
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS cat")
    con.execute("CREATE SCHEMA cat.src")
    for t in TABLES:
        con.execute(f"CREATE TABLE cat.src.{t} AS SELECT * FROM read_parquet('{(data / f'{t}.parquet').as_posix()}')")
    return con


@pytest.mark.parametrize("copies", [2, 3])
def test_the_replicated_schema_gives_exactly_n_times_every_answer_in_the_key(data, copies):
    con = source_connection(data)
    plan = rep.replication_plan(data, "duckdb", "cat", "src", "dst", copies)
    report = execute(plan, Adapter(con))
    assert not report.stopped and report.failures == [] and len(report.done) == len(plan.steps) + len(plan.checks)
    base = json.loads((data / "truth.json").read_text())["values"]
    assert con.execute("SELECT COUNT(*) FROM cat.dst.orders_flat").fetchone()[0] == copies * con.execute("SELECT COUNT(*) FROM cat.src.orders_flat").fetchone()[0]
    assert any(c.name == "orders_distinct" and c.expected == copies * base["orders_distinct"] for c in plan.checks)


def test_copy_zero_is_the_original_and_every_id_is_unique_with_the_test_restaurant_still_excluded(data):
    con = source_connection(data)
    execute(rep.replication_plan(data, "duckdb", "cat", "src", "dst", 3), Adapter(con))
    assert con.execute("SELECT COUNT(*) FROM cat.src.orders_flat s JOIN cat.dst.orders_flat d USING (row_id, order_id)").fetchone()[0] == con.execute("SELECT COUNT(*) FROM cat.src.orders_flat").fetchone()[0]
    for t, key in (("orders_flat", "row_id"), ("orders", "row_id"), ("payments", "payment_id")):
        assert con.execute(f"SELECT COUNT(*) - COUNT(DISTINCT {key}) FROM cat.dst.{t}").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) - COUNT(DISTINCT (order_id, line_no)) FROM cat.dst.order_items").fetchone()[0] == 0
    n_test = con.execute("SELECT COUNT(*) FROM cat.src.orders_flat WHERE restaurant_id = 'R000000'").fetchone()[0]
    assert n_test > 0 and con.execute("SELECT COUNT(*) FROM cat.dst.orders_flat WHERE restaurant_id = 'R000000'").fetchone()[0] == 3 * n_test
    assert con.execute("SELECT COUNT(*) FROM cat.dst.restaurants").fetchone()[0] == con.execute("SELECT COUNT(*) FROM cat.src.restaurants").fetchone()[0]  # dimensions are shared, not multiplied


def test_a_wrong_copy_count_in_the_answer_key_is_caught(data):
    con = source_connection(data)
    plan = rep.replication_plan(data, "duckdb", "cat", "src", "dst", 3)
    wrong = rep.replication_plan(data, "duckdb", "cat", "src", "dst", 2)
    report = execute(type(plan)(**{**plan.__dict__, "checks": wrong.checks}), Adapter(con))
    assert any(f.endswith(":mismatch") for f in report.failures)


def test_scaling_the_key_multiplies_values_maps_and_hidden_and_the_manifest_rows(data):
    truth = json.loads((data / "truth.json").read_text())
    scaled = rep.scale_truth(truth, 5)
    assert all(scaled["values"][k] == 5 * v for k, v in truth["values"].items())
    assert all(scaled["maps"][m][k] == 5 * v for m, g in truth["maps"].items() for k, v in g.items())
    manifest = json.loads((data / "manifest.json").read_text())
    sm = rep.scaled_manifest(manifest, 5)
    assert sm["tables"]["orders_flat"]["rows"] == 5 * manifest["tables"]["orders_flat"]["rows"] and sm["tables"]["restaurants"]["rows"] == manifest["tables"]["restaurants"]["rows"]
    assert "SYNTHETIC DATA" in sm["notice"] and "5 times" in sm["notice"]


def test_the_scaled_keys_directory_drives_the_existing_view_only_loader(data, tmp_path):
    from reference_stack.loaders.synthetic import synthetic_plan
    rep.write_scaled_keys(data, tmp_path / "x4", 4)
    plan = synthetic_plan(tmp_path / "x4", "databricks", "dev", "dst", view_only=True)
    base = json.loads((data / "truth.json").read_text())["values"]["orders_distinct"]
    assert any(c.expected == 4 * base for c in plan.checks)


def test_the_guard_lets_a_statement_read_the_source_schema_after_from_or_join_and_never_write_it(data):
    plan = rep.replication_plan(data, "databricks", "dev", "src", "dst", 2)
    for step in plan.steps:
        guard(plan, step.text)  # every planned statement passes
    for bad in ("CREATE TABLE `dev`.`src`.`x` AS SELECT 1", "CREATE TABLE `dev`.`dst`.`x` AS SELECT 1 /* `dev`.`src`.`t` */", "CREATE TABLE `dev`.`other`.`x` AS SELECT * FROM `dev`.`dst`.`t` f CROSS JOIN `dev`.`other`.`u`",
                "CREATE TABLE `dev`.`dst`.`x` AS SELECT * FROM `other`.`src`.`t`"):
        with pytest.raises(PlanError, match="statement_outside_schema"):
            guard(plan, bad)


def test_it_refuses_a_silly_copy_count_the_same_schema_and_unmarked_data(data, tmp_path):
    for n in (0, 1, 101):
        with pytest.raises(PlanError, match="copies_out_of_range"):
            rep.replication_plan(data, "duckdb", "cat", "src", "dst", n)
    with pytest.raises(PlanError, match="source_and_target_are_the_same_schema"):
        rep.replication_plan(data, "duckdb", "cat", "src", "SRC", 2)
    (tmp_path / "manifest.json").write_text(json.dumps({"synthetic": False, "notice": "", "tables": {}}))
    (tmp_path / "truth.json").write_text(json.dumps({"values": {}, "maps": {}}))
    with pytest.raises(PlanError, match="not_marked_synthetic"):
        rep.replication_plan(tmp_path, "duckdb", "cat", "src", "dst", 2)


def test_the_dry_run_names_the_statements_and_the_light_checks_are_a_subset(data):
    full = rep.replication_plan(data, "databricks", "dev", "src", "dst", 45)
    light = rep.replication_plan(data, "databricks", "dev", "src", "dst", 45, light_checks=True)
    assert len(light.checks) < len(full.checks) and {c.name for c in light.checks} <= {c.name for c in full.checks}
    text = render_dry_run(full)
    assert "CREATE TABLE `dev`.`dst`.`orders_flat` AS SELECT" in text and "(VALUES (0), (1)" in text and "DROP SCHEMA" in text and "flatters the warehouse" in text


def test_the_command_dry_runs_a_replication_and_execute_refuses_without_a_recorded_approval(data, capsys):
    from reference_stack.loaders.__main__ import main
    args = ["replicate", "--data", str(data), "--engine", "databricks", "--catalog", "dev", "--schema", "dst", "--source-schema", "src", "--copies", "45"]
    assert main([*args, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "CREATE TABLE `dev`.`dst`.`payments` AS SELECT" in out and "Target dev.dst" in out
    assert main(["replicate", "--data", str(data), "--engine", "databricks", "--catalog", "dev", "--schema", "dst", "--dry-run"]) == 2  # no source or copies
    assert main([*args, "--execute", "--profile", "p", "--host", "https://h", "--warehouse-name", "Wh", "--approval-id", "no_such_approval"]) == 2  # no recorded approval: nothing runs
