"""Shared, session-scoped corpus runs.

The ADS-057 rollout corpus, the ADS-059 poisoning corpus, and the ADS-070 correctness run each take 15 to 40 seconds and were run
several times by different test files that only read the result. They are run once per session here. A test that needs to prove a
defence can fail by *changing behaviour* (for example switching a check off) still runs its own corpus; only read-only uses share.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="session")
def rollout_report(tmp_path_factory):
    from reference_stack import rollout_eval

    return rollout_eval.run_corpus(tmp_path_factory.mktemp("rollout-corpus"))


@pytest.fixture(scope="session")
def poisoning_report(tmp_path_factory):
    from reference_stack import poisoning_eval

    return poisoning_eval.run_corpus(tmp_path_factory.mktemp("poisoning-corpus"))


@pytest.fixture(scope="session")
def correctness_report():
    from reference_stack import m7_eval

    return m7_eval.run_correctness()


@pytest.fixture(scope="session")
def security_report(tmp_path_factory):
    from reference_stack import security_eval

    return security_eval.run_corpus(tmp_path_factory.mktemp("security-corpus"))


@pytest.fixture(scope="session")
def isolation_inputs(security_report, correctness_report):
    """The already-run ADS-067 and ADS-071 results that the ADS-074 evaluation folds in (it runs its own probe matrix)."""
    return {"security": security_report, "protected_cases": correctness_report["cases"]}


def agreement_with(report: dict, case_id: str, expected: str) -> float:
    """The agreement a corpus run would have scored had one case's expectation been `expected` (no new run needed)."""
    cases = report["cases"]
    return sum((c["got"] == expected) if c["case_id"] == case_id else bool(c["passed"]) for c in cases) / len(cases)


@pytest.fixture(autouse=True)
def _sys_path_has_no_duplicates():
    """Alembic's `prepend_sys_path = .` adds an entry on EVERY migration call and never removes it, so a long test session grew `sys.path` from 9 to 901 entries. Every failed import
    (and DuckDB tries several per value it converts) then walks all of them: 46 us became 3.3 ms in CI and the synthetic-data tests took 26 minutes instead of 1.4. Removing duplicates
    after each test keeps every real entry and its order, and stops the growth."""
    yield
    sys.path[:] = list(dict.fromkeys(sys.path))
