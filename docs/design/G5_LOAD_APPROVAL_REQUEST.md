# G5: a request to load reference data into a warehouse (2026-11-20)

Status: **a request for the owner's approval. Nothing has been done.** No workspace was contacted, nothing was written, and no credential was used in preparing this. Approval is requested by filling the blanks in "What I need from you" and replying with the sentence at the end.

Why this step: the synthetic food-delivery generator (G1 to G4) and the real Online Retail II download exist only as local files. Until the data sits in a warehouse the product has nothing real to run against, and the live checks that follow (L1 declared keys, L2 oracle comparison) have no tables to look at.

## 1. What would be written

Everything goes into **one new schema that Compass owns**, created by this run, containing only reference data. Nothing outside it is read, changed or dropped.

| Dataset | Tables | Rows (measured) | Size | Status |
|---|---|---|---|---|
| Synthetic food delivery, tier S (10,000 orders) | restaurants, couriers, customers, orders, order_items, payments, **orders_flat** | 80, 60, 2,000, 11,126, 25,975, 9,813, 11,126 | not measured; roughly 1/100 of tier M, so a few MB at most | generator built and tested |
| Synthetic food delivery, tier M (1 million orders), optional | same seven | 5,000, 2,000, 200,000, 1,112,404, 2,577,738, 979,779, 1,112,404 | 162 MB (measured) | generator built; one run timed at about 16 s |
| Online Retail II, optional | one table, **online_retail_ii** | 1,067,371 source rows | 43.5 MB workbook, smaller once converted | file downloaded and unpacked nowhere; **the loader is not built yet** (see section 5) |

Every table carries a comment starting `SYNTHETIC DATA` (the synthetic ones) or the CC BY 4.0 attribution (Online Retail II). `orders_flat` gets a declared primary key on its surrogate `row_id`, which is what the key-reading work (E1 and E2) reads. Warehouse primary keys are informational, so that key is a claim the loader makes about data it generated.

## 2. What would NOT happen

- No customer or production data, and no production workspace.
- No change to the product, no model call, no grant to any identity (the Connect principal gets read access later, through the existing registration flow, as its own step).
- No drop, overwrite or alter of any object this run did not create. If the schema already exists and is not empty, the run stops.
- No credential printed, logged, stored in a file or committed.

## 3. How it would run

Three options. They differ in who holds the credentials.

| Option | Who runs it | Notes |
|---|---|---|
| **A. I run it, as for C4 and C5** | me, with the owner's loader identity already in the terminal environment, behind an explicit `--allow-live` flag and an expected-host check | the same pattern as the earlier live runs; the credential never leaves the environment and a leak scan watches every output |
| **B. You run it** | you, using statements I generate and print for review first | no credential reaches me at all; slower, and I cannot verify the result unless you paste it back |
| **C. Review first, then A or B** | me prints every statement without executing, you read them, then A or B | recommended: it costs one extra round trip and removes the risk of surprises |

The loading identity must be **a different principal from the product's read-only Connect principal**: it needs create rights on the new schema and nothing more. The Connect principal stays read-only by design.

Mechanism, to be confirmed in the dry run (the exact syntax is not verified on this workspace):
- **Databricks:** upload each Parquet file to a Unity Catalog volume inside the new schema, then `CREATE TABLE ... AS SELECT * FROM read_files(...)`, then the comments and the key.
- **Snowflake:** an internal stage inside the new schema, `PUT` then `COPY INTO`, then the comments and the key.

## 4. Checks, stop rules and cleanup

**After loading, before anything else:**
1. Row counts of every table equal the generator's manifest.
2. The same 15 values the generator's two answer keys agree on are recomputed **as SQL on the loaded table** and must equal `truth.json`. Any difference stops the run and is reported, not smoothed over.
3. The declared key on `row_id` is present, and `row_id` is unique and non-null.

**Stop immediately if:** any statement fails; any count or value differs; the schema is not empty when expected to be new; a statement would touch anything outside the new schema; the spend cap below is reached; the host differs from the one you name.

**Spend cap:** a small cap on warehouse time, stated below. **The cost is not measured**: earlier live runs did not record warehouse seconds, so the cap is a limit, not an estimate.

**Cleanup:** by default the schema stays, because the next steps need it. On request, one `DROP SCHEMA ... CASCADE` on the schema this run created, and only that.

## 5. What has to be built first, offline

- **Loader for the synthetic data:** a small script that reads the generator's Parquet and emits the statements above, with a dry-run mode that prints them. Offline work; the plan's earlier approval (G1 to G4) covers building it, but not running it.
- **Loader for Online Retail II (only if you choose it):** converts the workbook to typed Parquet, adds `row_id`, `line_amount` (quantity times price, because the compiler cannot multiply columns) and `is_cancellation`, and drives the same load. Offline, no approval needed to build; downloading is already done.
- Neither loader runs against a warehouse until this request is approved.

## 6. What approval unlocks

- **L1:** reading declared keys on the loaded tables, catalog metadata only.
- **L2:** running the oracle comparison on them, which executes read-only statements against the loaded tables.
- The builder's 30-question set (20 answerable, 10 refused) can then be run through the product on real warehouse tables.
- A demo and landing-page screenshots on data that is clearly labelled synthetic.

## What I need from you

Fill in, or tell me to propose values:

| Blank | Suggested |
|---|---|
| Engine | Databricks only (the engine already used for C2 to C5); Snowflake later |
| Workspace host | _the validation workspace used in C4 and C5, or another you name_ |
| Catalog | _a catalog Compass owns; if none, say who creates it_ |
| New schema name | `compass_synthetic_food_delivery` (and `compass_reference_online_retail_ii`) |
| Warehouse | _the serverless starter warehouse used before, or another_ |
| Loading identity | _a principal with create rights on that schema only, not the Connect principal_ |
| Datasets | tier S now; tier M and Online Retail II as a second step |
| Run option | C (review the statements first), then A |
| Warehouse-time cap | _for example 15 minutes_ |
| Cleanup | keep the schema; drop only on your request |

## The sentence to reply with

> Approve G5: load \<datasets\> into \<catalog\>.\<schema\> on \<host\> using \<option\>, with a warehouse-time cap of \<N\> minutes, stop on any mismatch, no other object touched.

I will then record the approval in the program file (`docs/execution/enterprise-analytics/agentic-data-stack-program.yaml`) with your words, build the loader and its dry run, show you every statement, and only then run it.
