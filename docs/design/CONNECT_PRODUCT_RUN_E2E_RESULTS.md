# Product-run live end-to-end check: results (2026-11-18)

Plan: `CONNECT_PRODUCT_RUN_E2E_PLAN.md`. Statements: `CONNECT_PRODUCT_RUN_E2E_STATEMENTS.md`. This file records what each gate actually showed, on **one** Snowflake account (the owner's own, synthetic data only). It claims nothing beyond that. No address, client id, token or key appears here.

| Gate | Status |
|---|---|
| G0 Review | approved 2026-11-18 |
| G1 Register | **done 2026-11-18** (below) |
| G2 Run the product | approved 2026-11-18; **in progress, results not yet recorded here** |
| G3 Tear down and sweep | not yet approved, not run. **The registration objects, the synthetic fixture and anything G2 created are still in the account** until G3 |

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
