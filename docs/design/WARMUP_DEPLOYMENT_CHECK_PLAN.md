# Deployment check of the wired warm-up: plan and draft approval (2026-11-22)

Status: **a proposal. Nothing here is approved or built, and nothing has run.** The live check of 2026-11-22 (`docs/evaluation/scale-test-tier-m-results.md`, "Live check of the wired warm-up") ran the real `WarmUpService` but used an in-memory connection and the owner's CLI profile. This plan covers what it skipped.

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

1. **Owner:** create a read-only service principal with the product's own registration script (`docs/design/CONNECT_DATABRICKS_REGISTRATION_STATEMENTS.md`), granting `SELECT` on the two views of `dev.compass_synthetic_food_delivery` only and `CAN_USE` on the Serverless Starter Warehouse. The earlier principal was deleted, so this is new.
2. **Owner:** put its client id and secret in two environment variables in the owner's own terminal. The agent never sees, reads, prints or records them; the run script reads them and never logs them.
3. **Agent (after approval):** a script starts the app in process with a test verifier, registers the connection through the real setup routes, then:
   - `POST /warm` with the switch off → `skipped_disabled`, nothing sent.
   - `PUT .../warm-up` on → the audit event `connect.warm_up_changed` exists.
   - after an idle gap: `POST /warm` → 202 `warming`; the audit event `connect.warm_up_ran` shows `warmed` with a count and a duration (no statement, no value).
   - a second `POST /warm` → 200 `skipped_recent`; an `/analyze` call right after sends no extra warm-up.
   - one real question through `/analyze` (a canned model reply, no model call) returns the right answer on the real OAuth gateway.
4. **Owner:** delete the principal and its secret afterwards with the product's removal script (the principal is the owner's to remove).

## Draft approval scope (to record only on the owner's explicit yes)

"Run the warm-up deployment check as a local instance of the app on this computer, with a test identity verifier, an in-memory secret store and a SQLite control database, against https://dbc-b8672746-8e43.cloud.databricks.com, catalog and schema dev.compass_synthetic_food_delivery (views orders_current and orders_usd_gross), warehouse Serverless Starter Warehouse, through a read-only service principal the owner creates and removes, whose id and secret the owner provides only as environment variables. At most 60 SELECT statements and a 20-minute cap, a 10-minute idle gap, stopping on a wrong answer, any non-SELECT, or the budget or cap. It does NOT approve any deployment or Azure change, creating or changing any warehouse, any write, grant or setting change other than the owner's own principal script, any other schema or table, a model call, or certifying anything."

## Decisions needed from the owner

1. Option A (local) or B (deployed)?
2. Will the owner create the service principal and set the two variables (steps 1 and 2), and remove it afterwards (step 4)?
3. Is `dev.compass_synthetic_food_delivery` (the small schema the certified contract names) the right target? Its existence in the workspace is assumed from earlier loads and not checked here.

## Not built yet

The run script (local app with a test verifier plus the steps above) does not exist. It would be built, with fake-provider tests, before any run, and its dry run shown first.
