# Draft review: sandbox-databricks `draft-80200f1c`

**Status: DRAFT. Not certified. It cannot execute**: the registry refuses a draft, and this directory is outside the
registry root. A person must review, edit and certify it in Git.

- Source: Databricks sandbox catalog `compass_connect_spike_889cfc3a`, schema `data` (synthetic data, via the connection's own principal)
- Schema fingerprint: `80200f1cf7cf20cc4cb07e847fa326427fd84aa6ea177fc7e0a3a04bc8f2b082`
- Profile fingerprint (aggregate stats only): `6d7122bc303578a9fd30ff018b9125c73a426dcac1114895a97542ea44d0b9f7`
- Model: `gpt-6-luna`, prompt `m2-v1`, drafter `0.1.0`

## Candidates kept (all `candidate`, none certified)

- metric `order_count`: count of `rows` on `orders`
- metric `order_amount_sum`: sum of `orders.amount` on `orders`
- dimension `amount`: `orders.amount` (numeric)

## Dropped, with the reason

- identifier_not_dimension: 1

Items: `order_id` (identifier_not_dimension)

## Drafted joins (never executable: the compiler has no join support)

- none

## Columns skipped at introspection (type not recognised)

- none

## Checks run on this draft

- validator escapes: 0
- leaked secrets: 0; injected canaries: 0

## To certify

1. Read `contract.draft.json` and the descriptions on the nodes in `ontology.candidate.json`.
2. Edit anything wrong. Check each metric's grain and measure against the business definition.
3. In a separate change, move the contract into the registry with `lifecycle: certified` and the metrics `certified`.
   Certification is the semantic owners' decision, recorded in the program file; this draft is not one.
