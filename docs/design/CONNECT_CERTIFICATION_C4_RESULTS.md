# Certification C4 (ask through the connection): results (2026-11-18)

Plan: `CONNECT_CERTIFICATION_PLAN.md`. Statements: `CONNECT_CERTIFICATION_STATEMENTS.md`. Approved by the owner on 2026-11-18 (program file `connect_certification_c4`), after the owner certified the draft as drafted (`connect_certification_c3`). One workspace (the owner's validation workspace), synthetic data, suffix `889cfc3a`, registry `services/analytics-api/semantic_registry_sandbox`. No model call. No secret, host id or key is recorded.

## What ran

The sandbox was stood up as the product does, then the connected-warehouse half of a governed question was exercised through the connection's own principal. Everything created was removed: 8 removals, none left; the sweep found no catalog, principal or warehouse permission of ours. The leak scan watched the principal's secret and the rotated secret and found nothing; the audit chain verifies. A first attempt did not start (the wrong Python, no Databricks SDK) and touched nothing.

## Outcomes

| Step | Result |
|---|---|
| Connection active, 2 allowlisted tables | allowed |
| Assets assembled from the certified contract (dialect `databricks`, inside the allowlist) | allowed |
| One compiled metric through the gateway | `order_count` = 30 (the sandbox holds 30 rows in that table; **not independently checked** by the run) |
| Statement on a table outside the allowlist | **denied** before reaching Databricks |
| Connection suspended | resolver **refused** (`connection_not_active`) |
| Secret rotation (second secret, swapped in after table reads pass) | allowed, connection active |
| Same question after rotation | allowed |
| Connection removed (stored credential deleted) | allowed |

## Not shown

The model's intent extraction and the full governed graph (control database, review, evidence) were not exercised: the intent is built by hand from the certified contract. One metric on one 30-row table; this is a measurement, not a pass against thresholds. The 90-day secret lifetime and the production vault against a real vault are still untested live.
