# Warehouse scale test on tier M: a design (2026-11-21)

Status: **the harness and its dry run are built (fakes only); no live run, nothing approved.** See section 7 for what changed from the first draft. It follows the tier L result in `docs/evaluation/g5-live-loads-results.md` (generation did not fit on the laptop) and the owner's choice of option 3: treat tier M as the largest tier and test scale on the warehouse side. Databricks behaviour marked *check* is from general knowledge and not verified on this workspace.

## 1. What question this answers

Tier M is 1,112,404 orders and 2,577,738 order items, already loaded and verified in `dev.compass_synthetic_food_delivery_m` (tier M took about 2 minutes to load on a 2X-Small). The product's runtime cost is not loading; it is **the certified, compiled statements a user's question turns into**. So the test asks:

1. For the certified metrics and dimensions, what is the **latency and warehouse cost per answer** at each warehouse size, cold and warm, alone and under concurrency?
2. Is the **answer identical at every size** (it must be; a different number at a different size is a correctness bug, not a performance finding)?
3. Is the product's **cost bound** (the pre-run estimate that refuses an expensive statement) within an order of magnitude of what the warehouse actually did?
4. What default warehouse size should a customer be told to start with?

## 2. What it cannot show (stated up front)

- 1.1M orders is small. A 2X-Small likely answers most statements in a few seconds, and a bigger warehouse may show **no gain**, because the work is dominated by fixed overhead (compile, queue, start-up), not scan. A flat curve is a real result: it says the knee is not reachable at this size. It is **not** evidence about 50M orders and must not be quoted as such.
- It does not test data skew, small-file layout, or Delta table maintenance at tens of millions of rows.
- It does not exercise Snowflake, per-user pass-through, or the real model.

Phase 2 (below) is the honest way to get a scale signal if the flat curve makes Phase 1 uninformative. It is **not** part of this design's approval.

## 3. Design

### 3.1 Workload: derived, not hand-written

The statements come from the **deterministic compiler** applied to the certified combined contract (`food-delivery-synthetic-views`, 7 metrics, 15 dimensions), so we measure what the product really sends, not SQL we invented. Selection, fixed and seeded so reruns are identical:

| Shape | Example | Count |
|---|---|---|
| metric, no dimension | gross order value | 7 |
| metric by one low-cardinality dimension | orders by status | 7 |
| metric by one high-cardinality dimension | orders by city or courier | 7 |
| metric by two dimensions | orders by city and month | 7 |
| metric by time grain (day, month) | monthly gross value | 4 |
| Total | | about 32 |

The workload file records each statement's text hash, the metric and dimension ids, and the DuckDB-recomputed expected result (from the answer key, as in the earlier loads). Statement text is not logged; only its hash.

### 3.2 Matrix and control of the variables

- **Sizes:** 2X-Small (baseline, matches the load), X-Small, Small, Medium. Stop at Medium: a larger size on 1.1M rows is cost with no information.
- **One dedicated, temporary warehouse per size, all four live together** (so runs can be interleaved), named `compass-scale-<size>-<run id>`, serverless, auto-stop 5 minutes, created and deleted by the run. We do **not** resize the shared Serverless Starter Warehouse: that changes a resource other work depends on and makes runs unrepeatable.
- **Cold vs warm:** each statement runs once cold (the first run on that warehouse), then 3 warm runs. Cold and warm are reported separately and never averaged. Cold is per statement, not per table: earlier statements may already have warmed the table's data.
- **Result cache:** every statement carries a unique leading comment so identical text is never answered from the cache, and the warehouse's own record (`result_from_cache`) is checked afterwards: any statement it served from the cache is excluded and counted in the report. No `SET` is sent.
- **Concurrency:** 1, 4 and 8 simultaneous statements per size, same statements, to see queueing. The Connect principal's real limit is the warehouse's, so this is what a team of analysts would meet.
- **Order:** statements and sizes interleaved in a seeded random order within each pass (one cold, three warm), so time-of-day noise on a shared cloud does not look like a size effect.

### 3.3 Measurement: the warehouse's own record, not a stopwatch

Wall-clock timing includes our CLI process start and polling (the loader polls every 2 seconds), which would swamp sub-second work. Primary numbers come from the **query history** (`/api/2.0/sql/history/queries`, *check* field names): total duration, execution duration, compile time, queue wait, rows and bytes read. Wall-clock is recorded as a secondary column so the overhead of our own path is visible, not hidden.

Reported per size and shape: median and p95 (not mean), cold and warm, plus bytes read per answer. Warehouse cost is **time on the warehouse times that size's DBU rate times an assumed price**; the price table is an input that the owner supplies or approves, never a number we quote as fact (the same rule as `performance_eval.py`).

### 3.4 Correctness invariants checked on every run

- Every result equals its DuckDB-recomputed expected value, exactly for counts and to a stated tolerance for money. **One mismatch stops the run.**
- The same statement returns the same result at every size.
- Only a plain `SELECT` on the target schema's own objects ever runs (a new guard, `scale_test/guard.py`: no other verb, no `;`, no object outside the schema). Anything else is refused before it leaves the process.

