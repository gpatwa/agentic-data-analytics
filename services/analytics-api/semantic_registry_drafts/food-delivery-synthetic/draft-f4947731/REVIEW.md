# Draft review: food-delivery-synthetic `draft-f4947731`

**Status: DRAFT. Not certified. It cannot execute**: the registry refuses a draft, and this directory is outside the
registry root. A person must review, edit and certify it in Git.

- Source: Reply written by a Claude agent standing in for the drafting model; the product's own model was not called. Over the synthetic tier S schema loaded on 2026-11-20.
- Schema fingerprint: `f49477313d15c6931fb33753f87c30d7f471a3fd03cf75b198094f304e44d111`
- Profile fingerprint (aggregate stats only): `None`
- Model: `claude-agent-stand-in-not-the-product-model`, prompt `m2-v2`, drafter `0.1.0`

## Candidates kept (all `candidate`, none certified)

- metric `order_count`: count of `rows` on `orders`
- metric `total_amount_sum`: sum of `orders.total_amount` on `orders`
- metric `subtotal_sum`: sum of `orders.subtotal` on `orders`
- metric `delivery_fee_sum`: sum of `orders.delivery_fee` on `orders`
- metric `discount_amount_sum`: sum of `orders.discount_amount` on `orders`
- metric `tip_amount_sum`: sum of `orders.tip_amount` on `orders`
- metric `avg_delivery_minutes`: average of `orders.delivery_minutes` on `orders`
- metric `avg_prep_minutes`: average of `orders.prep_minutes` on `orders`
- metric `items_count_sum`: sum of `orders.items_count` on `orders`
- metric `payment_amount_sum`: sum of `payments.amount` on `payments`
- metric `payment_event_count`: count of `rows` on `payments`
- metric `order_line_count`: count of `rows` on `order_items`
- metric `quantity_sum`: sum of `order_items.quantity` on `order_items`
- dimension `order_time`: `orders.order_ts` (temporal)
- dimension `order_city`: `orders.city` (categorical)
- dimension `order_country`: `orders.country` (categorical)
- dimension `order_platform`: `orders.platform` (categorical)
- dimension `order_status`: `orders.status` (categorical)
- dimension `order_payment_method`: `orders.payment_method` (categorical)
- dimension `order_currency`: `orders.currency` (categorical)
- dimension `payment_kind`: `payments.kind` (categorical)
- dimension `payment_event_time`: `payments.event_ts` (temporal)

## The key behind each table's metrics (what it proves)

- `order_items`: key (order_id, line_no), evidence **declared**: a constraint recorded in the warehouse catalog. Primary keys on Snowflake and Databricks are not enforced, so this is a claim; confirm the grain is one row per key.
- `orders`: key (row_id), evidence **declared**: a constraint recorded in the warehouse catalog. Primary keys on Snowflake and Databricks are not enforced, so this is a claim; confirm the grain is one row per key.
- `payments`: key (payment_id), evidence **declared**: a constraint recorded in the warehouse catalog. Primary keys on Snowflake and Databricks are not enforced, so this is a claim; confirm the grain is one row per key.

## Terms you would be certifying (names and synonyms the model proposed)

Certifying this contract certifies these words: a question that uses one of them will be matched to the metric or dimension. Remove any you would not want a colleague to use for it.

- `order_count`: display name "Order count"; synonyms "number of orders", "orders placed"
- `total_amount_sum`: display name "Total order amount"; synonyms "total amount", "order total"
- `subtotal_sum`: synonyms "subtotal amount"
- `delivery_fee_sum`: display name "Delivery fees"; synonyms "delivery fee total"
- `discount_amount_sum`: display name "Discount amount"; synonyms "discount total", "discounts given"
- `tip_amount_sum`: display name "Tip amount"; synonyms "tip total", "tips"
- `avg_delivery_minutes`: display name "Average delivery minutes"; synonyms "average delivery time", "delivery duration"
- `avg_prep_minutes`: display name "Average prep minutes"; synonyms "average prep time", "preparation time"
- `items_count_sum`: display name "Items ordered"; synonyms "total items", "items count"
- `payment_amount_sum`: synonyms "payments total"
- `payment_event_count`: display name "Payment event count"; synonyms "number of payments"
- `order_line_count`: display name "Order line count"; no synonyms
- `quantity_sum`: display name "Quantity ordered"; synonyms "item quantity", "units ordered"
- `order_time`: display name "Order time"; synonyms "order timestamp", "order date"
- `order_city`: display name "City"; synonyms "order city"
- `order_country`: display name "Country"; synonyms "order country"
- `order_platform`: display name "Platform"; synonyms "order platform"
- `order_status`: display name "Order status"; synonyms "status of order"
- `order_payment_method`: display name "Payment method"; synonyms "order payment method"
- `order_currency`: display name "Order currency"; no synonyms
- `payment_kind`: display name "Payment kind"; synonyms "payment event kind"
- `payment_event_time`: display name "Payment event time"; synonyms "payment timestamp"

## Dropped, with the reason

- no_metric_for_dimension: 3
- sensitive_column: 1
- synonym_is_another_id: 2
- synonym_overlaps_another_term: 1

Items: `restaurant_name` (sensitive_column), `restaurant_cuisine` (no_metric_for_dimension), `courier_vehicle` (no_metric_for_dimension), `customer_signup_date` (no_metric_for_dimension), `subtotal_sum` (synonym_is_another_id), `payment_amount_sum` (synonym_is_another_id), `order_line_count#0` (synonym_overlaps_another_term)

## Drafted joins (never executable: the compiler has no join support)

- none

## Columns skipped at introspection (type not recognised)

- none

## To certify

1. Read `contract.draft.json` and the descriptions on the nodes in `ontology.candidate.json`.
2. Edit anything wrong. Check each metric's grain and measure against the business definition, and read every proposed term above.
3. In a separate change, move the contract into the registry with `lifecycle: certified` and the metrics `certified`.
   Certification is the semantic owners' decision, recorded in the program file; this draft is not one.
