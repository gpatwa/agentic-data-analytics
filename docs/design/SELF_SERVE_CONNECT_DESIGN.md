# Self-serve setup and onboarding for Connect mode: a design to decide (2026-11-23)

Status: **a design for the owner to review. Nothing here is built, approved or run; no warehouse was touched.** It answers the owner's point of 2026-11-23: manual steps and hand-set configuration are hard to remember, reproduce and scale, and the product should set itself up. It builds on `CONNECT_DATABRICKS_DESIGN.md` (D0 and the 2026-11-18 decision that the admin pre-creates the principal), `docs/ONBOARDING_DESIGN.md` (its principles) and the pieces that already exist (below). Every Databricks behaviour not already live-verified is marked **unverified**.

## The problem, from what happened

Writing the warm-up deployment check we made the owner do five things by hand: install drivers, create a service principal and run grants, `export` two secrets in the right terminal tab, run the command, and delete the principal afterwards. The `export` step failed several times because a variable set in one terminal is not visible to the process that needs it, and the customer path has the same shape: the admin runs a generated script, then copies an application id and a one-time secret into a form. Both are the same defect: **a human is the transport for a credential and the memory for a sequence.**

## What is manual today

| Who | Manual step | Why it hurts |
|---|---|---|
| Customer admin | Run the registration script, copy the application id, copy the secret once, paste both into the screen | Copy errors; a secret passes through a clipboard and a screen; no record of what was run |
| Customer admin | Remember to rotate before the secret's 90 days end | Silent expiry breaks every analyst's first question |
| Customer admin | Run the removal script later | Forgotten principals stay live |
| Operator (checks, demos, CI) | Create a test principal, set env vars, delete it | The failure above; not repeatable by anyone but its author |

## Principles (extending `ONBOARDING_DESIGN.md`)

1. **No human is a transport.** A credential moves machine to machine, once, and is never typed, shown, put in an environment variable, a file, a URL or a log.
2. **One action per person.** The admin does one thing (run one generated file or click one button); the operator runs one command. Everything after it is automatic and visible.
3. **The sequence lives in code.** Every step, order, name and teardown is generated and tested, so nothing depends on memory.
4. **Self-cleaning.** Anything created is listed in a ledger and removed in a `finally`; a sweep removes, by recorded id, what a crash left.
5. **Self-verifying.** Each setup ends with the product's own checks (verifier, connection test), and a scheduled check later detects drift.
6. **One code path for customers and for us.** Our own checks provision through the same runner a customer uses, so a passing check is evidence the customer path works.
7. **Compass never holds an admin credential** (decision of 2026-11-18, unchanged).

## What already exists (reuse, do not rebuild)

- `app/connect/databricks_registration.py`: `generate` makes the script (create principal, create secret, `CAN_USE`, `USE CATALOG`/`USE SCHEMA`, `SELECT` on exactly the named tables) and its removal; `verify_registration` compares the workspace's read-backs with it. **Live-verified** end to end on 2026-11-18 (`CONNECT_DATABRICKS_REGISTRATION_RESULTS.md`).
- `app/connect/e2e/databricks_e2e.py` (`E2E`): runs that script with an admin SDK client, verifies, connection-tests through the product's session, and removes. This is the missing "runner", today only used by the owner's one-off checks.
- `app/execution/warehouse_provisioning.py` (`DatabricksProvisioner`): the tagged, never-adopts, created-only teardown, `sweep` and in-memory-secret rules (live-verified on 2026-11-15/16 for the sandbox shape).
- `app/connect/databricks_api.py` and `apps/analytics-web/src/connect/DatabricksWorkspace.tsx`: the begin, script and credentials steps; the secret goes straight to the tenant secret store.
- `app/connect/warmup_service.py`, the rotation-free per-connection `warm_up` switch and the audit events.

## The design: three layers over one runner

### Layer 1: the registration runner (shared)

