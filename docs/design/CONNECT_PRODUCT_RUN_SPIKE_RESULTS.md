# Product-run spike: results (G1 and G2 done, 2026-11-17)

Status: **G1 and G2 are done; G3 (tear down and sweep) has not run and needs the owner's explicit approval.** These are observations from **one run on one account** (the owner's own Snowflake account, synthetic data only), not general claims. No credential, client id or account identifier is recorded here. The questions are numbered as in `docs/design/CONNECT_PRODUCT_RUN_SPIKE_PLAN.md`.

**G2 in one line:** with a public OAuth client, PKCE and no client secret, a delegated setup role with only account-level `CREATE ROLE` and `CREATE USER` plus a grant-option ceiling created a role and a service user, granted within the ceiling, was **refused on all eight negative tests**, and its service user passed the Connect-mode checks; **32 of 32 steps behaved as expected, and the setup role's teardown removed everything it created.** One earlier attempt failed before creating anything (below).

## What G1 did

1. **Read-only preflight.** Account, user and role were as expected (the owner's user, role `ACCOUNTADMIN`). Six `SHOW ... LIKE 'COMPASS_SPIKE%'` checks (databases, roles, users, warehouses, resource monitors, integrations) returned **zero rows**, so nothing existed under the prefix.
2. **Phase A**, the generated and reviewed statements (`docs/design/CONNECT_PRODUCT_RUN_SPIKE_STATEMENTS.md`), run in the Snowsight worksheet in three groups. **Every statement succeeded on the first run**; no syntax needed correcting. It created a sandbox database with three synthetic tables of 30 rows, a 1-credit resource monitor, an X-Small warehouse on that monitor (auto-suspend 60 seconds), the delegated setup role with its ceiling grants, a grant of that role to the owner's user, and a custom OAuth integration.
3. **Checks of what was built.** The warehouse is `SUSPENDED`; the delegated role holds exactly seven grants; the integration settings were read back.

A deviation to disclose: the three `INSERT` statements ran before the spike warehouse existed, so they used the session's existing `COMPUTE_WH` (X-Small) for a few seconds. The cost is negligible and not measured. Snowsight then switched the session to the spike warehouse after it was created.

## G2: what ran

1. **First attempt (no effect on the account).** The OAuth callback arrived but the token exchange failed in the script: Python's default certificate store rejected Snowflake's certificate chain (`self-signed certificate in certificate chain`); the certifi bundle connects fine. The script recorded zero steps and created nothing. The fix (certifi, and recording a network failure as a step) is in the spike tooling with tests.
2. **Second attempt.** The owner signed in on Snowflake's own page (the script never saw a password) and clicked Allow. The code was exchanged with the client id and PKCE verifier only. A connector session over the OAuth token ran the 9 per-setup statements, the 8 negative tests and the service-user checks, then the setup role dropped the user and role it had created.

## Observations by question (one run, one account)

| # | Question | Observed | Still to settle |
|---|---|---|---|
| Q1 | A role with only account-level `CREATE ROLE` and `CREATE USER` can create a role and user and set the user's key | **Yes.** `CREATE ROLE`, `CREATE USER ... TYPE = SERVICE` and `ALTER USER ... SET RSA_PUBLIC_KEY` all succeeded as that role | Other editions or account configurations |
| Q2 | A grant-option ceiling works and contains the role | **Yes.** It granted `USAGE` on the database and schema and `SELECT` on both ceiling tables to the new role. Asked to grant `SELECT` on the table outside the ceiling it was refused (errno 2003: the object is invisible to it, "does not exist or not authorized"); `MANAGE GRANTS`, `SYSADMIN`, `ACCOUNTADMIN` and `OWNERSHIP` were refused (errno 3102, insufficient privileges) | A role that already has other privileges on the outside table |
| Q3 | A pre-created warehouse and monitor avoid `ACCOUNTADMIN` per setup | **Yes.** The setup role granted `USAGE` on the pre-created warehouse to the new role; it did not create the monitor or warehouse | A monitor and warehouse owned by a different role |
| Q4 | A network policy can be attached to a created user | **Not tested** (Phase A created no policy object) | A follow-up check |
| Q5 | Negative tests refuse | **8 of 8 refused**: the outside table, `MANAGE GRANTS`, `SYSADMIN`, `ACCOUNTADMIN`, ownership transfer, a security integration, a resource monitor and a database (the last three errno 3001) | |
| Q6 | A public client works with no secret | **Yes.** The authorization-code exchange succeeded with the client id and PKCE verifier only; no client secret was read or sent. The integration also exposes RSA public key slots (a key-based route), not needed and not tested | Other editions; whether the same holds behind a hosted callback |
| Q7 | Admin roles are blocked at sign-in | The integration's default `BLOCKED_ROLES_LIST` is `ACCOUNTADMIN, ORGADMIN, SECURITYADMIN`; the account parameter exists (value and default `true`, read only). **The actual refusal of an `ACCOUNTADMIN` sign-in was not observed**: it needs another owner sign-in, which was not requested | Optional extra check |
| Q8 | `session:role:<NAME>` selects the role | **Yes.** After sign-in `CURRENT_ROLE()` was the delegated setup role | A user who was not granted the role |
| Q9 | Token about 600 seconds, no refresh token | **Yes.** `expires_in` was 599 seconds and no refresh token was issued | |
| Q10 | End to end | **Yes.** The service user connected with its key and role; `DESCRIBE TABLE` and `SELECT COUNT(*)` worked on both ceiling tables; `SELECT` on the outside table and an `INSERT` were refused (errno 2003 and 3001); the setup role then dropped the user and role and a check found neither | |
| Q11 | CM2 to CM4 shapes never run live | **Yes.** As the service user itself, `SHOW GRANTS TO ROLE` returned 5 rows and the Connect-mode `verify_grants` **accepted them**; `DESCRIBE TABLE` through the driver returned the columns | The user-level `SHOW GRANTS` check; tag comments through `SHOW` |

## What exists in the account now (until G3)

The sandbox database, the resource monitor, the X-Small warehouse (suspended), the delegated setup role, the custom OAuth integration, **the setup role granted to the owner's user**, and the owner's browser sign-in session on the account URL. The role and user that product-run created were dropped by the setup role. G3 removes everything else and revokes the grant. The warehouse is suspended and the monitor caps spending at 1 credit.

## What this means for the design (a reading, not a decision)

The decision tree in the plan lands on its first branch: **the ceiling holds, the negative tests pass, and a public client works with no secret, so product-run on Snowflake is feasible as designed**, with the registration step (integration, delegated role, ceiling, monitor and warehouse, as built in G1). Two open items remain before it is more than one run's evidence: Q4 (network policy per user) and the `ACCOUNTADMIN` refusal. Each OAuth sign-in needs the admin to log in on Snowflake's own page, so a hosted flow always involves the admin's browser. Databricks is untested.

## Not verified

Everything under "Still to settle". The costs of G1 and G2 were not measured. This was one account on one edition and region.