### 3.5 Harness

A new package `reference_stack/scale_test/`, reusing what exists (`DatabricksCliSession` with its wall-clock cap and `max_statements`, `check_approval`, the statement guard), adding no dependency:

- `workload.py`: build the statements and expected results from the contract and the compiler; pure, runs on DuckDB in tests.
- `runner.py`: the matrix, the seeded order, the concurrency, the stop rules; talks to an injected session so tests use a fake.
- `history.py`: reads the warehouse's query records and joins them to our statements by statement id.
- `report.py`: JSON plus a markdown table, rendered from the raw rows.
- `__main__.py`: **dry-run by default** (prints the plan, the statement count, the worst-case cost, makes no call); `--execute` needs `--approval-id`.

Tests (fakes only, no cloud): workload determinism and expected-result correctness on DuckDB; the read-only guard refuses a mutating statement; the stop rules trip (cap, statement budget, mismatch); the report is stable for fixed inputs; the approval check refuses a missing or mismatched approval. Nothing is claimed to have run live until it has.

### 3.6 Caps and stop rules (proposed)

- At most **600 statements** (the plan as built sends 578: 32 statements x 4 sizes x 4 runs, plus 8 statements at concurrency 4 and 8 per size, plus 2 preflight checks), **45 minutes** wall-clock, four warehouses live at once.
- Stop at once on: an answer mismatch, any non-`SELECT` attempt, the budget or the cap, a statement that took over 5 minutes (checked when it returns; the run's own cap bounds a hung one), a warehouse not stopping, or the cost estimate passing the cap the owner sets.
- **Teardown always runs** (a `finally`): delete every `compass-scale-*` warehouse the run created, then list to prove none remain. A warehouse we cannot delete is reported loudly with its name.

## 4. Preconditions found while building

- **The two views do not exist in the tier M schema.** `orders_current` and `orders_usd_gross` were created only in the tier S schema; tier M holds the seven tables. The certified contract's statements read the views, so the run's preflight (two read-only `SELECT COUNT(*)`) stops with a clear error until they exist. Creating them is a small write of the same kind as approval A (the loader's `--view-only` and `--usd-view-only` modes, 8 and 5 statements, checked against the tier M answer key). It is a **separate approval** and is not part of this one.
- **Expected answers come from a local tier M**, generated in about 20 seconds on the laptop, then each statement's DuckDB answer is compared with the warehouse's. The schema name is retargeted in memory; the certified contract on disk is untouched.
- **The product's compiled statements end in `LIMIT 100` with no `ORDER BY`.** Above 100 groups the rows returned are an arbitrary subset, so the check is: the count equals the limit and each row is a real group with the right value.

## 5. Approvals this needs (none given)

| Gate | What | Why it is a gate |
|---|---|---|
| Live write | create the two views in the tier M schema (8 + 5 statements, loader view-only modes) | a write to the workspace; prerequisite |
| Live cloud mutation | create and delete 4 temporary warehouses | creates billable compute |
| Live read | about 600 `SELECT`s on the tier M schema, via the owner's `databricks` login | warehouse time |
| Thresholds | what counts as acceptable p95 latency and cost per answer | a human gate; the numbers in this design are proposals |
| Price table | the DBU rate and price to assume | an input we must not invent |

Rollback: delete the warehouses (the run does this). Nothing is written to the data, no grant changes, the tier M schema is untouched. What cannot be undone: the compute already consumed.

## 6. Phase 2, only if the curve is flat (separate approval)

Reach a real scale signal **without generating anything on the laptop**: replicate tier M inside the warehouse, `INSERT ... SELECT` with ids offset by a fixed stride, N times, into a new schema `dev.compass_synthetic_food_delivery_m_x<N>`. Because ids are offset and everything else is copied, the answer keys transform exactly: counts and sums multiply by N; ratios, averages and distinct dimension values are unchanged. That gives 1.1M x 10 or x 45 (about 50M) with provable expected answers. Caveats to state with any result: replicated data has no new skew and perfectly repeated value distributions, so it flatters compression and caching. Its cost is real warehouse write time and storage, so it needs its own approval, cap and teardown.

## 7. Decisions for the owner, and what changed from the first draft

1. **Run Phase 1 as designed** (recommended): build the harness and the dry run first; the live run waits for your approval of the cloud mutation, thresholds and price.
2. **The matrix**: keep 2X-Small to Medium, or add Large for a reference point (recommended: no, add it only if Medium still improves).
3. **Thresholds and the price table**: you supply them, or approve the proposed ones after seeing the dry-run plan.
4. **Phase 2**: defer until Phase 1 shows a flat curve.

Changes from the first draft, found while building: no `SET` is sent (cache is avoided by a unique comment and verified from the warehouse record); the run is 1 cold + 3 warm per statement, not 5 warm; all four warehouses are live together; the views prerequisite above; the statement budget is 578 of 600.

Built and tested with fakes only: `python -m reference_stack.scale_test --tier M` prints the dry run. Nothing in this note is approved or measured live.
