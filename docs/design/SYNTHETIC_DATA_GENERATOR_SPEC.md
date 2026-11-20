# Synthetic data generator: a specification (2026-11-20)

Status: **G1 to G4 built offline on 2026-11-20** (approved the same day, program file `synthetic_generator_g1_g4`; code in `services/analytics-api/reference_stack/synthetic/`, tests in `tests/test_synthetic_food_delivery.py`, pinned report `docs/evaluation/synthetic-food-delivery-report.md`). Nothing is loaded anywhere. Loading any output into a warehouse (G5) is a separate live step that needs its own approval stating the workspace and schema.

What the build changed from the first draft of this spec:
- **D5 cannot be switched off.** Timestamps are always stored in UTC; the defect is the mismatch with the local business day. The configuration rejects a defect list without D5. D6 (mixed currencies) can be switched off, and then every amount is USD.
- **D7 is the `-1` placeholder only.** The nulls (no courier or no delivery time for a cancelled order) are meaning, not a defect, and stay when D7 is off.
- **Tiers.** T (2,000 orders) was added for the fastest tests. Tiers M and S were run here; **L and XL were not run** and are untested.
- **Acceptance item 5** (the drafter producing metrics only on a declared key) is **covered offline**, with a recorded reply standing in for a model: `services/analytics-api/tests/test_drafter_on_synthetic_keys.py` pins that the validator keeps every proposed item when the declared keys are present and keeps no metric when they are absent. The keys it uses are what the live read-back (L1, 2026-11-20) returned for the loaded tier S tables. It does not test a real model's drafting.
- **Measured:** tier M (1.1 million order rows) generated in about 16 seconds and recomputed in about 9 seconds on the developer's laptop, with no disagreement between the two answer keys. That run is opt-in in the tests (`SYNTHETIC_SLOW=1`).

Context: `REALISTIC_DATA_SOURCE.md`, `REALISTIC_DATA_SOURCE_REVIEW.md` and `FOOD_DELIVERY_DATA_SOURCES.md` found that no real order-level food-delivery data can be hosted, and that only Online Retail II has a clear public licence. The owner's direction (2026-11-20): make synthetic data the main source, food delivery first, other domains later, with Online Retail II kept as the one real anchor.

## 1. Purpose and non-goals

- **Purpose.** Give the product data it can be demonstrated and tested on, in any domain, at any scale, with a licence we own and answers known exactly.
- **Not a goal.** Passing for real data. Everything produced is labelled synthetic (section 9).
- **Not a goal.** Reproducing any real dataset. We copy *kinds of mess* that real sources document, never rows, values or distributions taken from them.
- **Not a goal.** Realistic people. No real person, address, phone number or email appears (section 6).

## 2. The self-grading problem, and the rules that answer it

A generator we write means we wrote both the mess and the answers, so a test can pass because it agrees with our own assumptions. Five rules:

1. **Every defect says how well its real analogue is evidenced.** Each injected defect is marked **reviewed** (documented in a real dataset whose page or dictionary we read: taxi, 311, Online Retail II) or **general** (a pattern common in production data feeds that no source we reviewed documents). A *general* defect is allowed only if the owner accepts it by name (decision 2), and the report always shows the two groups apart.
2. **The owner's 20-question hold-out is written without seeing the generator.** It lives in `reference_stack/m7/coverage-holdout.owner.json`; the generator's own question set (section 7) is the builder's regression set and is never reported as the hold-out result.
3. **The expected answers come from two independent computations** (section 7) that must agree, and neither uses the product.
4. **Defect rates and distributions are parameters in a versioned config**, not hidden constants. Changing one bumps the generator version and every pinned number with it.
5. **One real dataset stays in the mix** (Online Retail II), so there is always a check on data we did not write.

## 3. How it works

- **Engine.** A single deterministic generator over DuckDB SQL and a pinned seed. Randomness comes from a keyed hash of `(seed, table, row number, column)`, so a row never depends on how many rows came before it and any partition can be regenerated alone.
- **Determinism.** Same generator version, config and seed give the same logical content. The check is a *logical digest* (an order-independent hash over every column), not byte-identical Parquet, because Parquet bytes vary by writer version.
- **Output.** Parquet files partitioned by month, a `manifest.json` (generator version, config digest, seed, per-table row counts, logical digest) and `truth.json` (section 7).
- **Scale tiers.**

| Tier | Orders | Used for |
|---|---|---|
| S | 10 thousand | unit tests, runs in seconds |
| M | 1 million | CI and the default demo |
| L | 50 million | large-table behaviour (declared-key reading, profile window, cost estimates) |
| XL | 1 billion | manual only, never in CI |

- **No network, no warehouse, no model** at any point in generation.

## 4. Domain one: food delivery

### 4.1 Tables

