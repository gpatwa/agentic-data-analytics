# Warehouse scale test on tier M: results (2026-11-21)

Approval `scale_test_tier_m_run`. Run `a6ce30bb` on the Serverless Starter Warehouse (2X-Small), against `dev.compass_synthetic_food_delivery_m` (views `orders_current`, `orders_usd_gross`; 999,488 orders). No price per DBU was stated, so no dollar figure is computed.

## What happened

- **Only the 2X-Small ran.** The workspace refused to create X-Small, Small and Medium warehouses (the profile is named `...-free`; the cause is not confirmed, the API error text is not kept). So **no size comparison exists**, and the question "does a larger warehouse help" is **unanswered on this workspace**. Nothing was created, so nothing needed deleting; teardown was clean and only the starter warehouse remains.
- **104 statements, 0 wrong answers.** All 32 compiled statements (7 metrics; none, one low-cardinality, one high-cardinality, two dimensions, and time grains) returned the answer-key values, 1 cold run and 2 warm runs each, plus 8 at concurrency 4. The run was not stopped. This is the product's correctness check on a 1.1-million-row tier, and it passed.

## Latency, from the warehouse's own query history (re-read afterward)

The 32 first runs (the only ones executed rather than answered from the result cache):

| measure | median | p95 | max |
|---|---|---|---|
| execution | 403 ms | 565 ms | 638 ms |
| compilation | 233 ms | 400 ms | 558 ms |
| total in the warehouse | 675 ms | 950 ms | 989 ms |
| bytes read | 11.7 MB | 18.0 MB | 18.5 MB |

Each statement scanned about 1.07 million rows from one file, entirely from the warehouse's disk cache. Wall-clock from the tool was 1.3 s median and 1.6 s p95 for these first runs; the difference is the command line's own overhead.

Against the proposed thresholds (5 s for no or one dimension, 10 s for two dimensions or a time grain): **met by about an order of magnitude** on the first run. The concurrency and cold-to-warm criteria were **not measurable** (below).

## What did not work, and what was corrected

1. **The result cache was not avoided.** A unique leading comment does not change the cache key: 72 of the 104 statements (every warm and concurrent run) were answered from the result cache. So there is **no uncached warm-execution number and no concurrency number**; the cached repeats took about 0.2 s in the warehouse and about 0.9 s of wall-clock, almost all of it the command line. The harness now labels those runs `cached_repeat` instead of excluding them. Getting a true warm, uncached figure needs another approach (see below).
2. **The first run read the wrong history field**, so its report showed no execution times (the vendor's field is `execution_time_ms`, not `execution_total_time_ms`). The fields are now confirmed against a real response and fixed; the table above comes from re-reading the history. The report's own count of cached runs (68) differs from the 72 seen afterward because history marks the cache flag a little late.

## Reading it honestly

- Tier M is small. A 2X-Small answered every certified statement in under a second, so there is no sign of strain, but also **no evidence about 50 million orders**.
- The first-run numbers include compilation, which a repeated question avoids through the cache; both are real product behaviour.

## Options, none built

- Ask whether the workspace allows larger warehouses (an edition or quota limit), or use another workspace for the size comparison.
- For an uncached warm number: add a harmless unique output (for example a constant column the check ignores) so each run has its own plan, or measure with the cache disabled at the workspace level (a setting change, which needs approval).
- Phase 2 (in-warehouse replication of tier M) for a scale signal.
