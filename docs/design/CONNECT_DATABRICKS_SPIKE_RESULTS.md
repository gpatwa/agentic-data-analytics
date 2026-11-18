# Databricks spike (option A): results (2026-11-18)

Plan: `CONNECT_DATABRICKS_SPIKE_PLAN.md`. Statements: `CONNECT_DATABRICKS_SPIKE_STATEMENTS.md`. D2-1 to D2-3 were approved together by the owner and ran as one process (`app/connect/e2e/databricks_spike.py`) on **one** workspace (the owner's validation workspace, synthetic data), after the owner's single browser sign-in. It claims nothing beyond that. No secret, host id or message from the API is recorded.

## Outcome

Run completed with no stop. Teardown removed everything created (two secrets, three principals, the sandbox catalog; nothing left). The sweep found no catalog, no principal and no warehouse permission entry of ours. The leak scan found no secret in the report. The warehouse was only used, never edited.

## What it showed

| # | Question | Result |
|---|---|---|
| Q1 | Can a non-admin setup principal `S` create a service principal? | **No** (denied). |
| Q2 | Can `S` create a secret for a principal an admin made? | **No** (denied). Not tested for a principal `S` created itself, because Q1 was denied. |
| Q3 | Can `S` grant exactly the ceiling? | **Partly.** `S` (with `MANAGE` on two tables) could grant `SELECT` on those two tables, and was **denied** `SELECT` on the table outside, `SELECT` on the schema, `USE CATALOG` and `USE SCHEMA`. So the ceiling holds at table level, but `S` cannot grant the path privileges. |
| Q4 | Can `S`, holding `CAN_USE`, give another principal `CAN_USE` on the warehouse? | **No** (denied). |
| Q5 | Can `S` undo what it granted and delete what it created? | `S` could **revoke** the two `SELECT` grants; it could not delete the principal (it never created one). |
| Q6 | Is the connection principal `T` read-only? | **Yes.** With the admin-supplied fallbacks, `T` read both ceiling tables (30 rows each) and was denied the outside table, an insert, a create table and a drop. |
| Q7 | Does `S` see a same-name principal, and leave it alone? | `S` **saw** the decoy in a list, was **denied** creating a duplicate, and the decoy was untouched. |

Fallbacks the admin had to use so later questions could run: create `T`, create `T`'s secret, grant `USE CATALOG` and `USE SCHEMA`, give `T` `CAN_USE` on the warehouse.

## What this means for the design

Option A as designed (Compass creates per-connection principals with a narrow setup principal) **does not work on this workspace**: a non-admin `S` cannot create principals, mint their secrets, grant the path privileges or the warehouse permission. What *did* hold: table-level `SELECT` delegation with `MANAGE`, the read-only guarantee for `T`, and visibility and collision protection for same-name principals. The remaining options from the design: the admin pre-creates the per-connection principal and its warehouse and path grants with a short script (Compass then only passes on `SELECT` within the ceiling), or Compass holds an admin-level credential (not recommended). This is the owner's decision.

## Not shown

Whether a workspace admin principal (rather than a person) could be narrowed; the sign-in option B (no account console here); a different cloud or edition; scale. Q2 for a principal `S` created itself and Q5's deletion were not reachable because Q1 was denied.
