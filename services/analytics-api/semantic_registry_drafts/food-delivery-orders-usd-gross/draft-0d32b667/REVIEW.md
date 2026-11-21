# Draft review: food-delivery-orders-usd-gross `draft-0d32b667`

**Status: DRAFT. Not certified. It cannot execute**: the registry refuses a draft, and this directory is outside the
registry root. A person must review, edit and certify it in Git.

- Source: Reply written by a Claude agent standing in for the drafting model; the product's own model was not called. Over the orders_usd_gross view (USD, not cancelled, gross), key order_id with profiled_full evidence.
- Schema fingerprint: `0d32b66779290150ac35cf21b330af923f53633bb2b3ac1a2b4d054e75b111dc`
- Profile fingerprint (aggregate stats only): `None`
- Model: `claude-agent-stand-in-not-the-product-model`, prompt `m2-v2`, drafter `0.1.0`

## Candidates kept (all `candidate`, none certified)

- metric `order_count`: count of `rows` on `orders_usd_gross`
- metric `gross_order_value`: sum of `orders_usd_gross.total_amount` on `orders_usd_gross`
- metric `gross_subtotal`: sum of `orders_usd_gross.subtotal` on `orders_usd_gross`
- metric `delivery_fee_total`: sum of `orders_usd_gross.delivery_fee` on `orders_usd_gross`
- metric `service_fee_total`: sum of `orders_usd_gross.service_fee` on `orders_usd_gross`
- metric `discount_total`: sum of `orders_usd_gross.discount_amount` on `orders_usd_gross`
- metric `tip_total`: sum of `orders_usd_gross.tip_amount` on `orders_usd_gross`
- dimension `order_time`: `orders_usd_gross.order_ts` (temporal)
- dimension `city`: `orders_usd_gross.city` (categorical)
- dimension `country`: `orders_usd_gross.country` (categorical)
- dimension `cuisine`: `orders_usd_gross.cuisine` (categorical)
- dimension `platform`: `orders_usd_gross.platform` (categorical)
- dimension `order_status`: `orders_usd_gross.status` (categorical)
- dimension `payment_method`: `orders_usd_gross.payment_method` (categorical)

## The key behind each table's metrics (what it proves)

- `orders_usd_gross`: key (order_id), evidence **profiled_full**: unique and never null across the whole table when it was profiled. A fact about today's rows, not a constraint; confirm the grain.

## Terms you would be certifying (names and synonyms the model proposed)

Certifying this contract certifies these words: a question that uses one of them will be matched to the metric or dimension. Remove any you would not want a colleague to use for it.

- `order_count`: display name "Order count"; synonyms "number of orders"
- `gross_order_value`: display name "Gross order value"; synonyms "total amount", "gross total amount"
- `gross_subtotal`: display name "Gross subtotal"; synonyms "subtotal amount"
- `delivery_fee_total`: display name "Delivery fees"; synonyms "delivery fee"
- `service_fee_total`: display name "Service fees"; synonyms "service fee"
- `discount_total`: display name "Discounts given"; synonyms "discount amount"
- `tip_total`: display name "Tips"; synonyms "tip amount"
- `order_time`: display name "Order time"; synonyms "order timestamp"
- `city`: display name "City"; no synonyms
- `country`: display name "Country"; no synonyms
- `cuisine`: display name "Cuisine"; no synonyms
- `platform`: display name "Platform"; no synonyms
- `order_status`: display name "Order status"; synonyms "status"
- `payment_method`: display name "Payment method"; no synonyms

## Dropped, with the reason

- synonym_is_another_id: 1

Items: `order_count#1` (synonym_is_another_id)

## Drafted joins (never executable: the compiler has no join support)

- none

## Columns skipped at introspection (type not recognised)

- none

## To certify

1. Read `contract.draft.json` and the descriptions on the nodes in `ontology.candidate.json`.
2. Edit anything wrong. Check each metric's grain and measure against the business definition, and read every proposed term above.
3. In a separate change, move the contract into the registry with `lifecycle: certified` and the metrics `certified`.
   Certification is the semantic owners' decision, recorded in the program file; this draft is not one.