The product works on one dataset at a time with no joins, so the generator emits a normalised model *and* a flat table the product reads.

| Table | Grain | Purpose |
|---|---|---|
| `restaurants` | one row per restaurant | name, cuisine, city, opened date |
| `couriers` | one row per courier | vehicle type, hire date, a fake contact |
| `customers` | one row per customer | a surrogate id, a fake contact |
| `orders` | one row per order version | the order as the platform records it (section 4.3) |
| `order_items` | one row per item | item name, quantity, unit price |
| `payments` | one row per payment event | charge, refund, tip adjustment |
| **`orders_flat`** | **one row per order version** | **the product's dataset: `orders` joined to the dimensions, with a surrogate `row_id` declared as the primary key** |

`orders_flat` columns:

| Column | Type | Notes |
|---|---|---|
| `row_id` | integer | surrogate key, declared primary key (what E2 reads) |
| `order_id` | string | repeats when a row is a later version or a duplicate |
| `order_ts` | timestamp | stored in UTC (defect D5) |
| `city`, `country` | string | Zipf-weighted over a fixed list of 40 real city names |
| `restaurant_id`, `restaurant_name`, `cuisine` | string | cuisine has spelling variants (D8) |
| `platform` | string | `app`, `web`, `partner` |
| `status` | string | `delivered`, `cancelled`, `refunded`, `partially_refunded` |
| `subtotal`, `delivery_fee`, `service_fee`, `discount_amount`, `tax`, `tip_amount`, `total_amount` | decimal | exact, in the order's currency |
| `currency` | string | mostly USD, some EUR and GBP (D6) |
| `payment_method` | string | `card`, `wallet`, `cash` |
| `items_count` | integer | |
| `prep_minutes`, `delivery_minutes` | integer | nulls and sentinel values (D7) |
| `courier_id` | string | null when cancelled before dispatch |
| `customer_id` | string | a surrogate, never a real identifier |
| `customer_email`, `courier_phone`, `delivery_address` | string | fake and obviously so (section 6); present to exercise redaction |
| `promo_code` | string | |
| `is_reversal` | boolean | true on a reversal row (D1) |
| `updated_at` | timestamp | when this version was recorded |

### 4.2 Distributions (all parameters, initial values)

- Orders per day follow a weekly pattern (Friday and Saturday higher), a dinner-time peak and a handful of holiday spikes.
- City and restaurant popularity are Zipf; delivery minutes are log-normal per city.
- Roughly 6 percent of orders are cancelled, 2.5 percent refunded in full and 1.5 percent partly refunded.
- Tips are present on about 55 percent of card orders and are recorded on the platform's own cash orders only as zero (D2).

These are starting values chosen for plausibility, not measured from any real platform, and the spec says so in `manifest.json`.

### 4.3 Defects, each with a real analogue

| ID | Defect | Evidence | What it changes for a question |
|---|---|---|---|
| D1 | A cancellation or refund is a separate **reversal row** with negative amounts and `is_reversal = true`, not an update | **Reviewed**: Online Retail II records cancellations as negative quantities on separate "c" invoices | "Revenue" differs depending on whether reversals are included; the certified definition must say |
| D2 | **Cash tips are not recorded**: `tip_amount` is zero when `payment_method = cash` although a tip was paid | **Reviewed**: the taxi trip records exclude cash tips from the tip field | Tip totals and any tip analysis are biased; the definition must state the scope |
| D3 | **Late-arriving versions**: an order is re-recorded with a later `updated_at` and a corrected `status` or amount | **Partly reviewed**: the 311 records carry update dates after creation; re-recorded duplicate versions were not seen in a reviewed source | Counting rows overcounts orders; a question about "orders" needs the latest version |
| D4 | **Duplicate events**: the identical row is written twice (same `order_id`, same `updated_at`) | **General** | Row counts and sums are inflated by about 0.3 percent |
| D5 | **Time zone**: `order_ts` is UTC while the business day is local, including daylight-saving changes | **General** (the taxi dictionary does state its timestamp handling, which supports the concern but is not a documented defect) | "Orders per day" shifts at the day boundary; a time-zone choice is a definition |
| D6 | **Mixed currencies** in one column with a `currency` code | **General** (Online Retail II spans markets, but the review recorded no currency column) | Summing `total_amount` across currencies is meaningless without a rule |
| D7 | **Nulls and placeholder values**: `courier_id` null when no courier; `delivery_minutes` null or `-1` | **Partly reviewed**: the taxi codes include "unknown" values and 311 leaves many columns null; a `-1` sentinel was not documented in a reviewed source | Averages and sums over the column are wrong unless the placeholder is excluded |
| D8 | **Category spelling drift**: `Pizza`, `pizza `, `PIZZA`, `Pizza Place` | **Partly reviewed**: 311 is free-text heavy with text codes; spelling variants were not specifically documented | Group-by on `cuisine` splits one cuisine into several |
| D9 | **Test orders**: a small set of orders at a test restaurant with amounts of 0.01 | **General** | Counts include rows that are not real business |
| D10 | **Orphan references**: a few `restaurant_id` values with no matching restaurant | **General** | Matters for joins, which the product does not do; kept so the refusal is shown honestly |

