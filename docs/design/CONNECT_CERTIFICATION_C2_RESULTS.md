# Certification C2 (draft on the sandbox): results (2026-11-18)

Plan: `CONNECT_CERTIFICATION_PLAN.md`. Statements: `CONNECT_CERTIFICATION_STATEMENTS.md`. Approved by the owner on 2026-11-18 (program file `connect_certification_c2`). One workspace (the owner's validation workspace), synthetic data, one model (the pilot's `gpt-6-luna`). No secret, host id or key is recorded.

**Run suffix: `889cfc3a`.** C4 must use the same suffix so the draft's physical names (`compass_connect_spike_889cfc3a.data.orders`) match.

## What ran

The sandbox was stood up as the product does (catalog, three synthetic tables, the generated registration script, custody of the principal's secret, a table-read connection test). The schema and a redacted profile of **one** ceiling table (`orders`) were read through the connection's own principal, the model drafted from the redacted view only, every candidate was compiled for Databricks and `EXPLAIN`ed, and the draft was written to `services/analytics-api/semantic_registry_drafts/sandbox-databricks/draft-80200f1c/`. Everything created was then removed: 7 removals, none left, the sweep found no catalog, principal or warehouse permission of ours. The leak scan found nothing, including in the draft files, and the audit chain verifies.

One earlier attempt, minutes before, stopped at the model call (`BadRequestError`) because I had not set the reasoning-model settings the pilot uses (API version `2025-04-01-preview`, effort `medium`); its teardown was also clean and nothing was written. The run above is the second.

## What it showed

| Question | Result |
|---|---|
| Does the drafter work through a connection's own principal on Databricks? | **Yes** on this tiny schema: introspection, profile, key inference, draft and EXPLAIN dry runs all ran. |
| What did it keep? | 2 candidate metrics (`order_count`, `order_amount_sum`) and 1 candidate dimension (`amount`, numeric). 1 drop: `order_id` as a dimension (`identifier_not_dimension`). |
| Safety counters | validator escapes 0, leaked secrets 0, injected canaries 0. The key was **inferred** (`orders.id`: all 30 rows seen, unique, no nulls), not declared. |

This is a measurement on one 30-row table and one model call, not a pass; no thresholds were applied beyond the existing stop rules.

## For you at C3 (certification is yours)

- Read `contract.draft.json` and `ontology.candidate.json`; `REVIEW.md` summarises them.
- **Confirm the grain** (`orders.id`): it was inferred from a fully-seen table, not declared.
- `amount` as a dimension is a numeric column; you may prefer to drop it.
- To certify, place a contract with `lifecycle: certified`, its metrics `certified`, `tenant_id: sandbox` and a version of your choice in a registry folder. Nothing in the product does this for you, and the C4 run refuses anything that is not certified.
