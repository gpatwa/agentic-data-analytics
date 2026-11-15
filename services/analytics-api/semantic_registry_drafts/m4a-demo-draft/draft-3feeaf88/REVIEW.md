# Draft review: m4a-demo-draft `draft-3feeaf88`

**Status: DRAFT. Not certified. It cannot execute**: the registry refuses a draft, and this directory is outside the
registry root. A person must review, edit and certify it in Git.

- Source: PostgreSQL schema `public`, tables demo_sales_orders (host local)
- Schema fingerprint: `3feeaf88eff579db4a2b2050461e8df07c71fd011f725229017af9ed6404ab0b`
- Profile fingerprint (aggregate stats only): `7188c234b31fd7e20c0d743a527ae6f7f0ce7794d16d7155b5f23bbd1fed170d`
- Model: `gpt-6-luna`, prompt `m2-v1`, drafter `0.1.0`

## Candidates kept (all `candidate`, none certified)

- metric `order_count`: count of `rows` on `demo_sales_orders`
- metric `order_amount`: sum of `demo_sales_orders.amount` on `demo_sales_orders`
- dimension `order_status`: `demo_sales_orders.status` (categorical)
- dimension `order_created_at`: `demo_sales_orders.created_at` (temporal)

## Dropped, with the reason

- none

## Drafted joins (never executable: the compiler has no join support)

- none

## Columns skipped at introspection (type not recognised)

- none

## Checks run on this draft

- validator escapes: 0
- leaked secrets: 0; injected canaries: 0
- recall against the certified reference: metrics 1.00, dimensions 1.00

## To certify

1. Read `contract.draft.json` and the descriptions on the nodes in `ontology.candidate.json`.
2. Edit anything wrong. Check each metric's grain and measure against the business definition.
3. In a separate change, move the contract into the registry with `lifecycle: certified` and the metrics `certified`.
   Certification is the semantic owners' decision, recorded in the program file; this draft is not one.