Each defect has a rate parameter, a seed-independent description in `manifest.json`, and a row-level flag in `truth.json` (not in the data) so the oracle knows exactly which rows carry which defect.

## 5. Later domains (after food delivery works)

Same engine, a domain spec per dataset (tables, columns, distributions, defects with analogues). Candidates, in order:

1. **Retail orders**, close to the real anchor: invoices, products, countries, returns.
2. **SaaS subscriptions**: accounts, plans, monthly recurring amounts, upgrades and cancellations as events.
3. **Support tickets**: ticket, category, priority, opened and resolved times, reopened versions.

Each ships with the same manifest, truth file and oracle. Adding a domain never changes an existing domain's output.

## 6. Sensitive-looking columns, without real people

The product must be shown handling columns that look personal. They are generated so they cannot be real:

- Emails use the reserved domain `example.invalid`, with a surrogate local part (`user_0000123`).
- Phone numbers use the 555-01xx fictional range.
- Delivery addresses are a number plus a street from a short invented word list (`12 Example Street`); no postal code, no coordinates.
- Names are not generated. A column the drafter must redact exists only because of its name and shape.

A test scans the output for anything outside these patterns.

## 7. Truth and the oracle

- **`truth.json`** is written by the generator from its own ledger: exact aggregates for the question set, computed in integer cents so there is no floating-point doubt, under each candidate definition (for example revenue including and excluding reversals).
- **A DuckDB recomputation script** reads only the Parquet and recomputes the same numbers with ordinary SQL. The build test fails if the two disagree. Neither depends on the product.
- **The builder's question set** has two halves, reported separately and never mixed into the owner's hold-out:
  - about 20 questions the product should answer under certified definitions (order count by city and by month, gross order value, tip total, delivered orders, reversals, each with the definition that decides the number);
  - about 10 questions it should refuse today (average order value, refund rate, cancellation rate, on-time percentage, anything needing a join), so the refusal is checked for naming the unsupported part.
- Live runs on a warehouse use the oracle runner from E5, with the oracle statement authored from these definitions.

## 8. Acceptance for the build

1. Same version, config and seed give the same logical digest, twice, and a one-parameter change gives a different one.
2. `truth.json` and the DuckDB recomputation agree on every number.
3. Each defect appears at its configured rate within a stated tolerance, and a deliberately broken generator (a defect switched off) fails a test.
4. The sensitive-column scan finds nothing outside the allowed patterns.
5. `orders_flat` has a unique `row_id`; the drafter, given the Parquet loaded into DuckDB, produces metrics only on a declared key.
6. Tier M generates in a few minutes on a laptop; tier S in seconds.
7. Ruff clean, tests pinned, a generated report kept equal to its generator by a test, as for the earlier evaluations.

## 9. Labelling rule

Every output carries "synthetic": the schema is named `compass_synthetic_<domain>`, every table has a comment starting `SYNTHETIC DATA`, the manifest says so, and any page, screenshot or demo that shows it says so. It is never described as real, as customer data, or as "like" a named company's data.

## 10. Plan

| Stage | What | Needs |
|---|---|---|
| G0 | this specification reviewed | done; decisions 1 to 5 approved 2026-11-20 |
| G1 | engine, config format, determinism and logical digest, tier S | built |
| G2 | food-delivery domain spec and defects D1 to D10 | built |
| G3 | `truth.json`, the DuckDB recomputation and the builder's question set | built |
| G4 | tiers M and L, performance check | tier M built and timed; **tier L not run**; pinned report at tier S |
| G5 | a loader into a Compass-owned reference schema on Databricks and Snowflake | **a separate live approval** naming the workspace, schema and size |
| G6 | the second and third domains | later, after G3 passes |

## Decisions for the owner

1. **Approve G1 to G4 as offline builds?** (G5, the live load, is not part of this.)
2. **Defect list.** D1 and D2 are reviewed; D3, D7 and D8 are partly reviewed; D4, D5, D6, D9 and D10 are *general* and need your acceptance by name (or removal). D10 exists only to show a refusal honestly.
3. **Initial rates** (6 percent cancelled, 0.3 percent duplicates, and so on) are plausible guesses, not measurements. Accept them as starting values, to be tuned only through the versioned config?
4. **City list.** Real city names only, no coordinates. Agree?
5. **Domain order after food delivery:** retail, then SaaS, then support tickets?
