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

## Second run: an uncached warm figure (approval `scale_test_uncached_rerun`, 2026-11-21)

Run `ab892222`, the borrowed 2X-Small only, 104 statements, 0 wrong answers, no warehouse created, nothing stopped. Each statement now carries a constant output column unique to the run (the answer check ignores it), so each run has its own plan. **0 of 104 were answered from the result cache** (the first run had 72), so these are real executions, from the warehouse's own history.

| shape | phase | runs | execution p50 / p95 (ms) | wall p50 / p95 (ms) | read (MB) |
|---|---|---|---|---|---|
| no dimension | cold | 7 | 354 / 372 | 1,242 / 1,309 | 11.1 |
| no dimension | warm | 14 | 326 / 416 | 1,187 / 1,329 | 11.1 |
| one high-cardinality | cold | 7 | 393 / 481 | 1,290 / 1,390 | 11.9 |
| one high-cardinality | warm | 14 | 360 / 417 | 1,225 / 1,373 | 11.9 |
| one low-cardinality | cold | 7 | 402 / **5,370** | 1,296 / **6,637** | 11.2 |
| one low-cardinality | warm | 14 | 342 / 354 | 1,213 / 1,301 | 11.3 |
| two dimensions | cold | 7 | 383 / 651 | 1,273 / 1,551 | 11.3 |
| two dimensions | warm | 14 | 358 / 381 | 1,233 / 1,525 | 11.3 |
| time grain | cold | 4 | 368 / 525 | 1,305 / 1,545 | 17.0 |
| time grain | warm | 8 | 332 / 353 | 1,202 / 1,218 | 17.2 |

Concurrency 4 (8 statements): no-dimension and high-cardinality executed in 346 and 426 ms median; the other three shapes' history rows had no execution time yet when read (wall-clock 1.5 to 1.6 s against 1.2 to 1.5 s alone).

**Against the proposed thresholds** (criteria for reading the report, not enforced):
- Warm p95 at most 5 s (none or one dimension) and 10 s (two dimensions or a time grain): **met**, the worst warm p95 is 417 ms.
- Concurrency 4 p95 at most 3 times the single-run p95: **met on wall-clock** (no queueing visible at 4); exact execution times are missing for three shapes.
- Cold at most 3 times the warm median: **met for every shape except one outlier**. One cold run of the one-low-cardinality statements took 5.4 s (execution) against a 0.34 s warm median. It happened once in 32 cold runs. It repeated in a rerun, and the cause is a cold storage read: see the last section.

**What it says.** On 1.1 million orders the 2X-Small answers every certified statement in about 0.35 s of execution, nearly flat across shapes (the scan of 11 to 17 MB dominates, and a group-by adds almost nothing). The wall-clock of about 1.2 s is mostly the command line's own overhead, not the warehouse. The cold-to-warm gap is small because the data is already in the disk cache. Still **no size comparison** (larger warehouses were refused) and still **nothing about 50 million orders**.

Not verified: why three concurrent shapes lacked execution times in history (probably the read came before the record was final).

## The cold outlier repeats, and the cause is a cold storage read (approval `scale_test_outlier_rerun`, 2026-11-21)

Run `ab892222`'s 5.4 s outlier recurred in a repeat run (the same plan, 104 statements, 0 cache hits, 0 wrong answers): one cold run of `one_low_cardinality:delivery_fee_total:usd_country` took **6.2 s** of execution (7.5 s wall), against a 0.35 s median for everything else. The report now lists its five slowest runs.

The warehouse's record explains it. Both slow queries (5.37 s and 6.16 s) read the same **11,185,427 bytes, all of it remote storage, 0% from the disk cache**. Every other query read about 11.7 MB with 100% from the disk cache. So the slow run is the first one to read the table's data file after the warehouse's disk cache was empty (the warehouse auto-stops when idle): it fetched the file from cloud storage once, then every later statement was served from the cache. Compile time was normal (about 0.5 s), so it is not planning.

Which statement pays the cost is decided by the run order, and the order is seeded and identical in both runs, which is why the same statement was hit twice. That the slow statement is the first one to touch the data is **consistent with the evidence but not proven**: the run order was not recorded alongside the timings.

