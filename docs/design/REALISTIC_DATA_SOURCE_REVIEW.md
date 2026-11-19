# In-depth review of the four shortlisted data sources (2026-11-19)

Status: **a review for the owner; nothing is downloaded, loaded or run.** It follows `REALISTIC_DATA_SOURCE.md` and **corrects it in two places** (below). Facts were read from each source's own pages and from the repository's code on 2026-11-19; the NYC 311 row count was read from the portal's public API (a single read-only count). Items marked *verify* need a check before relying on them. Loading anything into the owner's Snowflake or Databricks account is a live step that needs explicit approval with the statements shown first (CLAUDE.md).

## Corrections to the first note

1. **Citi Bike is not a clean-licence fallback.** Its agreement, read in full, licenses use and modification, but **bars selling or hosting the data as a standalone dataset**, allows it in analyses only for **non-commercial** purposes, bars scraping and correlating with customer identities, and excludes its trademarks. A hosted reference copy inside a product demo would breach it. Dropped.
2. **The NYC sources have no stated licence.** The TLC trip-record page contains no licence, terms or redistribution statement. NYC.gov's general terms say all rights are reserved on site materials, **never mention Open Data or any open licence**, and say nothing about commercial use or redistribution. The Open Data programme says data is "open by default" under the city's Open Data Law, which describes access, not reuse rights. So for TLC and 311 the honest status is **"public, as-is, reuse terms not stated"**: fine to test against internally; before a public demo hosts a copy, ask the agency or counsel.

## Findings that apply to all four (from our own code)

| # | Finding | Evidence | What it means |
|---|---|---|---|
| 1 | **Large tables get no metrics from the drafter unless a primary key is declared.** A drafted metric needs a grain key, and the drafter drops any metric with no key evidence. We infer a key only when the profile saw the **whole** table, and the profile window is **100,000 rows**. Declared constraints are not read on Snowflake or Databricks yet (the C1 note lists "declared-constraint key reading" as not built). | `app/ontology_draft/profile.py` (`DEFAULT_ROW_LIMIT = 100_000`), `CONNECT_CERTIFICATION_PLAN.md` (C1 findings) | TLC (tens of millions), 311 (23 million) and Online Retail II (1.07 million) all exceed the window. **Build item before any of them is drafted live:** read declared primary keys from `information_schema`, and give each loaded table a declared surrogate key (`row_id`), which is also what a production gold table has. |
| 2 | **The sensitive-name rule would flag useful columns.** It matches whole tokens (`name`, `address`, `key`, `account`, `ip`, `tax`, …) and is conservative on purpose. | `app/ontology_draft/facts.py` | 311's primary key `unique_key` contains the token `key`, and `agency_name`, `street_name`, `incident_address`, `park_facility_name` match too: they become restricted fields and are not offered as filters. Acceptable and safe, but the contract will look thin until a person decides which to keep. |
| 3 | **None of these gives a join-free business question that uses ratios.** The compiler has no ratios or joins. | `REFERENCE_DATA_DEMO_PLAN.md` risks | Questions like tip percentage or average resolution time need the ratio packet; counts, sums, averages, min and max work. |
| 4 | **Time dimensions finally get exercised.** The sandbox had no date column, so the time-range, time-grouping and coverage-check rules ran only on unit tests. | `docs/evaluation/intent-coverage-k2-report.md` | All four have dates, so "per month" and "last year" questions become real tests of the intent and coverage checks. |
| 5 | **Shared sample catalogs may not accept per-table grants.** | `REALISTIC_DATA_SOURCE.md` | Settled only by one read-only inspection of the workspace (see D). |

## A. NYC TLC yellow-taxi trips