One component, `RegistrationRunner`, that takes a `ConnectScope` (the ceiling) and an admin client and does what the owner's hand did: preflight (names free, rights sufficient, stop with a plain message otherwise), run the generated steps, read back and verify, return the principal's credential **in memory only**, and expose `teardown()` and `sweep()`. It wraps `E2E` and the provisioner's safety rules rather than duplicating them. It has two callers, below. It never writes a credential anywhere; a crash leaves objects but no usable secret. A ledger of ids and names (never secrets), written after each creation, lets `sweep` finish the job. **Sweep removes only the ids in the ledger, never by name prefix**: the principal carries the product's own `compass-conn-` name, and a customer's real connection in the same workspace uses the same prefix.

### Layer 2a: operator self-serve (our checks, demos, CI)

`python -m reference_stack.deployment_check --provision` replaces steps 1 to 3 and 5 of the manual plan: it uses the owner's already-signed-in CLI profile (the one the earlier live checks used) to run the runner, hands the secret to the in-memory custody store, runs the existing check over HTTP, and tears down in a `finally`. No environment variables, no copy, nothing to delete by hand; `--sweep` clears a crashed run. The env-var path stays as a fallback for an operator without admin rights. This **creates a principal and grants in the owner's workspace**, which the recorded approval `warmup_deployment_check` does not cover (it excludes principal creation), so it needs its own approval (draft below).

Unverified: that the profile's rights allow creating principals and grants on `dev.compass_synthetic_food_delivery`. The earlier validation runs created and removed principals on that workspace, which suggests yes; the preflight proves it before anything is created.

### Layer 2b: customer self-serve onboarding

The admin does **one** thing and the screen does the rest. Tracks, chosen by what the account supports (the screen shows only what is likely to work, per principle 6 of `ONBOARDING_DESIGN.md`):

| Track | Admin does | Compass does | Needs |
|---|---|---|---|
| **1. Enrol file (default, proposed)** | Downloads one generated file `compass-enrol.py`, reads its digest on screen, runs it once under their own Databricks sign-in (CLI profile, notebook or terminal) | Waits; the file runs the runner's steps as the admin, then **posts the new principal's id and secret straight to Compass** with a single-use enrolment token; Compass stores the secret in custody, runs the verifier and connection test, and the screen ticks through the checks | A new enrol endpoint and file generator; Databricks SDK on the admin's side |
| **2. Product-run sign-in** | Clicks "Connect", signs in on Databricks' own page, approves the listed actions | Runs the runner as the admin's delegated token, as on Snowflake | An account console (**the owner's workspace has none**, so this cannot be tested there); unverified |
| **3. Terraform module** | Applies the module | Waits for the same enrolment call from the module's output hook | A module; additive for customers on infrastructure as code |
| **Fallback: export SQL/CLI** | Today's manual script and form | Same custody and checks | Already built |

**Why track 1 is the default.** It removes the human as transport (principle 1) without Compass ever holding an admin credential (principle 7): the file runs on the customer's side under the customer's admin identity. The secret does reach Compass over TLS, exactly as it does today when pasted; what changes is that no one copies it, sees it or leaves it in a clipboard.

**Enrolment token.** Minted when the admin begins a setup; single-use; 30-minute lifetime; bound to the tenant, the setup id and the script digest; accepted only on the enrol endpoint; it can submit a credential for that one setup and nothing else; rate-limited; never logged; the endpoint's response never echoes the secret. The generated file is plain, readable Python with the digest shown on screen so a security reviewer can read it first (no pipe-to-shell).

**What happens after "connected", automatically:** the eight conformance checks in plain words; a prompt to turn on warm-up (off by default, with its cost text); a prompt to choose what Compass may use and draft definitions (drafts only; a person certifies); a first governed question. The warm-up and other per-connection switches are plain admin settings, never environment variables.

### Layer 3: lifecycle with no reminders to remember

- **Drift detection.** A scheduled connection check (the existing `/check` path) runs with the stored secret. A denied read, an expired secret or a removed principal moves the connection to `suspended` with a plain reason and a one-click fix, and writes an audit event.
- **Rotation.** The secret's 90-day lifetime is tracked per connection; the screen warns at 60 days and the same enrol file rotates it (same single admin action). Unverified: whether a principal may mint its own next secret (which would allow silent rotation with no admin at all); the spike did not test it.
- **Removal.** One "Remove access" button deletes Compass's custody and shows the exact one-line removal file (the runner's `teardown`) for the admin's side; the next scheduled check confirms the principal is gone and closes the record.
- **Audit.** Every step above writes an event with codes only.

