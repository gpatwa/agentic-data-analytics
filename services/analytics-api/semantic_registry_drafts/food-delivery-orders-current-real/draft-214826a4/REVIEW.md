# Draft review: food-delivery-orders-current-real `draft-214826a4`

**Status: DRAFT. Not certified. It cannot execute**: the registry refuses a draft, and this directory is outside the
registry root. A person must review, edit and certify it in Git.

- Source: Databricks catalog `dev`, schema `compass_synthetic_food_delivery`, tables orders_current (via an injected session)
- Schema fingerprint: `214826a46be14f9a75bccefae9a14098a45ebdbcd06f851627c62834108d79cb`
- Profile fingerprint (aggregate stats only): `7b76d5e0c171a15f4050cf880b3847d68e96bb6a4135cfdc6b642e5afc5434ea`
- Model: `gpt-6-luna`, prompt `m2-v2`, drafter `0.1.0`

## Candidates kept (all `candidate`, none certified)

- metric `order_count`: count of `rows` on `orders_current`
- dimension `order_timestamp`: `orders_current.order_ts` (temporal)
- dimension `city`: `orders_current.city` (categorical)
- dimension `country`: `orders_current.country` (categorical)
- dimension `order_status`: `orders_current.status` (categorical)
- dimension `platform`: `orders_current.platform` (categorical)

## The key behind each table's metrics (what it proves)

- `orders_current`: key (row_id), evidence **profiled_full**: unique and never null across the whole table when it was profiled. A fact about today's rows, not a constraint; confirm the grain.

## Terms you would be certifying (names and synonyms the model proposed)

Certifying this contract certifies these words: a question that uses one of them will be matched to the metric or dimension. Remove any you would not want a colleague to use for it.

- `order_count`: display name "Order Count"; no synonyms
- `order_timestamp`: display name "Order Timestamp"; no synonyms
- `city`: display name "City"; no synonyms
- `country`: display name "Country"; no synonyms
- `order_status`: display name "Order Status"; no synonyms
- `platform`: display name "Platform"; no synonyms

## Dropped, with the reason

- none

## Drafted joins (never executable: the compiler has no join support)

- none

## Columns skipped at introspection (type not recognised)

- none

## Checks run on this draft

- validator escapes: 0
- leaked secrets: 0; injected canaries: 0

## To certify

1. Read `contract.draft.json` and the descriptions on the nodes in `ontology.candidate.json`.
2. Edit anything wrong. Check each metric's grain and measure against the business definition, and read every proposed term above.
3. In a separate change, move the contract into the registry with `lifecycle: certified` and the metrics `certified`.
   Certification is the semantic owners' decision, recorded in the program file; this draft is not one.
