# Product-run live end-to-end check: results (2026-11-18)

Plan: `CONNECT_PRODUCT_RUN_E2E_PLAN.md`. Statements: `CONNECT_PRODUCT_RUN_E2E_STATEMENTS.md`. This file records what each gate actually showed, on **one** Snowflake account (the owner's own, synthetic data only). It claims nothing beyond that. No address, client id, token or key appears here.

| Gate | Status |
|---|---|
| G0 Review | approved 2026-11-18 |
| G1 Register | **done 2026-11-18** (below) |
| G2 Run the product | **done 2026-11-18** (below); the two optional checks were not run |
| G3 Tear down and sweep | **done 2026-11-18** (below); nothing from the check remains in the account |

## G1: what ran

Driven by me in the owner's signed-in Snowsight worksheet, as `ACCOUNTADMIN`, with the statements the repository generated (`app/connect/e2e/statements.py`, `generate_registration`). Nothing was typed by hand except the generated text.

1. **Preflight (read only).** All eight sweep checks returned zero rows, and the account had no network policies at all, so nothing under our prefixes or the fixture name existed. The lockout stop rule passed on every generated statement (none alters the account or a user, none sets a network policy on either, none grants `MANAGE GRANTS`).
2. **Fixture.** One synthetic database with three tables of 30 rows: two inside the ceiling (orders, customers) and one outside it (a payroll-like table). One disclosed deviation, the same as in the spike: `USE WAREHOUSE` on an existing warehouse was needed before the fixture could be created.
3. **Registration, exactly as the product generates it**, in two groups. Every statement succeeded: resource monitor, warehouse, network policy, setup role with its grants, and the OAuth integration (public client, PKCE, no refresh token, 600 s token validity, the local redirect with the non-TLS flag, and the network policy attached to the integration only). The setup role was granted to the owner's user.

## G1: what it showed

| # | Question | Result |
|---|---|---|
| E1 | Does the script as generated run on a real account? | **Yes**, every statement, with the network policy included. |
| E1 | Does `verify_registration` accept what Snowflake reports? | **Yes.** Fed the real `DESCRIBE SECURITY INTEGRATION` (13 properties) and `SHOW GRANTS TO ROLE` (7 rows) as read back: no missing, unexpected, dangerous or grant-option problems and no integration problems. **Without** the local-dev flag it reports exactly one problem, `oauth_allow_non_tls_redirect_uri`, so the deviation is visible and is not silently accepted. |
| E2 part 1 | Do `CREATE NETWORK POLICY` and `NETWORK_POLICY` on the integration work? | **Yes.** The integration reports the policy by name, and `DESCRIBE NETWORK POLICY` shows the single allowed address list entry (a /32 for the owner's address). Previously unverified. |
| E2 part 2 | Does a code exchange from an allowed address succeed and a disallowed one fail? | **Not yet** (G2). |

Things G1 does **not** show: the hosted sign-in, the identity checks, the run, the connection test, the screens, the leak scan, the `registration_changed` negative, anything with an `https` redirect. Those are G2 or stay unverified by design.

Caveats: the read-back rows were read from Snowsight's results grid and fed to the verifier by me, not fetched by the product. The setup role's grants were read as `ACCOUNTADMIN`, which is how an admin would verify after registering; the setup role's own view (E4) is a G2 check.

## G2: what ran

The local harness (`app/connect/e2e/harness.py`, `127.0.0.1` only, a throwaway static-token identity verifier, the built web UI, real Snowflake sessions) was driven through the product's own screens in the owner's Chrome. The owner approved, signed in on Snowflake's own page and clicked Allow; no password was typed by me.

1. Generated the registration script in the UI (same digest as the one run in G1), registered, planned a connection for one ceiling table (`orders`), the owner approved the 8 statements, signed in, and the product ran them. The screen reported "Connected and tested".
2. Read back in Snowflake as `ACCOUNTADMIN`: the new role has exactly four grants (usage on the database, the schema and the warehouse, select on `orders`), none with grant option; the service user is type SERVICE, key only, no password, default role the new role.
3. **Drift check.** I granted the setup role select on a table outside the ceiling, planned a second connection (`customers`), and the owner approved and signed in. The run was refused: "The setup role no longer matches the registration. Nothing was created." Snowflake showed no second role or user. The generated service credential was deleted (`connect.credential_deleted`). The widening was then revoked and the setup role read back as exactly the two ceiling tables.
4. Leak scan over the whole run: the sign-in code, the access token, the PKCE verifier and the service private key (base64 and DER) were watched in every log record, response and the audit text. No finding. The audit chain verifies.

## G2: what it showed

| # | Question | Result |
|---|---|---|
| E3 | Does the hosted sign-in work with Snowflake's real redirect and a real browser? | **Yes.** Start, binding cookie, callback, code exchange and hand-off worked; the callback returned to the review page. |
| E4 | Do the identity checks and the preflight work through a real OAuth session? | **Yes, shown by the outcome**: the run went past the account, role and setup-role-grants checks and the preflight. I did not observe the individual check results. |
| E5 | If the admin widens the setup role after registration, does the product refuse and create nothing? | **Yes.** Refused with the plain-words message; nothing created in Snowflake. This is the control that stops a widened ceiling being used. |
| E6 | Does a run create exactly the approved effects, pass the grants check and activate through the real driver session? | **Yes** for one connection: exactly the approved grants, a key-only service user, and the connection test through the real driver passed. |
| E7 | Do the screens work against a real backend? | **Yes**, after one fix (below), plus two behaviours worth knowing. |
| E8 | Does any token, code, verifier or key appear in the audit chain, logs or responses? | **No finding** in this run. |
| E2 part 2 | Does a code exchange from a disallowed address fail? | **Not run** (optional). The allowed address worked. |
| E9 | Is a sign-in requesting `ACCOUNTADMIN` refused? | **Not run** (optional). |

## Found during G2

- **Harness gap (fixed):** the screens call the script-path router for capabilities first; the harness had not mounted it, so the first click returned 404. Mounted, with a test.
- **Logging state (fixed):** three harness tests failed in CI because earlier tests left `logging.disable(INFO)` and a disabled access logger behind. That would also have let the scanner see nothing and report clean, so the harness now re-enables logging when it attaches, and the tests isolate logging state.
- **Approval window is 5 minutes.** A plan left unclicked for longer is refused by the approval gate (a 409; I did not capture the exact code, the gate's lifetime is 300 seconds) and a sign-in started from a stale page ends in "The sign-in took too long". The refusals were correct, but five minutes is tight for a person who has to read statements and also sign in; **decide a lifetime for the real product** (not changed here).
- **The page forgets the access token after the return from Snowflake** (it is held in memory), so the person pastes it again. Fine for a harness; a real session should survive the redirect.

## Still not shown

An `https` redirect on a real deployment, a real identity provider, durable stores and a vault, process isolation, a second account or edition, Databricks, a rollback after a failure part-way through a run, a governed question over the new connection, the independent human review. The registration objects, the fixture and the first connection's role and user were removed in G3 (below).

## G3: tear down and sweep

1. **The product's own removal** of the one connection: Compass's stored credential was deleted at once and the product returned `DROP USER` and `DROP ROLE` for the connection (digest recorded in the audit chain). The two other plans (the refused run and the plan whose approval expired) had created nothing in Snowflake.
2. **Run as `ACCOUNTADMIN`**, in order: the connection's user and role, the setup role revoked from the owner's user, the integration, the network policy, the setup role, the warehouse, the resource monitor, the fixture database. Created-only, by generated names; the lockout stop rule passed. Every statement succeeded (the run stops at the first error, and the last statement reported success).
3. **Sweep:** the eight `SHOW` checks (database, setup roles, connection roles, service users, warehouses, resource monitors, integrations, and **all** network policies) each returned zero rows. The account had no network policy before G1 and has none now.
4. **Harness stopped**; its final report: no secret findings, audit chain verifies. The private keys lived only in its memory and are gone. The throwaway access token and the notes holding the owner's address and the client id were deleted from the scratch folder.

## Summary

On one Snowflake account (the owner's, synthetic data, a local non-TLS redirect, a stubbed identity verifier), the product-run path **works end to end and its main safety control held**: the generated registration script runs and verifies; the hosted sign-in works with Snowflake's real redirect; a run created exactly the approved read-only role and a key-only service user and passed its connection test; a widened setup role was refused with nothing created; no secret reached a log, response or audit event; and teardown left no trace.

What this does **not** make true: it is not deployed, not reviewed by an independent human, not run against a customer's account or data, and the `https` redirect, a real identity provider, durable storage and a vault, process isolation, Databricks and scale are untouched. The two optional checks were not run. Two product decisions fall out of it: the approval lifetime (5 minutes today) and keeping the person's session across the return from Snowflake.
