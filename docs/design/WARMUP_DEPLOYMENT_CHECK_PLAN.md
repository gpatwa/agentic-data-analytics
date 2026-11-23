# Deployment check of the wired warm-up: plan and draft approval (2026-11-22)

Status: **the run script is built and tested with fakes (`python -m reference_stack.deployment_check`, dry run by default). Nothing here is approved and nothing has run live.** The live check of 2026-11-22 (`docs/evaluation/scale-test-tier-m-results.md`, "Live check of the wired warm-up") ran the real `WarmUpService` but used an in-memory connection and the owner's CLI profile. This plan covers what it skipped.

## What a deployment check would add

| Skipped before | Covered by this check |
|---|---|
| The OAuth M2M connector (`databricks_gateway_for`, secret read from custody) | yes: a real service principal's own read-only access |
| A real connection record in the control database and the admin switch | yes: set through the real `PUT /connections/{id}/warm-up`, with its audit event |
| The HTTP routes `POST /warm` and the fallback inside `/analyze` | yes: called over HTTP on a running instance |
| The identity check (Entra) | **no**: a local run uses a test verifier (see option A) |
| The delay itself | **no**: already measured; this check is about wiring, so it uses the small schema |

## Two ways to run it

**A. A local instance of the real app (recommended).** The API runs on the owner's machine (`ANALYTICS_V2_ENABLED`, `ANALYTICS_CONNECT_DATABRICKS_ENABLED`, a SQLite control database, a temporary registry folder, the in-memory secret store). Identity comes from a test verifier that accepts one test bearer for the tenant `synthetic` with a manager role; nothing touches Entra or any deployed resource. No Azure resource is created or changed.

**B. A deployed environment.** Needs a named environment, Entra sign-in, a Key Vault and the owner's go-ahead to touch it. Not proposed now: it adds identity and vault coverage but needs a deployment the owner has not asked for.

## Steps (option A)

1. **Owner:** install the warehouse drivers where the check will run (`pip install -r services/analytics-api/requirements-warehouses.txt`: the Databricks SQL connector and SDK; they are not installed in the agent's environment either).
2. **Owner:** create a read-only service principal with the product's own registration script (`docs/design/CONNECT_DATABRICKS_REGISTRATION_STATEMENTS.md`), granting `SELECT` on the two views of `dev.compass_synthetic_food_delivery` only and `CAN_USE` on the Serverless Starter Warehouse. The earlier principal was deleted, so this is new.
3. **Owner:** put its client id and secret in `COMPASS_CHECK_CLIENT_ID` and `COMPASS_CHECK_CLIENT_SECRET` in the owner's own terminal. The agent never sees, reads, prints or records them; the script reads them, hands the secret only to the in-memory custody, and writes neither to the report.
4. **Agent (after approval):** runs `python -m reference_stack.deployment_check --execute ...`, which starts the real app in process (real routes, wiring, Entra verifier with a locally generated key, SQLite control database, a model that fails at once), registers the connection straight into the product's store, and drives it over HTTP:
   - `POST /warm` with the switch off → `skipped_disabled`, nothing recorded.
   - `PUT .../warm-up` on → the `connect.warm_up_changed` audit event exists.
   - `POST /warm` → 202 `warming` at once; `connect.warm_up_ran` records `warmed` with a count and a duration only.
   - a second `POST /warm` → `skipped_recent`; an `/analyze` call right after triggers no second warm-up.
5. **Owner:** delete the principal and its secret afterwards with the product's removal script.

What reaches the warehouse: for each of at most four certified warm-up queries, the product's gateway sends its own `EXPLAIN COST`, two session `SET`s (statement timeout, time zone) and the `SELECT`. The agent also lists the warehouses once through the owner's CLI profile to find the warehouse's id. No idle gap is needed: this is a wiring check, not a timing one.

Not covered even then: a real model answer through `/analyze` (the model fails at once by design), Entra, a vault, and a deployed instance.

## Draft approval scope (to record only on the owner's explicit yes)

"Run the warm-up deployment check as a local instance of the app on this computer, with a test identity verifier, an in-memory secret store and a SQLite control database, against https://dbc-b8672746-8e43.cloud.databricks.com, catalog and schema dev.compass_synthetic_food_delivery (views orders_current and orders_usd_gross), warehouse Serverless Starter Warehouse, through a read-only service principal the owner creates and removes, whose id and secret the owner provides only as environment variables. At most four certified warm-up queries (each sent with the gateway's own EXPLAIN COST and two session SETs), one read-only listing of warehouses to find the id, and a 20-minute cap, stopping on any other statement or the cap. It does NOT approve any deployment or Azure change, creating or changing any warehouse, any write, grant or setting change other than the owner's own principal script, any other schema or table, a model call, installing anything on the owner's behalf, or certifying anything."

## Decisions needed from the owner

1. Option A (local) or B (deployed)?
2. Will the owner create the service principal and set the two variables (steps 1 and 2), and remove it afterwards (step 4)?
3. Is `dev.compass_synthetic_food_delivery` (the small schema the certified contract names) the right target? Its existence in the workspace is assumed from earlier loads and not checked here.

## Built

`services/analytics-api/reference_stack/deployment_check/` and `tests/test_deployment_check.py` (7 tests: the whole path over HTTP with the real verifier and a fake connector, the admin-role and no-token refusals, the dry run, the refusals, the cap). Start-up and the check share `warm_up_for` in `app/connect/runtime_wiring.py`. Approval needle: `warmup_deployment_check`.