- **What it is:** monthly Parquet files from the NYC Taxi and Limousine Commission, `yellow_tripdata_YYYY-MM.parquet` on the TLC's CloudFront host, published with about a two-month lag, from 2009 to September 2026. Records come from technology providers, not from TLC, and TLC makes no accuracy claim.
- **Schema (from the March 2025 data dictionary):** 20 columns: `VendorID` (1, 2, 6, 7), pickup and drop-off timestamps, `passenger_count`, `trip_distance`, `RatecodeID` (1 to 6, 99), `store_and_fwd_flag`, `PULocationID` and `DOLocationID` (taxi zones), `payment_type` (0 to 6), `fare_amount`, `extra`, `mta_tax`, `tip_amount`, `tolls_amount`, `improvement_surcharge`, `total_amount`, `congestion_surcharge`, `airport_fee`, and from 2025 `cbd_congestion_fee`. A separate taxi-zone lookup CSV names the zones and boroughs.
- **Size:** millions of rows per month, tens of millions per year (*verify by counting the files you choose*). No row id exists.
- **Real mess to expect:** `tip_amount` excludes **cash tips**, so tips by payment type are structurally biased; payment and rate codes include "unknown" values; the schema changed over the years (new fee columns appear in 2025); the page warns some 2020 records from smaller bases may be updated later. Whether negative fares, zero distances and impossible timestamps appear is *verify on load*; they are common in this data and are the point of the test.
- **Sensitive columns:** none by name; no driver or passenger identity.
- **Certifiable metrics (today's compiler):** trip count, sum and average of `total_amount`, `fare_amount`, `tip_amount`, `trip_distance`, `passenger_count`; dimensions: pickup month and hour, pickup and drop-off zone and borough (after folding in the zone lookup), payment type, rate code, vendor.
- **Scenes it supports:** "Trips and total fare by month and borough last year" (answers); "Average tip by payment type" (answers, with the cash-tip caveat shown in the definition); "Tip as a percentage of fare" (refused: needs a ratio); a borough and hour pair with fewer than 10 trips (suppressed); "Show every trip for zone 132" (refused); a no-filter scan of the full table under a tight cost ceiling (refused before it runs).
- **Load plan:** one or more monthly Parquet files plus the zone lookup folded into the table as `pickup_zone`, `pickup_borough`, `dropoff_zone`, `dropoff_borough`, a `row_id`, and a declared primary key. Same files on both engines (Snowflake `COPY INTO` from a stage; Databricks `COPY INTO` or a table from files). Oracle: DuckDB over the same Parquet files, plus a per-month checksum table.
- **Terms:** no licence stated (see corrections). **Verdict: the right scale and mess test, internal only until the reuse question is answered.**

## B. NYC 311 service requests (2020 to present)

- **What it is:** the city's 311 requests, one row per request.
- **Size (verified):** **23,199,516 rows**, from 2020-01-01 to 2026-11-18, read from the portal's API. The portal split the old single dataset in two, so 2010 to 2019 is a different dataset.
- **Schema (48 columns, read from the portal's metadata):** a text `unique_key`; `created_date`, `closed_date`, `due_date`, `resolution_action_updated_date`; `agency`, `agency_name`; `complaint_type`, `descriptor`, `descriptor_2`; `location_type`, `facility_type`; `status`; `resolution_description` (free text); address, street, cross-street, intersection, `incident_zip`, `city`, `borough`, `community_board`, `council_district`, `police_precinct`, `bbl`; coordinates and a point; `open_data_channel_type`; park, vehicle, taxi, and bridge and highway columns that are populated only for some complaint types; and four computed region columns. All but the dates and coordinates are text.
- **Real mess to expect:** very wide with many columns null for most rows (the park, taxi and bridge columns apply to a few complaint types); every code is text; `closed_date` is null for open requests; complaint-type names have been renamed over time ("Problem (formerly Complaint Type)"); free text in `resolution_description`.
- **Sensitive columns:** the dataset says it reveals no personally identifying information about requesters, but it carries **street addresses, coordinates and free text**. Our rule would restrict `incident_address`, `street_name`, `agency_name`, `park_facility_name` and, notably, `unique_key`. A person decides which stay out of the contract; the free-text column must stay out.
- **Certifiable metrics:** request count (a natural key exists: `unique_key`), count of distinct complaint types, and, with a derived `hours_to_close` column added at load, average and maximum time to close; dimensions: created month, agency, complaint type, borough, status, channel.
- **Scenes:** "Requests by borough and month this year"; "Open requests by agency" (status filter); "Average hours to close by complaint type" (answers only if `hours_to_close` is loaded as a column, because the compiler cannot subtract two columns); "Percentage of requests closed within 3 days" (refused: ratio); "Show the addresses of noise complaints" (refused: restricted fields and personal-data question); a complaint type with fewer than 10 requests in a borough (suppressed).
- **Load plan:** the portal's export (CSV or via its API) into a staged file, typed on load (dates as timestamps, coordinates as numbers), with `hours_to_close`, a declared key on `unique_key`, and the four computed region columns dropped. Oracle: DuckDB over the same export, with a checksum table.
- **Terms:** no licence stated; the dataset page gives none. **Verdict: the best ticket-and-operations test and the only one with a native key, internal until the reuse question is answered.**

## C. UCI Online Retail II

- **What it is:** transactions of a UK online gift-ware retailer, 2009-12-01 to 2011-12-09, donated to the UCI repository in 2019.
- **Licence (verified):** **CC BY 4.0**. Sharing and adaptation for any purpose, with credit. Suggested citation: Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository, https://doi.org/10.24432/C5CG6D. **The only one of the four that can be shown publicly as is.**
- **Size and shape (verified):** **1,067,371 rows**, one file `online_retail_II.xlsx` (43.5 MB), eight columns: `InvoiceNo` (a leading "c" marks a cancellation), `StockCode`, `Description`, `Quantity`, `InvoiceDate`, `UnitPrice` (sterling), `CustomerID`, `Country`. The page says the data has missing values but does not say which columns; the repository commonly has two worksheets, one per year (*verify on download*).
- **Real mess to expect:** cancellations as negative quantities on "c" invoices; non-product stock codes (postage, manual adjustments, bank charges); missing customer ids on a share of lines (*verify the share*); wholesalers inflating quantities; duplicate lines.
- **Sensitive columns:** none by name (`CustomerID` is a pseudonymous number). Treat it as a join key to nothing.
- **Certifiable metrics:** revenue as the sum of a derived `line_amount` (quantity times price, added at load because the compiler cannot multiply columns), line count, distinct invoices and customers (`count_distinct`), units; dimensions: invoice month, country, stock code, a `is_cancellation` flag derived from the invoice prefix.
- **Scenes that matter:** "Revenue by country and month" answers only if cancellations are handled **in the definition**, which is exactly the certified-definition story (a naive sum nets returns silently); "revenue excluding cancellations" versus "net revenue" show two certified metrics; "Revenue per customer" (refused: ratio); "Customers by country" (count distinct); a country with fewer than 10 invoices (suppressed).
- **Load plan:** convert the workbook to Parquet or CSV with typed columns, add `row_id`, `line_amount`, `is_cancellation`, and a declared key; same file on both engines; oracle in DuckDB; attribution line on any page that shows it.
- **Verdict: go. Quickest to a recognisable commercial demo, public-safe, small enough to load in seconds, and its cancellation handling is a natural showcase for certified definitions. Too small for a scale test.**

## D. Databricks `samples.wanderbricks`

- **What it is:** a "simulated travel booking platform" dataset preloaded in the `samples` catalog of Unity Catalog workspaces. Eleven tables: `users`, `hosts`, `properties`, `bookings`, `payments`, `booking_updates`, `reviews`, `clickstream`, `page_views`, `customer_support_logs`, `destinations`.
- **What the page gives and does not give:** table purposes and a few columns (user name, email, country or region and user type; booking check-in and check-out, guests, total amount, status; payment method, amount, status; review rating and an `is_deleted` soft-delete flag; clickstream events with nested device and referrer; support logs with nested message arrays). **No full column lists, no row counts, no dataset-specific terms**: only the general Databricks terms, and the stated purpose is exploring pipelines, dashboards and machine-learning workflows.
- **Why it is on the list:** it is the only one with realistic **personal columns** (name, email) that exercise the redaction rule on real-shaped data, nested and soft-deleted records that a denormalising view has to handle, and it needs no download.
- **Open checks that need the workspace (a read-only step):** `SHOW TABLES IN samples.wanderbricks`, `DESCRIBE` and `SELECT count(*)` on each table, whether a `GRANT SELECT` on a `samples` table is possible, and whether our `SHOW GRANTS` read-only proof works there. These are a handful of reads through the owner's signed-in workspace and need explicit approval with the statements shown; nothing was run.
- **Likely shape for us:** a denormalised `bookings_gold` table or view (booking, property, destination, user country and type, payment status) created in our own schema, because the compiler has no joins; the nested clickstream and support tables stay out.
- **Terms:** no dataset-specific licence. It is a vendor sample intended for use inside a workspace; **redistributing it, or exporting it to Snowflake, is not covered.** Keep it in Databricks. **Verdict: use it for the redaction, denormalised-view and grant-versus-copy tests on Databricks only; not a public demo dataset.**

## Comparison for the decision

| | A. TLC | B. 311 | C. Online Retail II | D. wanderbricks |
|---|---|---|---|---|
| Real data | Yes | Yes | Yes | Simulated |
| Rows | millions per month | 23.2 million (verified) | 1.07 million (verified) | unknown |
| Reuse terms | none stated | none stated | **CC BY 4.0 (verified)** | vendor sample, no dataset terms |
| Needs a denormalised step | no (fold in zones) | no (derive hours to close) | no (derive amount and flag) | **yes** |
| Native key | none | `unique_key` | none | per table, unverified |
| Personal-style columns | none | addresses, coordinates, free text | none | name and email |
| Works on both engines | yes | yes | yes | Databricks only |
| Public demo safe | **ask first** | **ask first** | **yes** | **no** |
| Best use | scale, cost, suppression | operations and ticket analytics, text refusal | commercial sales demo, certified-definition story | redaction and views on Databricks |

## Recommendation and the order of work

1. **Now, offline, no approval:** (a) build **declared-primary-key reading** for Snowflake and Databricks introspection and tests on fakes (finding 1); (b) write the loaders and DuckDB oracles for C, A and B as scripts with checksums, with the surrogate-key and derived-column rules, tested on small fixtures. **Downloading the real files needs your permission (source, file and size stated first).**
2. **First live step, once you approve:** Online Retail II into Compass-owned reference schemas on Snowflake and Databricks (about 45 MB; seconds of compute), draft a contract through the connection, you certify it, then run the question set. It is the only dataset we can show publicly, and it tests the new declared-key reading.
3. **Then** the read-only inspection of `samples.wanderbricks`, and TLC and 311 for scale, **internally**, while the reuse question for the NYC data is put to the agencies or counsel.
4. **Public pages:** only Online Retail II, with its attribution, and the same status badges and wording rules as the landing page.

## Decisions for you

1. **Approve the offline build** in step 1 (declared-key reading and the loader and oracle scripts).
2. **Permission to download** the Online Retail II file (about 43.5 MB from the UCI repository) to test the loader locally; the NYC files wait for the reuse answer.
3. **Who asks NYC** whether a hosted reference copy of TLC and 311 data is acceptable, or whether the demo uses only internal test copies.
4. **Approve the read-only `samples.wanderbricks` inspection** as its own live step, with the statements shown first.

Sources: [NYC TLC trip record data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) and its [yellow-taxi data dictionary](https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf); [NYC.gov terms of use](https://www.nyc.gov/home/terms-of-use.page); [NYC Open Data](https://www.nyc.gov/opendata/get-started/what-is-open-data-); [311 service requests](https://data.cityofnewyork.us/api/views/erm2-nwe9.json); [Citi Bike data licence](https://citibikenyc.com/data-sharing-policy); [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online%2Bretail%2Bii); [Databricks Wanderbricks](https://docs.databricks.com/aws/en/discover/wanderbricks-dataset).
