"""The offline registration printer and verifier (approval C, preparation only). Prints text; nothing is run or granted."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.connect import registration_print as rp
from app.connect.databricks_registration import Step
from app.connect.grants import ScopeError

CAT, SCH = rp.DEFAULT_CATALOG, rp.DEFAULT_SCHEMA
TABLES = list(rp.SYNTHETIC_TABLES)
APP = "<APPLICATION_ID>"


def script(tables=TABLES):
    return rp.build(CAT, SCH, tables, suffix="0a1b2c3d", warehouse="Serverless Starter Warehouse")


def sql(steps):
    return [s.arg for s in steps if s.op == "sql"]


def test_the_default_list_is_the_seven_synthetic_tables():
    pytest.importorskip("duckdb")
    from reference_stack.synthetic.food_delivery import TABLES as GENERATED

    assert tuple(GENERATED) == rp.SYNTHETIC_TABLES and len(rp.SYNTHETIC_TABLES) == 7


def test_setup_statements_are_exact_and_scoped():
    s = script()
    who = f"`{APP}`"
    expected = [f"GRANT USE CATALOG ON CATALOG `dev` TO {who}", f"GRANT USE SCHEMA ON SCHEMA `dev`.`{SCH}` TO {who}",
                *(f"GRANT SELECT ON TABLE `dev`.`{SCH}`.`{t}` TO {who}" for t in sorted(TABLES))]
    assert sql(s.steps) == expected and len(expected) == 9
    assert [x.op for x in s.steps][:3] == ["create_principal", "create_secret", "warehouse_can_use"]
    assert "CAN_USE" in s.steps[2].text and "Serverless Starter Warehouse" in s.steps[2].text
    joined = "\n".join(x.text for x in s.steps).upper()
    assert not any(w in joined for w in ("ALL TABLES", "ALL PRIVILEGES", "GRANT OPTION", "FUTURE", "MODIFY", "MANAGE", "*", "%"))
    assert s.principal == "compass-conn-0a1b2c3d" and s.principal in s.steps[0].text  # a per-run principal


def test_teardown_reverses_exactly_what_was_created():
    s = script()
    who = f"`{APP}`"
    assert sql(s.teardown) == [*(f"REVOKE SELECT ON TABLE `dev`.`{SCH}`.`{t}` FROM {who}" for t in sorted(TABLES)),
                               f"REVOKE USE SCHEMA ON SCHEMA `dev`.`{SCH}` FROM {who}", f"REVOKE USE CATALOG ON CATALOG `dev` FROM {who}"]
    assert [x.op for x in s.teardown if x.op != "sql"] == ["delete_principal"] and s.principal in s.teardown[-1].text


def test_every_run_has_its_own_principal():
    a = rp.build(CAT, SCH, TABLES, suffix="0a1b2c3d", warehouse="w")
    b = rp.build(CAT, SCH, TABLES, suffix="ffffffff", warehouse="w")
    assert a.principal != b.principal
    with pytest.raises(ScopeError):
        rp.build(CAT, SCH, TABLES, suffix="not-hex", warehouse="w")


def test_orders_current_is_added_only_when_asked(capsys):
    assert rp.main(["--warehouse", "w", "--suffix", "0a1b2c3d"]) == 0
    assert "orders_current" not in capsys.readouterr().out
    assert rp.main(["--warehouse", "w", "--suffix", "0a1b2c3d", "--include-orders-current"]) == 0
    out = capsys.readouterr().out
    assert f"`{SCH}`.`orders_current`" in out and out.count("SELECT ON TABLE") == 16  # 8 grants and 8 revokes


def test_the_printed_text_is_what_the_verifier_accepts(capsys):
    assert rp.main(["--warehouse", "w", "--suffix", "0a1b2c3d"]) == 0
    out = capsys.readouterr().out
    assert "SETUP" in out and "TEARDOWN" in out
    assert rp.verify_script(script(), CAT, SCH, TABLES) == []


@pytest.mark.parametrize("bad", ["*", "orders*", "ALL TABLES", "or%ders", "a`b", "a b", "a.b", "", "x;DROP"])
def test_a_wildcard_or_unplain_table_name_is_refused(bad):
    with pytest.raises(ScopeError):
        rp.build(CAT, SCH, [*TABLES, bad], suffix="0a1b2c3d", warehouse="w")


def test_an_empty_list_and_a_wildcard_schema_or_catalog_are_refused():
    for args in ((CAT, SCH, []), ("*", SCH, TABLES), (CAT, "%", TABLES)):
        with pytest.raises(ScopeError):
            rp.build(*args, suffix="0a1b2c3d", warehouse="w")


def test_the_command_refuses_a_wildcard_and_prints_nothing_to_run(capsys):
    assert rp.main(["--tables", "orders", "*", "--warehouse", "w", "--suffix", "0a1b2c3d"]) == 2
    seen = capsys.readouterr()
    assert not seen.out and "refused" in seen.err


def test_the_verifier_refuses_wildcards_broad_grants_and_grant_option():
    good = sql(script().steps)
    for extra in (f"GRANT SELECT ON TABLE `dev`.`{SCH}`.`*` TO `{APP}`", f"GRANT SELECT ON ALL TABLES IN SCHEMA `dev`.`{SCH}` TO `{APP}`",
                  f"GRANT ALL PRIVILEGES ON SCHEMA `dev`.`{SCH}` TO `{APP}`", f"GRANT SELECT ON TABLE `dev`.`{SCH}`.`orders` TO `{APP}` WITH GRANT OPTION",
                  f"GRANT SELECT ON FUTURE TABLES IN SCHEMA `dev`.`{SCH}` TO `{APP}`"):
        problems = rp.verify_statements([*good, extra], CAT, SCH, TABLES)
        assert problems and problems[0].startswith("refused"), extra


def test_the_verifier_refuses_a_table_outside_the_list_and_a_missing_one():
    good = sql(script().steps)
    extra = f"GRANT SELECT ON TABLE `dev`.`{SCH}`.`orders_current` TO `{APP}`"
    assert rp.verify_statements([*good, extra], CAT, SCH, TABLES) == [f"outside the list: {extra}"]
    assert rp.verify_statements(good[:-1], CAT, SCH, TABLES)[0].startswith("missing")
    other = f"GRANT SELECT ON TABLE `dev`.`other_schema`.`orders` TO `{APP}`"
    assert any("outside the list" in p for p in rp.verify_statements([*good, other], CAT, SCH, TABLES))
    assert rp.verify_statements(good, CAT, SCH, TABLES) == []


def test_the_verifier_refuses_a_tampered_script():
    s = script()
    assert rp.verify_statements([*sql(s.teardown), f"REVOKE SELECT ON TABLE `dev`.`{SCH}`.`extra` FROM `{APP}`"], CAT, SCH, TABLES, revoke=True)
    stmt = "GRANT SELECT ON TABLE `dev`.`x`.`y` TO `p`"
    tampered = replace(s, steps=(*s.steps, Step("sql", stmt, stmt), Step("grant_admin", "make admin")))
    problems = rp.verify_script(tampered, CAT, SCH, TABLES)
    assert any("outside the list" in p for p in problems) and any("unexpected setup step" in p for p in problems)
