# Restaurant and food-delivery data for the production experience: what exists (2026-11-19)

Status: a research note. Nothing was downloaded or loaded. It adds one domain to `REALISTIC_DATA_SOURCE.md` and `REALISTIC_DATA_SOURCE_REVIEW.md`, at the owner's request ("restaurants data like DoorDash"). Searches were short and licence fields were not opened on every page, so treat each licence line as "to verify before use", as marked.

## The finding in one paragraph

**No real order-level data from DoorDash, Uber Eats, Grubhub, Swiggy or Zomato is openly available.** What exists are community uploads: scraped restaurant listings, and small order tables of about 1,000 to 6,000 rows with unclear provenance and unverified licences, many of them synthetic. The real open data in this domain describes *restaurants as places* (inspections, locations), not *orders as transactions*. So a DoorDash-shaped transaction dataset has to be generated.

## What I found

| Source | What it is | Licence (as found) | Use here |
|---|---|---|---|
| Kaggle food-delivery uploads (Zomato, foodpanda, Uber Eats USA restaurants, "food ordering and delivery app") | scraped listings; small order tables (about 1,000 to 6,000 rows) | not checked page by page; community uploads, provenance unclear | no: too small, unclear origin, cannot be called real |
| NYC DOHMH restaurant inspection results (NYC Open Data) | real inspections: restaurant, cuisine, grade, violations | the NYC reuse terms are unstated, same issue as TLC and 311 | internal reference for messy restaurant dimensions |
| Chicago food inspections (city data portal) | real inspections | **conflicting**: one harvested record says CC0; the Data.gov mirror says no licence information. Check the portal's licence field | possible reference for restaurant dimensions if CC0 is confirmed |
| Foursquare Open Source Places | 100 million plus places with categories, monthly releases | Apache 2.0 per the dataset card; the Hugging Face copy is now gated behind a contact-details prompt, with an Iceberg catalog as the other route; one community thread questions Apache 2.0 as a *data* licence | restaurant and venue reference only, no transactions; check the attribution and gating terms first |
| Yelp Open Dataset | businesses and reviews | personal, educational and academic use; the evaluation licence bars commercial redistribution | **no**: unsuitable for a hosted demo or a commercial product |

## Recommendation: a Compass-owned, seeded, synthetic food-delivery generator

- **Why synthetic is the honest answer here.** It is the only way to get the transaction shape (orders, items, deliveries, couriers, refunds, promotions, tips) at any scale with a licence we own. It would sit beside the real datasets, never replace them.
- **The weakness, stated.** The mess is ours, so it risks the problem the real sources avoid: we wrote the answer key. Three mitigations: (1) model each defect on a documented real one from TLC, 311 and Online Retail II (cash tips missing from the tip field, cancellations recorded as negatives, late-arriving updates, timezone and duplicate-event issues); (2) the owner's hold-out questions are written without seeing the generator; (3) it is always labelled **synthetic**, on the landing page and in any demo.
- **Scale.** A deterministic generator (fixed seed, DuckDB) can produce one million to one billion rows, so it doubles as the large-table test the real sources cannot give us at will.
- **Answer key by construction.** Every number is known exactly, so the independent oracle is the generator's own ledger plus a DuckDB recomputation.

## Product fit: what a DoorDash-style question set would show

The product supports sums and counts over one dataset, with no joins and no ratios. Typical food-delivery questions split accordingly:

| Answerable today | Refused today (a useful honest test) |
|---|---|
| order count by city, cuisine, day; gross order value; tips total; refund amount; delivered orders | average order value, refund rate, cancellation rate, on-time percentage (all ratios); revenue by restaurant name joined to a cuisine table (a join) |

That split is worth showing: the refusals should name the unsupported part and the answerable questions should be right to the cent. It also tells us which ratio support is the most valuable next feature, which is a product decision, not something to build because the data is available.

## Proposed next steps (all offline, none approved to run live)

1. Specify the generator: tables, columns, the list of defects with the real source each copies, and the scale tiers. (A design note.)
2. Build it with a pinned seed and a DuckDB oracle; no warehouse and no network.
3. Verify the Chicago licence field on the portal and the Foursquare gating terms before either is used even as a reference.
4. Loading it into Snowflake or Databricks is a separate, later, live step needing its own approval.

## Sources

- [Kaggle-derived food-delivery listings (Baselight index)](https://baselight.app/u/kaggle/dataset/ahsan81_food_ordering_and_delivery_app_dataset) and the [Mendeley online food delivery dataset](https://data.mendeley.com/datasets/5nck6tjr28/1) (examples of the small community datasets)
- [Foursquare Open Source Places (Hugging Face card)](https://huggingface.co/datasets/foursquare/fsq-os-places) and [Foursquare's note on its evolution](https://foursquare.com/resources/blog/data/evolving-fsq-open-source-places/)
- [Chicago Food Inspections (Data.gov mirror)](https://catalog.data.gov/dataset/food-inspections)
- [DOHMH New York City Restaurant Inspection Results (Data.gov mirror)](https://catalog.data.gov/dataset/dohmh-new-york-city-restaurant-inspection-results)
- Yelp: the Open Dataset and Evaluation Data License terms as summarised in search results; the agreement itself was not opened.