## What each manual step becomes

| Before | After |
|---|---|
| Install drivers by hand | The runner checks for them at preflight and says exactly what is missing; the enrol file declares and checks its own prerequisites |
| Create principal and grants | Layer 1, from the same generated script |
| Export the id and secret | Never: in-memory hand-off to custody (operator), single-use enrolment call (customer) |
| Run the check | One command, `--provision` |
| Delete the principal | `finally` teardown, `--sweep` for crashes, the ledger as backup |
| Rotate before 90 days | Warned, then the same single action |

## Risks and open questions

1. **Secret in transit to Compass** (track 1): equals today's paste in exposure, minus the human copy; still a security change, so it needs the owner's security review before any build is exposed to customers.
2. **A crash between "create principal" and "teardown"** leaves a principal (without a usable secret). Mitigations are the ledger (ids, never secrets) and the sweep, which removes only recorded ids and never matches by the shared `compass-conn-` prefix; the preflight refuses to start while leftovers exist.
3. **The admin's runtime** (notebook, CLI, or terminal) varies; the file must be self-contained and print only codes. Unverified on any customer environment.
4. **Principal self-rotation and the account console** (above) are unverified.
5. **Verifier trust.** In track 1 the read-back comes from the admin's side. Compass should also run its own negative check (a read outside the ceiling is denied) through the stored secret before showing "read-only". Unverified.

## Phases and gates (each needs the owner's explicit approval; none is approved)

| Phase | Content | Live? |
|---|---|---|
| S0 | Decide this document (tracks, enrolment token, rotation approach) | no |
| S1 | Build `RegistrationRunner` and `deployment_check --provision` on fakes, with tests for crash, sweep and leak scan | no (no approval needed to build) |
| S1-live | Run `--provision` once on the owner's workspace (scope drafted below) | yes, needs a new approval |
| S2 | Enrol endpoint, token and file generator on fakes | no |
| S3 | Screens: the track chooser, the live checks list, rotate and remove | no |
| S4 | Drift check and rotation reminders | no |
| S5 | One live end-to-end of track 1 on the owner's workspace | yes, needs approval |

## Draft approval scope for S1-live (to record only on the owner's explicit yes)

"Run the warm-up deployment check with `--provision` on this computer against https://dbc-b8672746-8e43.cloud.databricks.com, catalog and schema dev.compass_synthetic_food_delivery (views orders_current and orders_usd_gross), warehouse Serverless Starter Warehouse, using the owner's signed-in CLI profile to create one temporary service principal named compass-conn- followed by 8 random hex characters (the product's own registration script), one OAuth secret held only in memory, `USE CATALOG`, `USE SCHEMA` and `SELECT` on those two views and `CAN_USE` on the named warehouse, and to remove all of it in the same run (and by `--sweep` if interrupted). At most four certified warm-up queries (each with the gateway's own EXPLAIN COST and two session SETs), one read-only listing of warehouses, a 20-minute cap. It does NOT approve any deployment or Azure change, changing any warehouse, any other schema or table, any grant or write beyond the list above, a model call, installing anything on the owner's behalf, or certifying anything."

## Decisions for the owner

1. Is track 1 (enrol file) the right default for customers, or must the first release stay with the manual script until track 2 is testable?
2. Approve building S1 now (offline, no approval needed) so the deployment check becomes one command?
3. Rotation: accept a "rotate by re-running the enrol file" first, and test principal self-rotation later?
4. Is the 30-minute single-use enrolment token the right shape for the security review?

## Built: S1 (2026-11-23), on fakes only

`services/analytics-api/reference_stack/deployment_check/runner.py` (`RegistrationRunner`, `Ledger`, `run_sql`, `pick_warehouse`) and `--provision` / `--sweep` in `deployment_check/__main__.py`; `tests/test_deployment_check_provision.py` (17 tests against a fake admin client that keeps real state: principals, secrets, grants, the warehouse list). **None of it has touched a workspace.**

