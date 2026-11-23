"""The CI split must run every test file exactly once: a file in no part, or in two, is a silent gap or a double cost."""

from __future__ import annotations

import ci_parts


def test_every_named_file_exists_and_is_in_one_part_only():
    named = [f for files in ci_parts.PARTS.values() for f in files]
    assert len(named) == len(set(named)), "a file is listed in two parts"
    assert set(named) <= set(ci_parts.all_test_files()), "a listed file does not exist"


def test_rest_covers_everything_that_is_not_named():
    args = ci_parts.pytest_args(ci_parts.REST)
    ignored = {a.split("/")[-1] for a in args if a.startswith("--ignore=")}
    named = {f for files in ci_parts.PARTS.values() for f in files}
    assert ignored == named and args[0] == "tests"
    runs = {f for part in ci_parts.PARTS for f in (a.split("/")[-1] for a in ci_parts.pytest_args(part) if a.startswith("tests/"))} | (set(ci_parts.all_test_files()) - ignored)
    assert runs == set(ci_parts.all_test_files())


def test_an_unknown_part_is_refused():
    try:
        ci_parts.pytest_args("nope")
    except SystemExit:
        return
    raise AssertionError("an unknown part must be refused")


def test_only_a_part_with_independent_tests_uses_workers():
    assert set(ci_parts.EXTRA_ARGS) <= set(ci_parts.PARTS)
    assert ci_parts.pytest_args("eval-ads056")[-2:] == ["-n", "2"] and "-n" not in ci_parts.pytest_args("eval-ads057")
