"""L4: the offline reader of existing metric views and tags, on a fake session. Named objects only, a failed read gives nothing, tags only tighten,
and the CLI runs nothing without --allow-live."""

from __future__ import annotations

import json

import pytest
from test_existing_assets import YAML, facts
from test_import_assets_cli import files

from app.ontology_draft import databricks_assets as da
from app.ontology_draft import existing_assets as ea
from app.ontology_draft.import_assets import main
from app.ontology_draft.proposal import DraftError

MV = "cat.sch.orders_mv"


class Stream:
    def __init__(self, rows):
        self.rows, self.closed = list(rows), False

    def fetchmany(self, n):
        out, self.rows = self.rows[:n], self.rows[n:]
        return out

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *, yaml_text=YAML, column_tags=(), table_tags=(), fail_on=None):
        self.log: list[str] = []
        self.yaml_text, self.column_tags, self.table_tags, self.fail_on = yaml_text, column_tags, table_tags, fail_on
        self.closed = False

    def execute(self, sql, parameters):
        self.log.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("PERMISSION_DENIED customer 4417")
        if sql.startswith("DESCRIBE TABLE EXTENDED"):
            return Stream([("id", "int", None), ("", "", ""), ("# Detailed Table Information", "", ""), ("View Text", self.yaml_text, "")])
        if "column_tags" in sql:
            return Stream(self.column_tags)
        return Stream(self.table_tags)

    def close(self):
        self.closed = True


def test_it_reads_the_yaml_of_exactly_the_named_view():
    s = Session()
    assert da.read_metric_view_yaml(s, MV) == YAML
    assert s.log == ["DESCRIBE TABLE EXTENDED `cat`.`sch`.`orders_mv`"]


@pytest.mark.parametrize("name", ["cat.sch.*", "cat.sch.%", "cat.sch", "cat..mv", "cat.sch.mv.x", "", "cat.sch.mv'; DROP", "*", "cat.sch.a`b"])
def test_only_a_three_part_identifier_is_ever_read_never_a_wildcard(name):
    s = Session()
    with pytest.raises(DraftError):
        da.read_metric_view_yaml(s, name)
    with pytest.raises(DraftError):
        da.read_tag_rows(s, name)
    assert s.log == []


def test_an_empty_list_or_a_huge_one_is_refused_and_no_statement_form_lists_or_wildcards():
    with pytest.raises(DraftError):
        da.named_objects([])
    with pytest.raises(DraftError):
        da.named_objects([MV] * (da.MAX_OBJECTS + 1))
    for sql in da.planned_statements([MV], ["cat.sch.orders"]):
        assert "SHOW" not in sql.upper() and "*" not in sql and "LIKE" not in sql.upper()
        assert sql.startswith("DESCRIBE TABLE EXTENDED `cat`.`sch`.`orders_mv`") or "schema_name = 'sch' AND table_name = 'orders'" in sql


def test_a_failed_or_empty_read_gives_nothing_and_no_driver_message():
    assert da.read_metric_view_yaml(Session(fail_on="DESCRIBE"), MV) is None
    assert da.read_tag_rows(Session(fail_on="table_tags"), "cat.sch.orders") is None  # a partial answer could drop a restriction
    s = Session()
    s.yaml_text = ""
    assert da.read_metric_view_yaml(s, MV) is None
    s.yaml_text = "x" * (da.MAX_YAML_CHARS + 1)
    assert da.read_metric_view_yaml(s, MV) is None


def test_tags_become_restrictions_and_only_tighten():
    rows = da.read_tag_rows(Session(column_tags=[("orders", "status", "class", "pii")], table_tags=[("orders", "owner", "team-a")]), "cat.sch.orders")
    tags = da.tags_by_column(facts(), rows)
    assert ("orders", "status") in ea.restricted_columns(tags)
    assert ("orders", "id") not in ea.restricted_columns(tags)  # an innocuous table tag restricts nothing
    already = ea.apply_restrictions(facts(restricted=("status",)), ea.restricted_columns({}))
    assert next(c for c in already.tables[0].columns if c.name == "status").restricted  # no tags never clears a mark
    table_wide = da.tags_by_column(facts(), [("orders", None, "sensitivity", "confidential")])
    assert {c for _, c in ea.restricted_columns(table_wide)} == {"id", "amount", "status", "created_at", "customer_email"}
    assert da.tags_by_column(facts(), [("other", "x", "pii", "")]) == {("other", "x"): ("pii",)} and ea.apply_restrictions(
        facts(), ea.restricted_columns({("other", "x"): ("pii",)})) == facts()


# ---- the command --------------------------------------------------------------------------------------------------------------------


def read_args(tmp_path, *extra):
    base = files(tmp_path)
    i = base.index("--metric-view")
    return [*base[:i], *base[i + 2:], *extra]  # drop the saved-file option


def test_without_allow_live_it_prints_the_statements_and_runs_none(tmp_path, capsys):
    s = Session()
    code = main(read_args(tmp_path, "--read-metric-view", f"orders_mv={MV}", "--read-tags", "cat.sch.orders"), session_factory=lambda: s)
    out = capsys.readouterr().out
    assert code == 0 and s.log == [] and "DESCRIBE TABLE EXTENDED" in out and "column_tags" in out and "Nothing was run" in out
    assert not (tmp_path / "drafts").exists()


def test_allow_live_needs_a_session_factory_and_then_imports_like_a_saved_file(tmp_path, capsys):
    args = read_args(tmp_path, "--read-metric-view", f"orders_mv={MV}", "--allow-live")
    assert main(args) == 2 and "no session factory" in capsys.readouterr().err
    s = Session()
    assert main(args, session_factory=lambda: s) == 0 and s.closed
    assert len(s.log) == 1  # only the one named view
    assert len(list((tmp_path / "drafts").glob("orders-imported/*/contract.draft.json"))) == 1
    capsys.readouterr()


def test_a_live_restricting_tag_removes_the_column(tmp_path, capsys):
    s = Session(column_tags=[("orders", "status", "sensitivity", "pii")])
    args = read_args(tmp_path, "--read-metric-view", f"orders_mv={MV}", "--read-tags", "cat.sch.orders", "--allow-live")
    assert main(args, session_factory=lambda: s) == 0
    doc = json.loads(next((tmp_path / "drafts").glob("orders-imported/*/contract.draft.json")).read_text())
    assert "order_status" not in {d["id"] for d in doc["contract"]["dimensions"]}
    capsys.readouterr()


def test_a_failed_tag_read_refuses_and_writes_no_draft(tmp_path, capsys):
    s = Session(fail_on="column_tags")
    args = read_args(tmp_path, "--read-metric-view", f"orders_mv={MV}", "--read-tags", "cat.sch.orders", "--allow-live")
    assert main(args, session_factory=lambda: s) == 1
    assert "4417" not in capsys.readouterr().err and not (tmp_path / "drafts").exists() and s.closed


def test_a_failed_view_read_gives_nothing_to_import(tmp_path, capsys):
    s = Session(fail_on="DESCRIBE")
    args = read_args(tmp_path, "--read-metric-view", f"orders_mv={MV}", "--allow-live")
    assert main(args, session_factory=lambda: s) == 1
    captured = capsys.readouterr()
    assert "4417" not in captured.err and not (tmp_path / "drafts").exists()


def test_a_wildcard_name_is_refused_before_anything_runs(tmp_path, capsys):
    s = Session()
    assert main(read_args(tmp_path, "--read-metric-view", "mv=cat.sch.*", "--allow-live"), session_factory=lambda: s) == 2
    assert s.log == [] and "invalid_identifier" in capsys.readouterr().err
