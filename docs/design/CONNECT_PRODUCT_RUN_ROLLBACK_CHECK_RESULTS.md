# Product-run live rollback check: results (2026-11-18)

Plan: `CONNECT_PRODUCT_RUN_ROLLBACK_CHECK_PLAN.md`. Statements: `CONNECT_PRODUCT_RUN_ROLLBACK_CHECK_STATEMENTS.md`. This file records what each gate actually showed, on **one** Snowflake account (the owner's own, synthetic data only). It claims nothing beyond that. No address, client id, token or key appears here.

| Gate | Status |
|---|---|
| G0 Review | approved 2026-11-18 |
| G1 Register | **done 2026-11-18** (below); run suffix `6bf33fb2` |
| G2 Run (R1 injected failure, R2 decoy) | **done 2026-11-18** (below); the harness was stopped afterwards |
| G3 Tear down and sweep | not approved, not run. **The registration, the fixture and the decoy user are still in the account until G3 runs.** |

## G1: what ran

Driven by me in the owner's signed-in Snowsight worksheet, as `ACCOUNTADMIN`, with the statements the repository generated (`app/connect/e2e/statements.py`, new suffix). The statements were typed exactly as generated and the editor text was compared with them before each run.

1. **Preflight (read only).** The identity query showed the account, the owner's user, the role `ACCOUNTADMIN` and the client address (used only for the network policy; not recorded here). All seven sweep checks (database, setup roles, connection roles, service users, warehouses, resource monitors, integrations) and **all network policies** returned zero rows, so nothing under our prefixes or the fixture name existed. The lockout stop rule passed on every generated statement.
2. **Fixture.** One synthetic database with three tables of 30 rows each (confirmed by a count query): two inside the ceiling (orders, customers) and one outside it (a payroll-like table). The warehouse selected in the worksheet was used for the inserts, as in the earlier check.
3. **Registration, exactly as the product generates it**, in two groups. Every statement succeeded: network policy, resource monitor, warehouse, setup role with its grants, and the OAuth integration (public client, PKCE, no refresh token, 600 s token validity, the local redirect with the non-TLS flag, the network policy attached to the integration only). The setup role was granted to the owner's user.

## G1: what it showed

| Question | Result |
|---|---|
| Does the generated script run on a real account a second time, with a new suffix? | **Yes**, every statement. |
| Does `verify_registration` accept what Snowflake reports? | **Yes.** Fed the real `SHOW GRANTS TO ROLE` (7 rows: create role and create user on the account without grant option; usage on the database, schema and warehouse and select on the two tables with grant option) and the 13 integration properties it checks: no missing, unexpected, dangerous or grant-option problems and no integration problems. **Without** the local-dev flag it reports exactly one problem, `oauth_allow_non_tls_redirect_uri`, so the deviation stays visible. |
| Is the network policy exactly the one address? | **Yes**: `DESCRIBE NETWORK POLICY` shows one allowed address list entry, a /32 for the owner's address, and the integration reports the policy by name. |

Caveat, as before: the read-back rows were read from Snowsight's results grid and fed to the verifier by me, not fetched by the product. The setup role's grants were read as `ACCOUNTADMIN`.


## G2: what ran

The local harness (`127.0.0.1` only, a throwaway token, real Snowflake sessions) was started with the one-shot fault on step 8 and driven through the product's own screens. The owner approved the statements, signed in on Snowflake's own page and clicked Allow for each run; I typed no password.

**R1 (injected failure, real rollback).** Registered Compass with the G1 integration, planned a connection for the orders table (8 statements, role and service user with a fresh suffix), the owner approved and signed in, and I clicked Run. The screen said: "One of the setup steps was refused by Snowflake. What it had created was removed."

**R2 (decoy).** Planned a second connection for the customers table, read the planned user name on the review screen, and as `ACCOUNTADMIN` created a decoy user with exactly that name (a plain `CREATE USER` with only a comment marking it as a decoy; no key, password, network policy or grants; the statement was shown in chat before it ran). The owner then approved, signed in and clicked Allow, and I clicked Run. The screen said: "A role or user with that name already exists. Nothing of yours was changed. Plan the setup again to get fresh names."

## G2: what it showed

| # | Question | Result |
|---|---|---|
| R1 | Can the setup role, signed in through OAuth, drop the role and user it just created, and does the rollback leave nothing behind? | **Yes.** Snowflake's own query history for the setup role shows, in order and all successful: the preflight `SHOW ROLES` and `SHOW USERS`, `CREATE ROLE`, `CREATE USER`, `ALTER USER ... SET RSA_PUBLIC_KEY`, `GRANT USAGE` on the database, on the schema, `GRANT SELECT` on the table, `GRANT USAGE` on the warehouse (seven real statements), **no** `GRANT ROLE` (the injected failure), then `DROP USER` and `DROP ROLE`. Afterwards `SHOW ROLES LIKE 'COMPASS_RO%'` and `SHOW USERS LIKE 'COMPASS_SVC%'` returned zero rows. The setup role has no `MANAGE GRANTS` and did not need it. |
| R1 | Is it the rollback of what the run created, in the right order (user before role)? | **Yes**, as above. |
| R2 | Did the setup role see the decoy in its preflight? | **Yes.** The run was refused by the preflight (`name_taken`); the setup role's history shows no `CREATE`, `GRANT`, `ALTER` or `DROP` for the planned names, and the audit shows no teardown. So a role that only holds `CREATE USER` on the account **can** see an existing user in `SHOW USERS` (this was the open question). This is inferred from the refusal code and the absence of any create statement; my history query excluded the setup role's own `SHOW USERS` row for that name, so I did not look at that row. |
| R2 | Is a pre-existing object left untouched? | **Yes.** After the run the decoy still existed, owned by `ACCOUNTADMIN`, with its comment, no key, no password, no default role, not disabled, created time unchanged. No `COMPASS_RO%` role existed. |
| R2 | Does the rollback spare a pre-existing object when a real "already exists" failure happens mid-run? | **Not shown.** The preflight refused earlier than the design assumed, so that path stayed unreached on a real account. It is covered on the fake only (`test_a_decoy_the_setup_role_cannot_see_fails_the_run_mid_way_and_the_rollback_spares_it`). |
| R3 | Do the audit events tell the truth? | **Yes.** R1: consent shown, setup approved, torn down, setup failed, credential deleted. R2: consent shown, setup approved, setup failed, credential deleted (no teardown, because nothing was created). Both connections end `removed`. The audit chain verifies. |
| leaks | Any code, token, verifier or service key in a log, response or the audit text? | **No finding.** The scanner watched the code, access token, PKCE verifier and the service private key (base64 and DER) the whole time. |

## Found during G2

- **The injected failure reads as "refused by Snowflake" on the screen.** The product reports any driver error from a setup step in the same words. That is true for a real Snowflake refusal and slightly wrong for a local fault; not changed.
- **Two sign-ins in five-minute windows are tight but workable.** The pages again forgot the access token after Snowflake's redirect and it had to be pasted again (already listed as a product decision).
- **Query-history lookups are slow** (about 25 seconds on the sandbox warehouse); the check used a small amount of the warehouse, under the monitor's cap.

## Not shown

A rollback after a real mid-run Snowflake failure (name collision that the preflight cannot see), a rollback that itself fails, a killed process, and everything the end-to-end check listed as unshown. The decoy user, the registration and the fixture remain until G3.