- `python -m reference_stack.deployment_check --provision --profile <cli profile> --approval-id warmup_provision_check --out <dir> --execute ...`: preflight, the product's own generated script, verifier on the read-backs, the HTTP check, then teardown in a `finally`. No environment variables.
- Refusals: no `--profile`, `--approval-id` or `--out`; no recorded approval (needle `warmup_provision_check`; **no such approval exists yet**, so a live run is refused); a profile for a different workspace; leftovers in the ledger; a taken name; an admin that cannot read grants; a driver not installed.
- Only `GRANT`/`REVOKE` of `USE CATALOG`, `USE SCHEMA`, `SELECT` and `SHOW GRANTS`, naming the ceiling's catalog, can be sent as admin SQL (tested against `DROP`, `ALL PRIVILEGES`, `MANAGE`, `WITH GRANT OPTION` and another catalog).
- The report records the verifier's counts, the teardown result and a leak-scan verdict; it is not ok if anything is left behind or a secret appears in the report or the ledger.
- `--sweep` removes what a crashed run's ledger recorded, by id; it needs no approval because it only removes what an approved run created.
- The manual environment-variable path still works as a fallback.

Not verified: any behaviour on a real workspace, including the profile's rights, `SHOW GRANTS` and `delete` semantics for a principal that holds a warehouse entry (the 2026-11-18 live check showed the entry goes with the principal; the runner re-checks it and reports it if not).

## Live run of `--provision` (2026-11-23, approval `warmup_provision_check`)

Three runs on the owner's workspace (`dev.compass_synthetic_food_delivery`, Serverless Starter Warehouse), through the owner's CLI profile. Each created one principal and removed it in the same run (teardown removed 6 objects, left none; the ledger was empty after each).

| Run | Result |
|---|---|
| da394372, 6814b5f4 | **Stopped at verification**, correctly: the workspace answered `SHOW GRANTS <principal> ON CATALOG dev` with other principals' grants too (modify, create table and others), which the verifier read as the new principal's. The fake admin could not show this (the 2026-11-18 sandbox catalog was fresh). The runner then wrote only counts, so the second run added the grant labels to the report to see what they were. |
| 05dbc0a0 | After the fix (only rows whose principal is the new application id count; 32 other-principal rows were ignored and counted): verification clean (0 missing, 0 unexpected, 0 dangerous, no warehouse problem); the wired path behaved as designed over HTTP: switch off `skipped_disabled`, switch on with its audit event, `POST /warm` answered 202 `warming` in 1.8 ms, the warm-up recorded `warmed` (3 statements, 18.9 s, recorded keys code, connection_id, seconds, statements_run only), a second call `skipped_recent`, `/analyze` triggered no second warm-up; no secret in the report or ledger; nothing left behind. |

What this shows: the self-provisioning path works end to end on the owner's workspace with no manual step, including the OAuth M2M connector, the real routes and the switch, and the profile has the rights to create and remove a principal and its grants. Not shown: Entra, a vault, a deployed instance, a real model answer; the delay itself (already measured); a customer's environment. Run once successfully, so no claim about repeatability.

## Built: S2, the enrol file (2026-11-23), on fakes only

Decided by the owner on 2026-11-23: **the enrol file is the default customer track**, and **the 30-minute single-use token is the right shape**.

`app/connect/enrol.py` (tokens, limiter, the file generator), routes in `app/connect/databricks_api.py`, setting `ANALYTICS_CONNECT_PUBLIC_URL`, `tests/test_connect_enrol.py` (31 tests). **None of it has run against a workspace or a deployed instance.**

