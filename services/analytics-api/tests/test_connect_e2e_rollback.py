"""The rollback check's offline pre-work: the one-shot fault, the decoy, the generated statements and their guards.

Nothing here touches an account. The fake keeps state, so these tests show what the live check will look for.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests.test_connect_e2e_harness import LOCATOR, SUFFIX, P, auth, drive, harness, sign_in
from tests.test_connect_product_run import FakeConn, Refusal

from app.connect.e2e import statements as st
from app.connect.e2e.harness import SETUP_STATEMENTS, OneShotFault, main

ROOT = Path(__file__).resolve().parents[3]
APP = Path(__file__).resolve().parents[1] / "app"


def run_it(client, plan):
    sign_in(client, plan.json())
    return client.post(f"{P}/setups/{plan.json()['setup_id']}/run", headers=auth()).json()


def planned_user(plan) -> str:
    return re.search(r"CREATE USER (COMPASS_SVC_[0-9A-F]{8})", "\n".join(plan.json()["statements"]))[1]


# ---- the fault -------------------------------------------------------------------------------------------------------------------


def test_the_fault_counts_from_the_setups_first_statement_and_fires_once():
    fault = OneShotFault(3)
    for before in ("SELECT CURRENT_ACCOUNT()", "SHOW GRANTS TO ROLE X", "SHOW ROLES LIKE 'COMPASS_RO_1'"):
        fault.check(before)  # the identity check and the preflight come first and are never counted
    fault.check("CREATE ROLE COMPASS_RO_1 COMMENT = 'x'")
    fault.check("CREATE USER COMPASS_SVC_1")
    with pytest.raises(RuntimeError):
        fault.check("ALTER USER COMPASS_SVC_1 SET RSA_PUBLIC_KEY = 'k'")
    assert fault.fired
    for after in ("GRANT USAGE ON DATABASE D TO ROLE R", "DROP USER COMPASS_SVC_1", "DROP ROLE COMPASS_RO_1"):
        fault.check(after)  # one shot: the rollback's own statements are never touched


@pytest.mark.parametrize("step", [0, SETUP_STATEMENTS + 1, -1])
def test_the_fault_refuses_a_step_outside_the_setup(step):
    with pytest.raises(ValueError):
        OneShotFault(step)


def test_the_command_line_refuses_a_bad_step_and_still_needs_the_live_flag():
    base = ["--expect-account", LOCATOR, "--suffix", SUFFIX, "--egress", "35.238.115.103/32"]
    assert main([*base, "--allow-live", "--inject-failure-on-step", "9"]) == 2
    assert main([*base, "--allow-live", "--inject-failure-on-step", "0"]) == 2
    assert main([*base, "--inject-failure-on-step", "8"]) == 2  # no --allow-live


def test_a_run_without_the_fault_is_unaffected_and_the_report_says_so():
    built, client, account, driver = harness()
    script, registered, plan = drive(client)
    assert run_it(client, plan)["ok"] is True
    assert client.get("/__harness/report").json()["fault"] is None


# ---- R1: an injected failure, then a real rollback ---------------------------------------------------------------------------------


def test_an_injected_failure_after_seven_real_statements_rolls_back_exactly_what_was_created():
    built, client, account, driver = harness(inject_failure_step=8)
    script, registered, plan = drive(client)
    run = run_it(client, plan)
    assert run["ok"] is False and run["code"] == "setup_step_8:RuntimeError" and run["connection_status"] == "removed"
    assert run["created"] == ["role", "user"] and run["leftovers"] == []
    assert account.objects == {account.names.warehouse, account.names.monitor}  # only the pre-created warehouse and monitor remain
    ran = [s for s in account.executed if s.startswith(("CREATE", "ALTER", "GRANT", "DROP"))]
    assert [s.split()[0] for s in ran] == ["CREATE", "CREATE", "ALTER", "GRANT", "GRANT", "GRANT", "GRANT", "DROP", "DROP"]
    assert not any(s.startswith("GRANT ROLE") for s in ran)  # the injected statement never reached the account
    assert ran[-2].startswith("DROP USER") and ran[-1].startswith("DROP ROLE")
    report = client.get("/__harness/report").json()
    assert [e["type"] for e in report["audit_events"]] == [
        "connect.consent_shown", "connect.setup_approved", "connect.torn_down", "connect.setup_failed", "connect.credential_deleted"]
    assert report["fault"] == {"step": 8, "fired": True}
    assert report["leaks"]["clean"] is True and report["audit_chain_verifies"] is True


def test_the_fault_is_one_shot_so_a_second_connection_is_not_touched():
    built, client, account, driver = harness(inject_failure_step=8)
    script, registered, plan = drive(client)
    assert run_it(client, plan)["ok"] is False
    second = client.post(f"{P}/setups", json={"registration_id": plan.json()["registration_id"], "tables": [{"schema": "DATA", "table": "customers"}]}, headers=auth())
    assert run_it(client, second)["ok"] is True


def test_an_early_injected_failure_drops_only_the_role_that_exists():
    built, client, account, driver = harness(inject_failure_step=2)  # CREATE USER raises: only the role was created
    script, registered, plan = drive(client)
    run = run_it(client, plan)
    assert run["ok"] is False and run["created"] == ["role"] and run["leftovers"] == []
    assert not any(s.startswith("DROP USER") for s in account.executed)
    assert account.objects == {account.names.warehouse, account.names.monitor}


# ---- R2: a decoy with the planned user's name ------------------------------------------------------------------------------------


class DecoyConn(FakeConn):
    """The setup role as it might see an object it does not own: it cannot see the decoy in `SHOW USERS`, so the preflight passes,
    but `CREATE USER` collides with it, as Snowflake would."""

    def __init__(self, account, decoy, *, visible):
        super().__init__(account)
        self.decoy, self.visible = decoy, visible

    def execute(self, sql):
        if sql.startswith("SHOW USERS") and not self.visible and self.decoy in sql:
            self.a.executed.append(sql)
            self.description, self._rows = None, []
            return None
        if sql.startswith("CREATE USER") and sql.split()[2] == self.decoy:
            self.a.executed.append(sql)
            raise Refusal()
        return super().execute(sql)


def decoy_run(*, visible):
    built, client, account, driver = harness()
    script, registered, plan = drive(client)
    name = planned_user(plan)
    account.objects.add(name)  # the decoy, created by someone else before the run
    driver.connect = lambda **kwargs: DecoyConn(account, name, visible=visible)
    return built, client, account, name, run_it(client, plan)


def test_a_decoy_the_setup_role_cannot_see_fails_the_run_mid_way_and_the_rollback_spares_it():
    built, client, account, name, run = decoy_run(visible=False)
    assert run["ok"] is False and run["code"] == "setup_step_2:Refusal"
    assert run["created"] == ["role"] and run["leftovers"] == []
    assert name in account.objects  # the decoy survives: the rollback dropped only the role this run created
    assert not any(s.startswith("DROP USER") for s in account.executed)
    assert not any(o.startswith("COMPASS_RO_") for o in account.objects)
    assert client.get("/__harness/report").json()["leaks"]["clean"] is True


def test_a_decoy_the_setup_role_can_see_is_refused_by_the_preflight_before_anything_is_created():
    built, client, account, name, run = decoy_run(visible=True)
    assert run["ok"] is False and run["code"] == "name_taken" and run["created"] == []
    assert name in account.objects
    assert not any(s.startswith(("CREATE", "GRANT", "ALTER", "DROP")) for s in account.executed)


# ---- the generated statements ---------------------------------------------------------------------------------------------------


def test_the_decoy_is_a_plain_tagged_user_dropped_by_exact_name_only():
    create, read_back, drop = st.decoy("5ec0de01")
    assert create == f"CREATE USER COMPASS_SVC_5EC0DE01 COMMENT = '{st.DECOY_COMMENT}'"
    assert read_back == "SHOW USERS LIKE 'COMPASS_SVC_5EC0DE01'" and drop == "DROP USER IF EXISTS COMPASS_SVC_5EC0DE01"
    st.assert_no_lockout([create, read_back, drop])
    assert not re.search(r"PASSWORD|RSA_PUBLIC_KEY|NETWORK_POLICY|\bGRANT\b|\bDEFAULT_", create)  # no key, password, policy, grant or default role
    for bad in ("", "xyz", "5EC0DE01", "5ec0de0", "5ec0de011", "5ec0de01; DROP USER ADMIN"):
        with pytest.raises(ValueError):
            st.decoy(bad)


def test_the_rollback_statements_document_equals_the_generator():
    path = ROOT / "docs/design/CONNECT_PRODUCT_RUN_ROLLBACK_CHECK_STATEMENTS.md"
    assert path.read_text() == st.render_rollback_markdown(), (
        "regenerate: python3 -c 'from app.connect.e2e.statements import render_rollback_markdown; print(render_rollback_markdown(), end=\"\")'")


def test_every_statement_in_the_rollback_document_passes_the_lockout_rule():
    suffix, conn = "a1b2c3d4", "c0ffee11"
    statements = [*st.preflight(), *st.fixture(suffix), *st.registration(suffix, "OWNER", 8765, ["35.238.115.103/32"]).statements,
                  *st.read_backs(suffix), *st.decoy(conn), *st.teardown(suffix, "OWNER", ["35.238.115.103/32"], 8765)]
    st.assert_no_lockout(statements)


def test_the_fault_is_a_dev_tool_the_app_never_imports():
    offenders = [str(p.relative_to(APP)) for p in APP.rglob("*.py")
                 if "connect/e2e" not in p.as_posix() and re.search(r"connect\.e2e|connect import e2e", p.read_text())]
    assert offenders == []
