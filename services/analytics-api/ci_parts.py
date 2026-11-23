"""How CI splits the analytics tests into parallel parts, so the slowest part sets the wall-clock time and each part's own timing shows where the time goes.

`python ci_parts.py <part>` prints the pytest arguments for that part. The named parts list their files; `rest` is every other `tests/test_*.py` (so a new test file is picked up
automatically and nothing is ever left out). `tests/test_ci_parts.py` checks that every file runs in exactly one part.

The grouping comes from CI timings of 2026-11-23: one process took 47 minutes; as parts the longest took 7.3 minutes (the corpus gates, since split in two). The synthetic-data files took about 26 minutes inside the single process but 1.4 minutes alone, so they stay in a part of their own: they are slowed by something earlier tests leave behind, not by the files themselves."""

from __future__ import annotations

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent / "tests"
REST = "rest"

PARTS: dict[str, tuple[str, ...]] = {
    "eval-ads056": ("test_ads056_evaluation.py",),
    "eval-ads057": ("test_ads057_rollout.py",),
    "eval-corpora-a": ("test_ads059_poisoning.py", "test_ads071_correctness.py"),
    "eval-corpora-b": ("test_ads049_golden_suite.py", "test_ads075_reliability.py", "test_ads076_performance.py", "test_ads077_provider.py"),
    "synthetic": (
        "test_synthetic_food_delivery.py", "test_scale_test.py", "test_loaders.py", "test_loaders_replicate.py", "test_orders_current_certified.py",
        "test_orders_usd_gross_certified.py", "test_synthetic_views_certified.py", "test_drafter_on_synthetic_keys.py",
    ),
}

# Extra pytest arguments per part. eval-ads056 holds one 59 s corpus test and a dozen short, independent ones, so two workers put the short ones beside the long one (99 s -> 71 s on a
# laptop). Its corpus runner (reference_stack/evaluation_eval.py) is hashed into the release evidence manifest, so it is NOT changed to run its cases in parallel. The other parts share
# session-scoped corpus fixtures, which every xdist worker would rebuild, so they stay single-process.
EXTRA_ARGS: dict[str, tuple[str, ...]] = {"eval-ads056": ("-n", "2")}


def all_test_files() -> list[str]:
    return sorted(p.name for p in TESTS.glob("test_*.py"))


def pytest_args(part: str) -> list[str]:
    if part == REST:
        named = [f for files in PARTS.values() for f in files]
        return ["tests", *[f"--ignore=tests/{f}" for f in named], "-n", "auto"]
    if part not in PARTS:
        raise SystemExit(f"unknown part {part!r}; choose from {[*PARTS, REST]}")
    return [*[f"tests/{f}" for f in PARTS[part]], *EXTRA_ARGS.get(part, ())]


if __name__ == "__main__":
    print(" ".join(pytest_args(sys.argv[1])))
