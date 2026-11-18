# Product-run live rollback check: results (2026-11-18)

Plan: `CONNECT_PRODUCT_RUN_ROLLBACK_CHECK_PLAN.md`. Statements: `CONNECT_PRODUCT_RUN_ROLLBACK_CHECK_STATEMENTS.md`. This file records what each gate actually showed, on **one** Snowflake account (the owner's own, synthetic data only). It claims nothing beyond that. No address, client id, token or key appears here.

| Gate | Status |
|---|---|
| G0 Review | approved 2026-11-18 |
| G1 Register | **done 2026-11-18** (below); run suffix `6bf33fb2` |
| G2 Run (R1 injected failure, R2 decoy) | not approved, not run |
| G3 Tear down and sweep | not approved, not run. **The registration and fixture from G1 are still in the account until G3 runs.** |

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

## What this does not show yet

R1 (an injected failure and a real rollback), R2 (the decoy) and R3 (the audit and the screen after a failed run) are G2. Nothing has been created by a product run yet.
