# A realistic data source for the production experience: fourteen candidates compared, four to review (2026-11-19)

Status: **research and a shortlist for the owner to review; nothing is loaded or run.** Loading data into the owner's Snowflake or Databricks account is a live step that needs explicit approval with the statements shown first (CLAUDE.md). Facts come from vendor, portal and dataset pages read on 2026-11-19 and are **not verified on an account**; items marked *verify* are checks to make before relying on them. Ratings in the matrix are my judgement against the criteria below, not measurements. It supersedes the "demo data" question deferred on 2026-11-18 and extends `REFERENCE_DATA_DEMO_PLAN.md` (R1 built: three small synthetic tables).

## What "realistic" has to mean here

The validation so far used a 30-row synthetic table. Criteria, in the order they matter for what we are testing:

1. **Real mess**: nulls, outliers, skew, odd categories, dates running to the edge of the data.
2. **Scale** big enough that the cost estimate, row cap, timeout and small-group suppression bite (millions of rows).
3. **A shape the product can use today**: one dataset per query, no joins, no ratios. A normalised source is usable only through a denormalised table or view (the "gold layer" enterprises really expose to BI), and the view becomes part of what is certified.
4. **Loadable identically on both engines**, so parity can be checked against an independent oracle.
5. **A licence that allows a product demo**, and no real person in the data.
6. **Business relevance** to a data leader's questions (sales, operations, support, finance), and sensitive-column realism for the redaction rule.
7. Cheap and repeatable: scripted load, checksum, removal.

## The comparison

Rating: **H** high, **M** medium, **L** low. "Licence" is what I found; *verify* means the text was not read.

