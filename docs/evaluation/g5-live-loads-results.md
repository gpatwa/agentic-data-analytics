# Live loads on the validation workspace: results (2026-11-21)

Run through `python -m reference_stack.loaders ... --execute` with the owner's signed-in `databricks` profile, the Serverless Starter Warehouse and the approval `pending_approvals_a_to_h` (program file). Workspace `https://dbc-b8672746-8e43.cloud.databricks.com`, catalog `dev`. Each run stops on the first failed statement or count mismatch and drops nothing. Warehouse seconds were not recorded (the session counts statements and wall-clock, not warehouse time), so the caps were limits, not measurements.

| Run | Target | Result | Statements | Wall-clock |
|---|---|---|---|---|
| A: view only | `dev.compass_synthetic_food_delivery.orders_current` | created; 6 of 6 checks match the generator's answer key (9,995 orders, 0 duplicate `order_id`, 9,000 delivered, 591 cancelled, 244 refunded, 160 partially refunded) | 8 (cap 8) | seconds |
| A2: USD amount view | `dev.compass_synthetic_food_delivery.orders_usd_gross` (USD orders not cancelled; gross, refunds not netted) | created; 3 of 3 checks match the answer key (gross USD value, unique orders, no cancelled rows) | 5 (cap 5) | seconds |
| E1: synthetic tier M | new schema `dev.compass_synthetic_food_delivery_m` | 56 steps and checks, 0 failures: 7 tables (5,000 restaurants; 2,000 couriers; 200,000 customers; 1,112,404 orders and `orders_flat`; 2,577,738 order items; 979,779 payments), every value check and breakdown equal to the answer key | 49 | about 2 min (cap 15) |
| E2: Online Retail II | new schema `dev.compass_reference_online_retail_ii` | 18 steps and checks, 0 failures: 1,067,371 rows, 53,628 invoices, 5,942 customers, 243,007 lines without a customer id, 19,494 cancellation lines, sales 20,813,918.43, cancellations -1,526,667.86, naive net 19,287,250.57, quantity 10,608,492, 43 countries | 18 | under 15 min (cap 15) |

What this shows and does not show:

- The loader's Databricks syntax now works on a workspace for tables, volumes, `COPY INTO`, comments and one view. The `TIMESTAMP_NTZ` fix from the first tier S attempt held at 1.1 million rows.
- The 1.1 million-row run finished in about two minutes end to end, so the planned tiers L (50 million) and XL (1 billion) are still untested and should not be inferred from this.
- Online Retail II is **real data** (CC BY 4.0; Chen, D. (2012), https://doi.org/10.24432/C5CG6D) and carries its attribution in the schema and table comments; it must never be labelled synthetic.
- Nothing was granted to any principal, no model was called, nothing was certified, and no schedule was created. The three schemas and the view are kept; cleanup is a manual `DROP SCHEMA ... CASCADE` or `DROP VIEW` by the owner, only on request.

## Tier L (50 million orders): not run, generation does not fit on the developer laptop (2026-11-21)

Approved (program file `synthetic_tier_l_load`) into a new schema with a 30-minute cap, stop on any mismatch. **No statement was sent to the workspace and nothing was loaded.** The generator was run locally into a file-backed DuckDB so it could spill to disk (16 GB RAM, about 50 GB free). After about 20 minutes it was still generating: the working database had grown to 14 GB, free disk had fallen from 46 GB to 23 GB at roughly 2 GB a minute, and no Parquet file had been written yet (about 7 GB more would be needed). The run was stopped on the approval's own rule (abandon before free disk falls below 12 GB) and every file it had written was deleted.

What this does and does not show:

- Tier M (1.1 million orders) generated in about 17 seconds, so generation does **not** scale linearly: 45 times more orders did not mean 45 times the time, it meant a different regime (memory spilling to disk, tens of gigabytes of scratch). Tier L needs a bigger machine or a different method.
- Not shown: whether the Databricks load of 50 million rows works. The loader's upload timeout (three minutes, a statement's) was a real blocker for any multi-gigabyte file and is fixed in this change (an upload now gets the run's remaining cap).
- Options if tier L still matters, none built: (a) generate on a machine with at least 64 GB RAM and 100 GB free disk; (b) generate in chunks by order-number range (the generator keys every random draw by row number, so chunks are deterministic in principle, but its tables, ledger and answer keys are not built to be assembled from chunks); (c) accept tier M as the largest tier and test performance by loading it more than once, or load tier M into a larger warehouse size.
- The cost estimate for the warehouse side is still not measured: tier M took about two minutes end to end on the 2X-Small warehouse.

## Tier M views for the scale test (2026-11-21)

Approval `scale_test_views_tier_m`. In `dev.compass_synthetic_food_delivery_m` the loader created `orders_current` (8 statements; all 6 checks equal to the tier M answer key: 999,488 orders, 0 duplicate `order_id`, 899,711 delivered, 59,990 cancelled, 24,898 refunded, 14,889 partially refunded) and then `orders_usd_gross` (5 statements; 0 duplicate `order_id`, gross USD 5,422,256,408 cents equal to the answer key, 0 cancelled). No failure, no stop, 13 statements in all. No table, grant or other schema was touched. Rollback is a manual `DROP VIEW` of each. The scale test itself has not run and is not approved.

## Phase 2 replication pilot, N = 2 (2026-11-22)

Approval `replication_pilot_n2`. Through the loader's `replicate` mode on the Serverless Starter Warehouse (shared with other scheduled jobs), `dev.compass_synthetic_food_delivery_m_x2` was created from `dev.compass_synthetic_food_delivery_m` with the four fact tables twice and the three dimension tables once. **36 statements** (the approval said 37: the derived net-value check needs no statement), **0 failures, no stop**: all 8 create steps ran and all 28 checks equal the answer key scaled by 2 (for example 2,224,808 rows in `orders` and `orders_flat`, 5,155,476 in `order_items`, 1,959,558 in `payments`, 1,998,976 distinct orders, gross USD 10,844,512,816 cents, the net value, and the three breakdowns). Ids are unique and the shared dimensions were not multiplied. The source schema was only read.

Timings from the warehouse's own history: the whole run took about 87 s from the first to the last statement (about 63 s of statement time). The fact-table `CREATE TABLE AS` statements took 3.4 to 6.1 s of execution each (`orders` 5.7 s, `orders_flat` 5.7 s, `order_items` 3.7 s, `payments` 3.4 s); the 28 checks 0.1 to 1.7 s each. Every standard-SQL construct in the plan ran on Databricks as expected.

Rough extrapolation to 45 copies (22.5 times the pilot), **not measured and assuming linear scaling**: the four fact-table builds about 5 to 6 minutes in all, the checks several minutes (window functions over 50 million rows may scale worse than linearly, and the starter warehouse is shared), so roughly 10 to 15 minutes. A wrong guess costs time, not correctness: every check is exact. N = 10 and N = 45 are **not approved**; the new schema stays until the owner drops it (`DROP SCHEMA dev.compass_synthetic_food_delivery_m_x2 CASCADE`).
