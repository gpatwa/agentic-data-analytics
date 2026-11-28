# The `orders_current` contract draft: what to read before certifying (2026-11-21)

Status: **update 2026-11-21: the owner chose "Counts now, USD amounts next", so `order_count` and the eight dimensions are certified as contract version 2026-11-21 (`services/analytics-api/semantic_registry_synthetic/contracts/food-delivery-orders-current-v1.json`; program file `orders_current_certify_counts`). The amount sums, the tip sum and the averages are NOT certified. No synonym is certified.** The text below is the review as the owner read it. Files: `services/analytics-api/semantic_registry_drafts/food-delivery-orders-current/draft-7e7bf1e2/` (`contract.draft.json`, `ontology.candidate.json`, `REVIEW.md`). It was drafted by a Claude agent standing in for the drafting model (the product's own model was not called) and then passed through the product's real validator. Over the view `dev.compass_synthetic_food_delivery.orders_current` (one row per real order: latest recorded version, no reversal rows, no test restaurant), keyed on `order_id`. A view cannot declare a key, so the evidence is `profiled_full`: the live run counted 9,995 rows and 0 duplicate `order_id`.

**What the validator kept:** 6 metrics, 8 dimensions. It dropped `restaurant_name` (a sensitive-looking column) and one synonym that equalled another id.

## Measured against the generator's answer key (tier S, DuckDB over the same logic)

| Draft metric | What it computes | Answer key | Verdict |
|---|---|---|---|
| `order_count` | rows in the view | 9,995 distinct orders | **Correct.** This is what the view was built for. |
| `total_order_amount` | sum of `total_amount` over every row | 767,962.37 here; the key's gross USD is 545,012.03 | **Wrong as a business number.** It adds USD, EUR and GBP together (7,548 / 1,443 / 1,004 orders) and includes 591 cancelled orders that still carry a total. 41% too high. |
| `total_tip_amount` | sum of `tip_amount` | cash tips are not recorded in the field (about 985 of 9,995 orders are cash) | **Understates tips**, and also mixes currencies. |
| `total_discount_amount` | sum of `discount_amount` | not in the answer key | Mixes currencies. Not checkable here. |
| `average_delivery_minutes` | average of `delivery_minutes` | 23.63 excluding the `-1` placeholder; 23.51 as drafted | **Slightly biased**: 45 rows hold `-1` as "unknown", and 591 are null (cancelled orders). |
| `average_prep_minutes` | average of `prep_minutes` | not in the answer key | Same placeholder risk as delivery minutes. |

## What I recommend you certify, and what not

- **Certify now, if you agree:** `order_count`, and the dimensions `status`, `city`, `country`, `cuisine`, `platform`, `payment_method`, `currency`, `order_ts`. These give counts by group and match the answer key (the loader's live checks matched the status counts and the 9,995 total).
- **Do not certify yet:** the four amount sums and the two averages. They would turn into confident wrong numbers (`total_order_amount` by 41%). Each needs a rule first: a currency rule (convert, or restrict to one currency by a certified filter), whether cancelled orders count, and how the `-1` placeholder is treated. The compiler cannot convert currency or multiply columns, so the honest options are a second curated view with a USD-only amount column, or certified metrics that require a currency filter.
- A certified `order_count` should keep `required_filter_ids` empty: the view already removes duplicates, reversals and test orders, and that definition lives in the view, which the contract cannot see. **If the view definition changes, the certified meaning changes with it**, so the view and the contract must be versioned together.

## What stays gated

Certification is yours. This note and the draft approve nothing, grant nothing and call no model. The next steps it enables, each needing its own approval: the Connect registration grant (statements printable with `python -m app.connect.registration_print`), and a real-model drafter run.

## USD amounts next (built offline, not applied)

A second curated view, `orders_usd_gross`, carries the currency rule the compiler cannot: one row per real order in USD that was not cancelled, from `orders_current`. Amounts are gross: refunds are reversal rows in the source and are not netted. On DuckDB it reproduces the answer key's gross USD value exactly (545,012.03 on tier S) with unique orders and no cancelled rows. Creating it in the warehouse (`python -m reference_stack.loaders synthetic ... --usd-view-only`, 5 statements) needs an approval that names `orders_usd_gross`. After it exists, amount metrics (`order_amount_usd_sum`, and a count of USD orders) can be drafted on it for the next certification round. Tips and delivery averages still need their own rules (cash tips are not recorded; the `-1` placeholder).

## Amount metrics drafted on `orders_usd_gross` (2026-11-21): a draft, nothing certified

Draft: `services/analytics-api/semantic_registry_drafts/food-delivery-orders-usd-gross/draft-0d32b667/`, drafted by a Claude agent standing in for the model and passed through the real validator over the live view (key `order_id`, profiled; the live run found it unique). 7 metrics, 7 dimensions kept; one synonym dropped (it equalled another id). Measured on tier S (7,092 USD orders that were not cancelled):

| Draft metric | Value | Answer key | Verdict |
|---|---|---|---|
| `order_count` | 7,092 | n/a (USD orders) | Fine: counts the view's rows. |
| `gross_order_value` (sum of `total_amount`) | 545,012.03 | **545,012.03** | **Matches the key exactly.** The currency rule now lives in the view. Candidate to certify. |
| `gross_subtotal`, `delivery_fee_total`, `service_fee_total`, `discount_total` | 409,660.78; 17,775.08; 61,451.18; 12,571.40 | not in the key | Single-currency now; not checkable against the key. Certify only if you want them, after reading. |
| `tip_total` | 35,923.40 | card tips alone are 24,717.49 | **Ambiguous.** The field holds card and wallet tips, not cash tips (they are never recorded). "Tips" is therefore an understatement of real tips and does not equal the key's card-only figure. Do not certify under that name. |

Recommendation: certify `gross_order_value` (and `order_count` over this view if you want a USD-only count) as the next round; hold `tip_total` until it is named for what it holds ("recorded tips, excluding cash"); read the fee and discount sums before deciding. Gross means refunds are separate reversal rows and are not netted: a "net" value needs its own rule. Certification is yours.

**Update 2026-11-21: certified on the owner's choice** (program file `orders_usd_gross_certify_amounts`). Contract `food-delivery-orders-usd-gross` version 2026-11-21 (`services/analytics-api/semantic_registry_synthetic_usd/contracts/food-delivery-orders-usd-gross-v1.json`) holds `order_count`, `gross_order_value`, `gross_subtotal`, `delivery_fee_total`, `service_fee_total` and `discount_total`, with seven dimensions, no synonyms and no restricted field. **`tip_total` is not certified.** The compiler reproduces the answer key's gross USD value on tier T data in DuckDB. It sits in a registry root of its own (a root holds one certified contract per tenant), so it is certified but served nowhere. A net value (refunds netted) is not certified.

## Found by the DuckDB end-to-end run (2026-11-28): the certified counts-and-dimensions contract answers cuisine and month questions wrongly

Running the 30 builder questions through the real governed path over `orders_current` (tier S, DuckDB), with the certified `food-delivery-orders-current-v1`: 8 of 13 answerable questions were answered correctly and **5 were wrong numbers**. Cuisine: "orders for pizza" is 170 on the exact spelling (10 if the model guesses the lowercase value) against 200 in the key; sushi 22 against 676. The `cuisine` dimension reads the order-level copy that carries the planted spelling drift. Months: the only time dimension is `order_ts` in UTC, so January 2025 is 865 against 873, July 811 against 817, December 872 against 876. The product answered the certified columns faithfully; the columns are what is wrong. Details: `docs/evaluation/e2e-duckdb-synthetic-certified-report.md`.

Built offline, not applied: a new view `orders_clean` (cuisine and restaurant name from `restaurants`; `order_local_ts` in each city's time zone) and a draft contract over it. Against the same questions it answers 17 of 17 correctly with no wrong number (`docs/evaluation/e2e-duckdb-synthetic-report.md`). It is a NEW view and a NEW contract so that `orders_current` and its certified meaning stay as they are. Decisions that are yours: creating `orders_clean` in the warehouse (an approval that names it), certifying the new contract, and what to do with the certified `cuisine` and `order_ts` dimensions in the meantime.