- `POST /api/v2/connect/databricks/setups/{id}/enrol-file` (connection managers only): mints a token (single use, 30 minutes, bound to the tenant, the setup and the registration script's digest; only its hash is kept; a new file revokes the old token) and returns `compass-enrol.py` with the file's digest. 503 until `ANALYTICS_CONNECT_PUBLIC_URL` is set; the address must be https (or localhost for development).
- The file is plain Python the admin can read first. Under the admin's own Databricks sign-in it checks the workspace, finds the warehouse by name, creates the principal and secret, grants `CAN_USE` and exactly the read grants, reads them back, posts the id, secret and read-back to Compass, and removes everything it created if any step fails. It prints codes only and writes nothing to disk. `--digest` prints its digest (the address and token lines are left out) for comparison with the screen; `--remove <application id>` removes the principal later.
- `POST /api/v2/connect/databricks/enrol` is authorised by the token alone: failed attempts are limited per caller (10 per 5 minutes, then 429); the read-back goes through the product's verifier (a dangerous or unexpected grant is refused and nothing is stored); the secret goes straight to custody; then the existing connection test reads every named table as the principal. Answers are codes only; the body is read as a plain dict so no validation error echoes the secret. Audit events: `connect.enrol_file_issued` (digests), `connect.enrolled`, `connect.enrol_refused` (codes).
- Tests run the generated file's own code against a fake admin client and the real endpoint over HTTP: success, a failed connection test, a replayed file, an expired file, another workspace, and `--remove`; each failure leaves nothing behind and no secret appears in output, the file, the audit chain or a response.

Not verified: the file against the real Databricks SDK on a customer's machine, notebook or CLI (its calls are the ones the 2026-11-23 live provision run used, plus `service_principals.list()` and `delete` for `--remove`); a deployed instance with a public address and TLS; whether Compass should also run its own check that a read outside the ceiling is denied. Pending enrolments live in memory, so a restart loses them and the admin asks for a new file. `app/config.py` and `app/main.py` are listed in the ADS-079 evidence manifest; the checked-in manifest was already generated at another commit and is regenerated at final sign-off.

## Built: S3, the screens (2026-11-23), on fakes only

`apps/analytics-web/src/connect/` (`DatabricksWorkspace.tsx`, `databricksViews.tsx`, `databricksFlow.ts`, `databricksApi.ts`, `copy.ts`) and, on the server, `GET /api/v2/connect/databricks/setups/{id}/status` plus the principal's `application_id` on the connection list. **The screens were first covered only by render tests; on 2026-11-23 they were looked at in a browser against a local API (see the verification note below), which found and fixed two defects.**

- **Track chooser:** after the admin names the workspace and tables, "One file (recommended)" is selected; "Run the script yourself" is the earlier manual path, unchanged. If the server has no public address configured (`enrol_not_configured`) the screen says so and points to the manual path.
- **The file:** "Get the file" shows the digest to compare with `python3 compass-enrol.py --digest`, the run command, a download button and "Get a new file" (which revokes the old one).
- **Live status:** every 3 seconds the screen asks the status endpoint and shows waiting (with minutes left), connected (each table, "Can read"), refused (the plain-words error for its code, and that the file removed what it created) or expired. It stops when the file is spent or expired, and a failed poll is retried.
- **Remove:** after "Remove" the screen says Compass can no longer connect and shows the exact `python3 compass-enrol.py --remove <application id>` command, only when the application id is well formed.
- **Rotate:** unchanged (the admin pastes a new secret); the screen says plainly that rotating with one file is not built yet. That is S4.

Not built in S3: rotation by the enrol file, the drift check and the 60-day reminder (S4); a live end-to-end run (S5).

## Built: S4, rotation by file and the drift monitor (2026-11-23), on fakes only

`app/connect/enrol.py` (token purposes, the rotate file), `app/connect/drift.py` (new), routes in `databricks_api.py`, the web screens, setting `ANALYTICS_CONNECT_DRIFT_CHECK_HOURS`; `tests/test_connect_rotation_drift.py` (12 tests). **Nothing here has run against a workspace or a deployed instance.**

- **Rotation with one file.** `POST .../connections/{id}/rotate-file` (managers) issues `compass-rotate.py`: the same single-use, 30-minute token, now bound to the connection and its principal and usable only for rotating (a token works only for its own purpose; a misdirected call does not spend it). Under the admin's own sign-in the file finds the principal by application id (it must be named `compass-conn-...`), creates a new secret held only in memory, posts it to `POST .../enrol/rotate`, and only after Compass accepts it (the existing rotation: the old secret stays in custody until the new one reads every named table, and is put back if it cannot) deletes the principal's older secrets. If Compass refuses, the file deletes the new secret and the connection is as it was. `GET .../connections/{id}/rotate-status` feeds the screen.
- **No more date to remember.** The warning window is now 30 days before a 90-day secret ends (was 14), in the API and the screens. `DriftMonitor` runs an hourly expiry sweep that never touches the customer's warehouse: a connection whose secret has ended is paused (`suspended`) with `connect.secret_expired`, and one inside the window gets one `connect.secret_expiring` event per expiry date (a rotation re-arms it). It is started by the app's start-up when Connect on Databricks is enabled, and a failure inside it is swallowed.
- **Read check, off unless you turn it on.** `ANALYTICS_CONNECT_DRIFT_CHECK_HOURS` (default 0) runs the existing health check (read each named table as the principal) on every active connection at that interval, and pauses one that cannot read. It runs short queries on the customer's warehouse and starts it if stopped, so it is a decision for the owner, not a default.
- **Screens.** Each connection has "Rotate with a file" (first) and "Paste a new secret" (the earlier way), a panel with the digest, run command, download and live status (waiting, rotated, not rotated with the plain-words error, expired).

Not verified: the rotate file against the real SDK (it uses `service_principals.list`, `service_principal_secrets_proxy.create`, `.list` and `.delete`; the create and delete calls were used in the live provision run, `.list` of a principal's secrets was not); whether a principal can mint its own next secret (so rotation still needs the admin's sign-in, one action); the monitor's thread on a deployed instance; the screens in a browser. State is in memory, as for enrolment.

## Built: S5, the live check of the enrol file (2026-11-23); run once, passed (see below)

`services/analytics-api/reference_stack/enrol_check/` (`python -m reference_stack.enrol_check`, dry run by default) and `tests/test_enrol_check.py` (11 tests). The deployment check's local app now also serves the enrol routes. **A live run needs the recorded approval `enrol_file_live_check`; the owner gave it on 2026-11-23 ("Yes, record the approval and run it") and the run is reported below.**

What a run does, with the owner's signed-in profile: serves a local instance of the real app on localhost; as the admin asks for the enrol file; **runs that file** (it creates ONE service principal, its secret held in memory, `CAN_USE` on the warehouse and read grants on the two views, and posts them to the local app); checks the connection through the real Databricks session (OAuth M2M); runs the warm-up path on it; **rotates** with the rotate file; **removes** the connection and runs the enrol file's own `--remove`; lists the workspace and confirms the principal is gone. In a `finally`, whatever happened, the principal with this run's exact name is removed by id (never by the shared `compass-conn-` prefix), and the ledger entry written before the file ran is dropped; `--sweep` does the same for a crashed run. The report records step results and codes only, with a leak scan of every output and the report for the principal's secrets.

Safeguards tested on fakes: crash after the file ran (cleanup by exact name, a customer's own `compass-conn-*` principal untouched), a failed connection test (the file removes what it created), refusal without a recorded approval, with a leftover ledger, for a profile of another workspace, and above the 20-minute cap.

### Draft approval scope for `enrol_file_live_check` (to record only on the owner's explicit yes)

"Run the enrol_file_live_check on this computer against https://dbc-b8672746-8e43.cloud.databricks.com, catalog and schema dev.compass_synthetic_food_delivery (views orders_current and orders_usd_gross), warehouse Serverless Starter Warehouse, using the owner's signed-in CLI profile: serve a local instance of the app on localhost; run the generated enrol file, which creates one temporary service principal named compass-conn- followed by 8 random hex characters, one OAuth secret held only in memory, USE CATALOG, USE SCHEMA and SELECT on those two views and CAN_USE on the named warehouse; read both views as that principal; at most four certified warm-up queries (each with the gateway's own EXPLAIN COST and two session SETs); run the generated rotate file once, which creates one further secret for that principal and deletes the older ones; then remove the principal and its grants with the enrol file's --remove, list the workspace's service principals and warehouses read-only to confirm, and in every case remove the principal named exactly as this run's by id; a 20-minute cap. It does NOT approve any deployment or Azure change, changing any warehouse, any other schema or table, any grant or write beyond the list above, a model call, installing anything on the owner's behalf, or certifying anything."

### Live run (2026-11-23, approval `enrol_file_live_check`, run b6411365)

One run on the owner's workspace (`dev.compass_synthetic_food_delivery`, Serverless Starter Warehouse), through the owner's CLI profile. It passed first time; the report is `enrol-check-b6411365.json` (outside the repository). Results, codes only:

| Step | Result |
|---|---|
| Ask for the enrol file, run it | exit 0, "enrolled"; status **connected**; both views read as the new principal (OAuth M2M); connection **active** |
| Warm-up on that connection | switch off `skipped_disabled`; switch on with its audit event; `POST /warm` 202 `warming` in 2.3 ms; recorded `warmed` (3 statements, 9.1 s); a second call `skipped_recent`; `/analyze` triggered no second warm-up |
| Rotate with the rotate file | exit 0; status **connected**; the stored secret changed; the health check passed with the new secret |
| Remove | the connection deleted (200); the enrol file's own `--remove` exit 0; the workspace listing shows the principal **gone** |
| Cleanup and leaks | nothing left (cleanup by exact name found nothing to remove; the ledger is empty); no secret in any output or the report |

What this shows on the real workspace: the generated enrol file works end to end with no copied or pasted value; the real SDK calls it uses (`service_principals.create` and `list` and `delete`, `service_principal_secrets_proxy.create` and `list` and `delete`, `warehouses.update_permissions`, `statement_execution`) behaved as the fakes assumed; the rotate file works; `--remove` leaves nothing; the product's verifier accepts the real read-back (with the per-principal filter).

What it does not show: the check does not verify that the OLD secret stops working after rotation (it shows the new one works and the secret in custody changed); the enrol and rotate files on a customer's own machine, notebook or CLI; a deployed instance with a public https address and TLS (the check used localhost over http); the screens in a browser; the drift monitor's thread on a deployed instance; whether a principal can mint its own next secret. Run once, so no claim about repeatability.

### Verification pass (2026-11-23), after S5

A check of what was built, by running it rather than reading the tests. On `main` at the end of S5: the full Python suite (`tests` and `packages`) and the web tests, type check and build pass (the two manifest tests that failed in one run did so because the tree was edited during the run; rerun on a stable tree they pass); CI on `main` is green. Then the real application start-up (`app.main` lifespan, Connect on Databricks enabled) and the screens in a browser against a local API with a fake driver (no workspace touched). Found and fixed:

1. **The Databricks tab could not open on a real installation.** Its Open button called the generic Connect route (`/api/v2/connect/capabilities`), which answers 503 `connect_not_configured` unless the Snowflake-era Connect coordinator is wired, and it is not. Every test passed because none ran the real start-up with the real tab's calls. The tab now uses its own `/databricks/capabilities`. A new test runs the real start-up in a fresh process (`tests/test_main_lifespan_connect.py`) and records the 503.
2. **The one-file panel showed the manual track's wording** ("a secret you give to Compass once", "run the removal script"). It now says what the file does, from the setup's own details (tables, catalog, warehouse).
3. Tidied: the drift monitor is one per process (it was appended to a list; a second start-up in the same process skips the wiring, as before).

Confirmed working in the real start-up: the public address setting reaches the enrol and rotate routes (401 without a token, not 503); the drift monitor thread starts with the app, takes its interval from the setting, and stops on shutdown; with no public address the enrol file is not offered. Confirmed in the browser: sign-in, the form, the track chooser, getting the enrol file and the rotate file with their digests, and the live status polling ("stops working in 29 minutes"). Not exercised in the browser: a file actually being run (covered by the tests and the live run).

## Relationship to earlier documents

This supersedes the manual steps 1 to 3 and 5 of `WARMUP_DEPLOYMENT_CHECK_PLAN.md` once S1 is built; until then that plan's manual fallback is the only way to run the deployment check. It does not change the recorded approval `warmup_deployment_check` or any human gate.
