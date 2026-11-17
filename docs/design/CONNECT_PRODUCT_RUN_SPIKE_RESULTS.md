# Product-run spike: results (in progress, 2026-11-17)

Status: **G1 is done; G2 and G3 have not run and each needs the owner's explicit approval.** These are observations from **one run on one account** (the owner's own Snowflake account, synthetic data only), not general claims. No credential, client id or account identifier is recorded here. The questions are numbered as in `docs/design/CONNECT_PRODUCT_RUN_SPIKE_PLAN.md`.

## What G1 did

1. **Read-only preflight.** Account, user and role were as expected (the owner's user, role `ACCOUNTADMIN`). Six `SHOW ... LIKE 'COMPASS_SPIKE%'` checks (databases, roles, users, warehouses, resource monitors, integrations) returned **zero rows**, so nothing existed under the prefix.
2. **Phase A**, the generated and reviewed statements (`docs/design/CONNECT_PRODUCT_RUN_SPIKE_STATEMENTS.md`), run in the Snowsight worksheet in three groups. **Every statement succeeded on the first run**; no syntax needed correcting. It created a sandbox database with three synthetic tables of 30 rows, a 1-credit resource monitor, an X-Small warehouse on that monitor (auto-suspend 60 seconds), the delegated setup role with its ceiling grants, a grant of that role to the owner's user, and a custom OAuth integration.
3. **Checks of what was built.** The warehouse is `SUSPENDED`; the delegated role holds exactly seven grants; the integration settings were read back.

A deviation to disclose: the three `INSERT` statements ran before the spike warehouse existed, so they used the session's existing `COMPUTE_WH` (X-Small) for a few seconds. The cost is negligible and not measured. Snowsight then switched the session to the spike warehouse after it was created.

## Observations by question

| # | Question | Observed in G1 | Still to settle |
|---|---|---|---|
| Q1 | A role with only account-level `CREATE ROLE` and `CREATE USER` can create a role and user | `GRANT CREATE ROLE ON ACCOUNT` and `GRANT CREATE USER ON ACCOUNT` to a custom role were **accepted** | Whether the role can actually create them and set the user's key (G2) |
| Q2 | A grant-option ceiling works and contains the role | `GRANT USAGE/SELECT ... WITH GRANT OPTION` on database, schema, two tables and the warehouse were accepted; `SHOW GRANTS TO ROLE` shows **exactly seven rows**, `grant_option = true` on the five ceiling grants, none on the two account privileges, none on the outside table | Whether it can grant within the ceiling and is refused outside it (G2) |
| Q3 | A pre-created warehouse and monitor avoid `ACCOUNTADMIN` per setup | The monitor and warehouse were created as `ACCOUNTADMIN` as the design says; the warehouse is attached to the monitor and suspended | Whether the setup role can grant `USAGE` on the warehouse onward (G2) |
| Q4 | A network policy can be attached to a created user | Not tested | G2 |
| Q5 | Negative tests refuse | Not run | G2 |
| Q6 | A public client works with no secret | `OAUTH_CLIENT_TYPE = 'PUBLIC'` was **accepted** for a custom client; `DESCRIBE` shows a client id and **no secret field**; the integration also has `OAUTH_CLIENT_RSA_PUBLIC_KEY_FP` and `..._2_FP` properties, so a **key-based client route exists as an integration setting** (not tested) | Whether the authorization-code exchange succeeds with PKCE and no secret (G2) |
| Q7 | Admin roles are blocked by default; the account parameter exists | The integration's `BLOCKED_ROLES_LIST` is `ACCOUNTADMIN, ORGADMIN, SECURITYADMIN` by default. The account parameter `OAUTH_ADD_PRIVILEGED_ROLES_TO_BLOCKED_LIST` **exists**, value and default `true` (read only, never changed). `OAUTH_ANY_ROLE_MODE` is `DISABLE`. | That a sign-in requesting `ACCOUNTADMIN` is refused (G2). Note `GLOBALORGADMIN`, named in the docs, is not in this default list. |
| Q8 | `session:role:<NAME>` selects the role | Not tested | G2 |
| Q9 | Token about 600 seconds, no refresh tokens | The integration is set to access-token validity 600 and `OAUTH_ISSUE_REFRESH_TOKENS = false` (read back); `OAUTH_ENFORCE_PKCE = true`, `OAUTH_USE_SECONDARY_ROLES = NONE`, `NETWORK_POLICY` empty | The actual token (G2) |
| Q10 | End to end | Not run | G2 |
| Q11 | CM2 to CM4 shapes never run live | `SHOW GRANTS TO ROLE` (as `ACCOUNTADMIN`) returns the columns `created_on, privilege, granted_on, name, granted_to, grantee_name, grant_option, granted_by`, which **include the keys `verify_grants` reads**; the integration, warehouse and resource-monitor `SHOW` forms worked | The same query as the service user itself, `DESCRIBE TABLE` through the driver, tag comments through `SHOW` (G2) |

## What exists in the account now (until G3)

A sandbox database, a resource monitor, an X-Small warehouse (suspended), the delegated setup role, a custom OAuth integration, and **the setup role granted to the owner's user**. Nothing else was created or changed. G3 removes all of it and revokes the grant. The warehouse is suspended and the monitor caps spending at 1 credit, so leaving it idle costs nothing measurable, but it should not be left long.

## Not verified

Everything in "Still to settle". The cost of G1 was not measured.
