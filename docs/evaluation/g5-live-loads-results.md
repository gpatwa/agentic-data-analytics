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
