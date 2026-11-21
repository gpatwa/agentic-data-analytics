# Draft review: food-delivery-orders-current `draft-7e7bf1e2`

**Status: DRAFT. Not certified. It cannot execute**: the registry refuses a draft, and this directory is outside the
registry root. A person must review, edit and certify it in Git.

- Source: Reply written by a Claude agent standing in for the drafting model; the product's own model was not called. Over the orders_current view, key order_id with profiled_full evidence (a view cannot declare one).
- Schema fingerprint: `7e7bf1e27bef3a8cf231757f4d34267b5a400a7ebee01f349b97914f42c93e9a`
- Profile fingerprint (aggregate stats only): `None`
- Model: `claude-agent-stand-in-not-the-product-model`, prompt `m2-v2`, drafter `0.1.0`

## Candidates kept (all `candidate`, none certified)

- metric `order_count`: count of `rows` on `orders_current`
- metric `total_order_amount`: sum of `orders_current.total_amount` on `orders_current`
- metric `total_tip_amount`: sum of `orders_current.tip_amount` on `orders_current`
- metric `total_discount_amount`: sum of `orders_current.discount_amount` on `orders_current`
- metric `average_delivery_minutes`: average of `orders_current.delivery_minutes` on `orders_current`
- metric `average_prep_minutes`: average of `orders_current.prep_minutes` on `orders_current`
- dimension `order_ts`: `orders_current.order_ts` (temporal)
- dimension `city`: `orders_current.city` (categorical)
- dimension `country`: `orders_current.country` (categorical)
- dimension `cuisine`: `orders_current.cuisine` (categorical)
- dimension `platform`: `orders_current.platform` (categorical)
- dimension `status`: `orders_current.status` (categorical)
- dimension `payment_method`: `orders_current.payment_method` (categorical)
- dimension `currency`: `orders_current.currency` (categorical)

## The key behind each table's metrics (what it proves)

- `orders_current`: key (order_id), evidence **profiled_full**: unique and never null across the whole table when it was profiled. A fact about today's rows, not a constraint; confirm the grain.

## Terms you would be certifying (names and synonyms the model proposed)

Certifying this contract certifies these words: a question that uses one of them will be matched to the metric or dimension. Remove any you would not want a colleague to use for it.

- `order_count`: display name "Order count"; synonyms "number of orders"
- `total_order_amount`: display name "Total order amount"; synonyms "total amount", "order total"
- `total_tip_amount`: display name "Total tip amount"; synonyms "tip amount", "tips total"
- `total_discount_amount`: display name "Total discount amount"; synonyms "discount amount", "discounts total"
- `average_delivery_minutes`: display name "Average delivery minutes"; synonyms "delivery minutes", "delivery time"
- `average_prep_minutes`: display name "Average prep minutes"; synonyms "prep minutes", "preparation time"
- `order_ts`: display name "Order timestamp"; synonyms "order time", "order date"
- `city`: display name "City"; no synonyms
- `country`: display name "Country"; no synonyms
- `cuisine`: display name "Cuisine"; no synonyms
- `platform`: display name "Platform"; no synonyms
- `status`: display name "Order status"; no synonyms
- `payment_method`: display name "Payment method"; no synonyms
- `currency`: display name "Currency"; no synonyms

## Dropped, with the reason

- sensitive_column: 1
- synonym_is_another_id: 1

Items: `restaurant_name` (sensitive_column), `order_count#1` (synonym_is_another_id)

## Drafted joins (never executable: the compiler has no join support)

- none

## Columns skipped at introspection (type not recognised)

- none

## To certify

1. Read `contract.draft.json` and the descriptions on the nodes in `ontology.candidate.json`.
2. Edit anything wrong. Check each metric's grain and measure against the business definition, and read every proposed term above.
3. In a separate change, move the contract into the registry with `lifecycle: certified` and the metrics `certified`.
   Certification is the semantic owners' decision, recorded in the program file; this draft is not one.