**What it means:** the first question after the warehouse has been idle costs about 5 to 6 s on tier M, then about 0.35 s. That is a real product behaviour (a user's first question after a quiet period), not noise. Mitigations, none built: keep the warehouse warm, or send a cheap read when a session opens. Larger data would make the first read longer. Untested here: whether a bigger warehouse changes it (none could be created).

## Tier M times 10 (in-warehouse replication), 2026-11-22

Approval `replication_n10_views_and_latency`. Views `orders_current` and `orders_usd_gross` were created on `dev.compass_synthetic_food_delivery_m_x10` (8 and 5 statements, 0 failures, every check equal to the answer key scaled by 10, e.g. 9,994,880 rows in `orders_current`). Then run `4b5dd3a8` of the same uncached scale test (`--copies 10`: expected values scaled by 10, proved on DuckDB) on the borrowed Serverless Starter Warehouse (2X-Small).

- **104 statements, not stopped (so no wrong answer), 0 served from the result cache.**
- **Warm execution median about 1.8 to 2.1 s, p95 at most 2.45 s** (tier M: about 0.35 s, p95 at most 0.42 s). Ten times the data cost about 5 to 6 times the execution time: sublinear, but not flat.
- **Concurrency 4:** execution p50 1.7 to 2.8 s, p95 at most 2.83 s, 1.3 times the single-run p95 on the same shape.
- **Cold runs were not slower than warm** (cold p50 1.8 to 2.2 s). The 5 to 6 s remote-read outlier did NOT appear. Caution: the loader's view checks had just read this data, so it was in the disk cache; this run does not show what a first read after the data leaves the cache costs at 10 times the size.

Against the accepted thresholds: warm p95 at most 5 s (met: 2.45 s, including two dimensions and the time grain, whose limit is 10 s); concurrency 4 p95 at most 3 times single p95 (met: about 1.3 times); cold at most 3 times warm median (met, for the reason above).

Limits: replicated data compresses and caches better than real data, so this flatters the warehouse; the group sets are the same as tier M's (same cardinalities); one shared 2X-Small, no size comparison. The cold-after-idle question at this size is still open.

## Correction (2026-11-22): the warehouse was not idle

The section above says the first read was slow because the warehouse's disk cache was empty "since the warehouse auto-stops when idle". That mechanism is **not supported**. The first warm-up experiment waited 15 minutes for the starter warehouse to stop and it never did: other scheduled jobs in the workspace (a statement every 5 or so minutes) keep it running, and it was RUNNING when both outliers happened. So the measured fact stands (a first read of the tier M file came from remote storage, 0% from the disk cache, 5 to 6 s, the same 11,185,427 bytes twice), but the cause is **the table's data not being in the disk cache after about 13 minutes without a read, on a running warehouse** (the gap between the two outlier queries was about 16 minutes, with a run between them). Why it left the cache (eviction by the other jobs' work, a time limit, or something else) is **not known**.

The first warm-up experiment (`warmup_experiment`, 2026-11-22) therefore stopped with `warehouse_did_not_go_idle` and produced no result. Nothing was changed on the workspace.

## Warm-up experiment at 10 times the data, 2026-11-22

Approval `warmup_experiment_x10`. Run `4889e9af` on the Serverless Starter Warehouse against `dev.compass_synthetic_food_delivery_m_x10`, warmed round only (the control is the tier M figure of 5.4 s from earlier runs, not measured here). 36 statements, not stopped, **0 wrong answers**.

- Waited 20 minutes since the last read, then the two certified warm-up statements (`order_count`, `gross_order_value`) took **17.3 s together** (wall, about 8.6 s each), against about 2 s for the same kind of statement when the data is cached. That is where the cold read landed.
- The first workload statement after the warm-up (`discount_total`, no dimension) ran in **2.94 s** (execution), reading 761,486 bytes from remote storage; the other 31 had a **median of 2.05 s**. So the first one cost about 0.9 s more, 1.4 times the median, not the 3 to 5 times seen at tier M.
- The harness printed "warm-up did not remove the delay". **Do not read that as a finding.** Its threshold (first statement at most 1.5 s) was set for tier M, where a warm statement takes 0.35 s; at 10 times the data a normal statement takes 2 s, so nothing could pass it. The number that matters is 1.4 times the median.

What it shows: after 20 minutes idle at 10 times the data, a cold read costs about 8.6 s per warm-up statement, and a warm-up pass of two statements (17 s) leaves the next statements within about 1.4 times of normal. What it does not show: no control round was run at this size, so "the warm-up helped" is not proved here; it is consistent with it. The 17 s would be paid by the Warmer, not by the user, if it ran in the background.