| # | Source | Real? | Scale | Shape for us | Licence | Business fit | Notes |
|---|---|---|---|---|---|---|---|
| 1 | [NYC TLC yellow-taxi trips](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) | Real | H (about 1.5 billion rows overall; a month is millions) | **H**: one wide fact table | NYC terms of use via the [AWS registry](https://registry.opendata.aws/nyc-tlc-trip-records-pds/); *verify* redistribution | M (mobility, fares, tips) | Monthly Parquet; same file loads on both engines |
| 2 | [NYC 311 service requests](https://catalog.data.gov/dataset/311-service-requests-from-2010-to-present) | Real | H (over 40 million rows for 2020 on; split into two 10-year datasets) | **H**: one wide table | No licence stated; NYC [terms of use](https://www.nyc.gov/html/data/terms.html) apply; *verify* | **H** (ticket-style operations: agency, complaint type, status, open and close times) | Category-heavy, many nulls, free-text columns to exclude; no personal identity in the data |
| 3 | [Citi Bike trips](https://ride.citibikenyc.com/data-sharing-policy) | Real | H (about 320 million trips cumulative, per a third-party count) | **H**: one trips table | **Clear**: non-exclusive licence for any lawful purpose, as-is, trademarks excluded | M | A clean fallback if the TLC terms do not suit |
| 4 | Divvy trips (Chicago) | Real | H | H | Divvy data licence agreement; text not read, *verify* | M | Same shape as Citi Bike |
| 5 | Chicago taxi trips and crimes ([taxi](https://catalog.data.gov/dataset/taxi-trips-2013-2023)) | Real | H | H | "No licence information provided" on the catalogue records | M (crime: low commercial relevance, sensitive) | Skip until the portal terms are read |
| 6 | [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online%2Bretail%2Bii) | Real (a UK online retailer, Dec 2009 to Dec 2011) | M (about 1.07 million invoice lines) | **H**: one table of invoice lines | **CC BY 4.0** (attribution; any purpose) | **H** (sales, returns, customers, countries) | Cancellations and returns, wholesalers, missing customer ids: real sales mess; small for a scale test |
| 7 | Olist Brazilian e-commerce | Real, anonymised | M (about 100,000 orders) | M: 8 or 9 normalised tables, needs a denormalised view | Reported **CC BY-NC-SA 4.0**, a non-commercial licence ([one secondary source](https://scripts-and-tables.github.io/daa/data/)); *verify on Kaggle* | H | Already in this repo with a certified contract; **internal use only until the licence is checked** |
| 8 | Wide World Importers / AdventureWorks | Synthetic (Microsoft) | M | M: OLTP plus a data-warehouse star schema | **MIT** ([Microsoft samples](https://learn.microsoft.com/en-us/sql/samples/wide-world-importers-what-is)) | H (wholesale sales, purchasing) | Distributed for SQL Server; conversion work; modest size |
| 9 | Databricks [`samples.wanderbricks`](https://docs.databricks.com/aws/en/discover/wanderbricks-dataset) | Simulated (travel bookings) | *verify* | M: many tables (users, hosts, listings, bookings, payments, reviews, support, clickstream) | Vendor sample, built into Unity Catalog workspaces | **H** (enterprise-shaped, with name and email columns that exercise redaction) | Databricks only; read-only shared catalog, so likely copied (see below) |
| 10 | Databricks [`samples.nyctaxi.trips`, `tpch`, `tpcds_sf*`](https://docs.databricks.com/dbfs/databricks-datasets.html) | Taxi: real; TPC: synthetic | taxi *verify* small; TPC up to about 1 TB | taxi H; TPC needs joins | Vendor samples | L to M | Zero-setup smoke test |
| 11 | Snowflake [`SNOWFLAKE_SAMPLE_DATA`](https://docs.snowflake.com/en/user-guide/sample-data-tpch) (TPC-H, TPC-DS) | Synthetic benchmark | H (TPC-DS 10 TB `STORE_SALES` is nearly 29 billion rows) | L: normalised, needs joins | Vendor sample | M | For **scale and cost** tests, not realistic distributions |
| 12 | Snowflake Marketplace free public data ([Finance and Economics](https://data-docs.snowflake.com/foundations/products/finance-economics)) | Real, public domain | M | M: time series and indicators | Free for internal use per Snowflake's FAQ; *verify* listings are current | M (finance) | Snowflake only; a shared listing |
| 13 | [CTU "Financial" (Berka bank)](https://relational.fel.cvut.cz/dataset/Financial) | Real, anonymised, 1990s | M (about 1.09 million rows over 8 tables) | M: normalised | **Not stated** on the pages read | M (banking) | Old; skip unless a finance scene is needed and the terms are clear |
| 14 | Instacart market basket | Real | H (about 3 million orders) | M | Terms say they can change at any time; Kaggle "research use" (secondhand) | M | **Avoid**: unclear terms |
| 15 | Our own R1 synthetic tables | Synthetic, seeded, with an oracle | L (20,000 and 5,000 rows) | H | Ours | M | Needed for redaction, suppression and poisoned-metadata scenes; not a production stand-in |

## Two findings that change the plan

1. **Built-in shared sample data probably cannot be granted table by table.** Our connection gives a read-only identity `SELECT` on exactly the tables the admin names. Snowflake's sample database and Marketplace listings are shared databases (consumers get imported privileges, not per-object grants), and Databricks' `samples` catalog is read-only and system-owned. *Verify both.* If true, the product cannot connect to them the way it connects to a customer's tables, and the realistic path is to **copy the rows into our own database or catalog** (a persistent reference schema, as `REFERENCE_DATA_DEMO_PLAN.md` R4 proposes), then connect to that. Copying a licensed or vendor dataset also needs its terms read for redistribution.
2. **The compiler's single-dataset limit decides the shape.** Rows 1 to 6 are already one wide table. Rows 7 to 13 need a denormalised table built by the admin, which is realistic, but it is part of what we certify, and any question that truly needs a join waits for a join packet.

## The shortlist: four to review

Chosen to cover different domains and different things we need to prove, not to be four copies of the same test.

| Pick | Why it earns a place | What it proves in our product | Check before use |
|---|---|---|---|
| **A. NYC TLC taxi trips** (or **Citi Bike** as a clean-licence swap) | Real, huge, one wide fact table; same files on both engines | Cost estimate, row cap, timeout, suppression on small zone pairs, parity against a plain-Python or DuckDB oracle at scale | NYC terms for redistribution; or use Citi Bike, whose licence is explicit |
| **B. NYC 311 service requests** | The most "ticket and operations" shaped real data: statuses, agencies, closure times, many nulls, free-text columns | Refusing free-text and location columns, category-heavy dimensions, a status lifecycle metric, large-table behaviour | The dataset's own terms page; confirm no personal fields in the columns we keep |
| **C. UCI Online Retail II** | Real sales lines with returns and cancellations, and a **clear CC BY 4.0 licence**, so it can be shown publicly; the commercial-safe replacement for Olist | Revenue definitions that must treat cancellations correctly, country and customer dimensions, a certified sales contract a buyer recognises | Attribution wording; confirm the row count and the cancelled-invoice convention on load |
| **D. Databricks `samples.wanderbricks`** | An enterprise-shaped, multi-table dataset with name and email columns, available in every workspace for free | The redaction rule on realistic personal columns, building a denormalised view, and **settling the grant-versus-copy question** on a real shared catalog | `SHOW TABLES` and row counts; whether `GRANT SELECT` on a `samples` table is possible |

Kept alongside: the **R1 synthetic tables** for the injection and small-cohort scenes. Kept out of public use until checked: **Olist**. Used only to test scale and cost, never shown as realistic: **TPC-H and TPC-DS**. Dropped for now: Divvy, Chicago, Berka and Instacart (terms unclear or unread), and the Snowflake Marketplace feeds (a shared listing with a narrow finance angle).

**What production still needs that no static dataset gives:** schema drift, late and duplicate data, concurrency, and a customer's own naming and history. Those are pilot-with-a-design-partner tests, not data choices, and the page copy should keep saying so.

## Decisions for you

1. **Which of A to D to review in depth.** Recommended: all four, in the order C (clean licence, quickest to a recognisable demo), A, B, D.
2. **Where the loaded copies live:** Compass-owned reference schemas (recommended), versus isolated permanent schemas in the existing personal accounts for now.
3. **Olist:** I check the Kaggle licence before it appears anywhere public (recommended) and treat it as internal until then.
4. **Order of work:** DuckDB or PostgreSQL first for the oracle and a drafted contract (offline, no approval); then one approved live step on Snowflake and Databricks together, with the statements shown first.

A contract on any of this data is drafted by the drafter and certified by a person, as for the sandbox; nothing here is certified.

Sources: Databricks [sample datasets](https://docs.databricks.com/dbfs/databricks-datasets.html), [Wanderbricks](https://docs.databricks.com/aws/en/discover/wanderbricks-dataset) and [TPC-DS evaluation](https://docs.databricks.com/aws/sql/tpcds-eval); Snowflake [TPC-H](https://docs.snowflake.com/en/user-guide/sample-data-tpch), [TPC-DS](https://docs.snowflake.com/en/user-guide/sample-data-tpcds) and [public data](https://data-docs.snowflake.com/foundations/products/finance-economics); NYC [TLC](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page), [Open Data terms](https://www.nyc.gov/html/data/terms.html) and [311](https://catalog.data.gov/dataset/311-service-requests-from-2010-to-present); [Citi Bike licence](https://ride.citibikenyc.com/data-sharing-policy); [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online%2Bretail%2Bii); [Wide World Importers](https://learn.microsoft.com/en-us/sql/samples/wide-world-importers-what-is); [CTU Financial](https://relational.fel.cvut.cz/dataset/Financial); the [Instacart terms copy](https://gist.github.com/jeremystan/582eba13d6ee27ed465c43dc78934700).
