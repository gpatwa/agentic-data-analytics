# Product-run verification spike: plan (2026-11-17)

Status: **a plan for the owner to review. Nothing here has been run, and approving this plan approves no live step.** Each live gate below needs its own explicit approval in the task that runs it (CLAUDE.md). It is the "CM5e-style verification spike" that `docs/design/CONNECT_PRODUCT_RUN_DESIGN.md` recommends doing before any CM5 product code, on the owner's own Snowflake account, with synthetic data only.

## Why a spike, and what it settles

The design rests on Snowflake behaviour that the documentation does not state or states inconsistently. Building CM5a to CM5c on a wrong assumption would waste the most effort, so the spike answers the questions below first and writes down what it found. It builds no product code.

| # | Question | If yes | If no |
|---|---|---|---|
| Q1 | Can a custom role with only account-level `CREATE ROLE`, `CREATE USER` (and, if needed, `CREATE WAREHOUSE`) create a role and a service user, and set the user's `RSA_PUBLIC_KEY`? | The delegated role is small | Find which extra privilege is needed and whether it is acceptable (for example a `USERADMIN` grant) |
| Q2 | Can that role grant `USAGE` and `SELECT` to the role it created, **only within a ceiling** given to it `WITH GRANT OPTION`, and **not outside it**? | The scope ceiling works as the design intends | The ceiling idea fails; product-run would need `MANAGE GRANTS`, which is too powerful: recommend script-only |
| Q3 | Can the setup role use a **pre-created** warehouse and resource monitor (grant `USAGE` on the warehouse to the new role) without `ACCOUNTADMIN`? | No `ACCOUNTADMIN` needed per setup | The monitor and warehouse must also be handled in the one-time step only |
| Q4 | Can the setup role attach a network policy to a user it created, and with which privilege? | Per-user address restriction works | Put the restriction on the integration only, or on a pre-attached policy |
| Q5 | **Negative tests:** does the setup role fail to grant outside the ceiling, to create an integration, to create a resource monitor, to grant `MANAGE GRANTS`, to grant `SYSADMIN` or `ACCOUNTADMIN` to anything? | The delegated role is contained | **Stop**: this is a finding, report it before anything else |
| Q6 | Does a custom OAuth integration with `OAUTH_CLIENT_TYPE = 'PUBLIC'` give a client id and complete the authorization-code flow with PKCE and **no client secret**? | Compass authenticates with no secret | Test the `private_key_jwt` route (does the integration accept a registered public key?); if neither works, product-run on Snowflake needs a secret handoff |
| Q7 | Are `ACCOUNTADMIN` and `SECURITYADMIN` really blocked at sign-in by default, and does the account parameter named on one Snowflake page exist (read only, **never changed**)? | The design's blocked-roles assumption holds | Reconcile the two pages |
| Q8 | Does `scope=session:role:<NAME>` select the delegated role; what happens when the signed-in user was not granted it? | Role selection works as documented | Adjust the consent step |
| Q9 | Is the access token about 600 seconds, and are refresh tokens absent when `OAUTH_ISSUE_REFRESH_TOKENS = FALSE`? | Matches the design | Adjust the token lifetime handling |
| Q10 | End to end: with an OAuth token for the setup role, do the per-setup statements run through the Snowflake connector (`authenticator="oauth"`), and does a connection as the **new service user (key pair)** pass the two Connect-mode checks? Does teardown by the setup role remove everything it created? | Product-run is feasible on Snowflake | A named step fails; report which |
| Q11 | The CM2 to CM4 shapes that were never run live: `SHOW GRANTS TO ROLE` as the service user itself, `DESCRIBE TABLE` through the driver, tag comments, the sweep. | Those unverified items become verified | Fix the code before anything depends on them |

## Safety design

- **Synthetic data only, a sandbox database.** One database `COMPASS_SPIKE_DB_<suffix>` with three small tables created by the spike: `ORDERS` and `CUSTOMERS` (inside the ceiling) and `PAYROLL_OUTSIDE` (outside it, for negative tests). Each table holds invented values.
- **Names and tags.** Every object is named with a run suffix of 8 hex characters and carries the comment `compass-spike`. Nothing already in the account is touched, adopted or dropped. A preflight lists any existing object with the prefix and **stops** if one exists that is not ours.
- **Cost.** A shared warehouse, `XSMALL`, auto-suspend 60 seconds, on a resource monitor with a quota of 1 credit that suspends at 100 percent. The expected use is a small fraction of a credit; I have not measured it and the monitor is the cap.
- **No secrets printed or stored.** The OAuth token and PKCE verifier live in memory in one throwaway process and are never written, logged or put in a report. **No client secret is ever read**: the spike tests a public client and the key-based route only. If both fail and a confidential client would have to be tried, I stop and ask first. The throwaway key files for the service-user check are deleted afterwards, as in the earlier runs.
- **Your user is touched in one way.** To let you consent as the delegated role, `GRANT ROLE COMPASS_SPIKE_SETUP_<suffix> TO USER <you>` is run; the teardown revokes it. Nothing else about your user, roles or network policy changes. The integration is created with the **default blocked roles unchanged**, and the `OAUTH_ADD_PRIVILEGED_ROLES_TO_BLOCKED_LIST` parameter is read, never altered.
- **Non-TLS redirect, sandbox only.** To receive the authorization code on a local listener, the spike integration sets `OAUTH_ALLOW_NON_TLS_REDIRECT_URI = TRUE` with `http://localhost:<port>/callback`. That is acceptable only because it is a throwaway sandbox integration, dropped at the end; the product design forbids it.
- **Teardown and sweep always run**, created-only, with a final check that no object tagged `compass-spike` remains. A hung run is killed and swept by tag, never abandoned.
- **Stop rules.** Stop and report, without trying to be clever, on: an existing object that is not ours; any negative test that unexpectedly succeeds; any statement that touches something outside the sandbox names; an error naming a quota or billing limit; or an unexpected consent screen scope.
- **How the admin steps run.** The account has no single sign-on provider (earlier error 390190), so administrator SQL is run in the Snowsight worksheet in your signed-in Chrome, as before, with the exact reviewed text. The OAuth sign-in happens on Snowflake's own page and you click the consent; I do not enter your Snowflake password.

## The gates (each needs your explicit approval)

| Gate | What happens | Statements |
|---|---|---|
| **G0 Review** | You read the draft statements below and the questions, and approve or change them. **No account access.** | none |
| **G1 Build the sandbox** | A read-only preflight, then Phase A in the worksheet as `ACCOUNTADMIN`: sandbox database and tables, monitor, warehouse, the delegated setup role and its grants, the ceiling, the OAuth integration, the grant to your user. Then `DESCRIBE` and `SHOW` to record the settings (Q7). | about 25 |
| **G2 Sign in and run** | A local script starts the authorization-code flow with PKCE; you sign in and consent on Snowflake's page (Q6, Q8, Q9). With the token it runs the per-setup statements and the negative tests (Q1 to Q5), then connects as the new service user and runs the two checks (Q10, Q11), then tears down what the setup role created. | about 30 |
| **G3 Tear down and sweep** | Phase D as `ACCOUNTADMIN`: drop the integration, role, warehouse, monitor and database, revoke the grant from your user, then the sweep check. | about 10 |

G1 and G2 may be approved together, G3 always runs after G2 (and after a stop). A numbers-and-names report (`docs/design/CONNECT_PRODUCT_RUN_SPIKE_RESULTS.md`, no secrets) fills the table above with what was observed, labelled as one run on one account.

## Draft statements for review (G0)

Syntax is from memory of Snowflake's reference and **may need correcting at review time**; any change is made visibly before G1. `<S>` is the run suffix.

**Phase A, as `ACCOUNTADMIN` (G1):**

```sql
CREATE DATABASE COMPASS_SPIKE_DB_<S> COMMENT = 'compass-spike';
CREATE SCHEMA COMPASS_SPIKE_DB_<S>.DATA;
CREATE TABLE COMPASS_SPIKE_DB_<S>.DATA."orders"   ("id" NUMBER, "amount" NUMBER(12,2));   -- inside the ceiling
CREATE TABLE COMPASS_SPIKE_DB_<S>.DATA."customers"("id" NUMBER, "region" VARCHAR);         -- inside the ceiling
CREATE TABLE COMPASS_SPIKE_DB_<S>.DATA."payroll_outside"("id" NUMBER, "salary" NUMBER);    -- OUTSIDE the ceiling
-- invented rows inserted into each (a few dozen)
CREATE RESOURCE MONITOR COMPASS_SPIKE_MON_<S> WITH CREDIT_QUOTA = 1
  TRIGGERS ON 80 PERCENT DO NOTIFY ON 100 PERCENT DO SUSPEND;
CREATE WAREHOUSE COMPASS_SPIKE_WH_<S> WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE RESOURCE_MONITOR = COMPASS_SPIKE_MON_<S> COMMENT = 'compass-spike';
CREATE ROLE COMPASS_SPIKE_SETUP_<S> COMMENT = 'compass-spike';
GRANT CREATE ROLE ON ACCOUNT TO ROLE COMPASS_SPIKE_SETUP_<S>;
GRANT CREATE USER ON ACCOUNT TO ROLE COMPASS_SPIKE_SETUP_<S>;
-- the ceiling, with grant option:
GRANT USAGE  ON DATABASE COMPASS_SPIKE_DB_<S>        TO ROLE COMPASS_SPIKE_SETUP_<S> WITH GRANT OPTION;
GRANT USAGE  ON SCHEMA   COMPASS_SPIKE_DB_<S>.DATA   TO ROLE COMPASS_SPIKE_SETUP_<S> WITH GRANT OPTION;
GRANT SELECT ON TABLE COMPASS_SPIKE_DB_<S>.DATA."orders"    TO ROLE COMPASS_SPIKE_SETUP_<S> WITH GRANT OPTION;
GRANT SELECT ON TABLE COMPASS_SPIKE_DB_<S>.DATA."customers" TO ROLE COMPASS_SPIKE_SETUP_<S> WITH GRANT OPTION;
GRANT USAGE  ON WAREHOUSE COMPASS_SPIKE_WH_<S>       TO ROLE COMPASS_SPIKE_SETUP_<S> WITH GRANT OPTION;
GRANT ROLE COMPASS_SPIKE_SETUP_<S> TO USER <your user>;
CREATE SECURITY INTEGRATION COMPASS_SPIKE_OAUTH_<S>
  TYPE = OAUTH ENABLED = TRUE OAUTH_CLIENT = CUSTOM OAUTH_CLIENT_TYPE = 'PUBLIC'
  OAUTH_REDIRECT_URI = 'http://localhost:<port>/callback' OAUTH_ALLOW_NON_TLS_REDIRECT_URI = TRUE
  OAUTH_ENFORCE_PKCE = TRUE OAUTH_ISSUE_REFRESH_TOKENS = FALSE OAUTH_ACCESS_TOKEN_VALIDITY = 600
  COMMENT = 'compass-spike';
DESCRIBE SECURITY INTEGRATION COMPASS_SPIKE_OAUTH_<S>;        -- record settings; no secret is requested
SHOW PARAMETERS LIKE 'OAUTH_ADD_PRIVILEGED_ROLES_TO_BLOCKED_LIST' IN ACCOUNT;   -- read only
```

**Phase C, signed in as the setup role over OAuth (G2), expected to succeed:** `CREATE ROLE`, `CREATE USER ... TYPE = SERVICE`, `ALTER USER ... SET RSA_PUBLIC_KEY`, `GRANT USAGE ... / GRANT SELECT ON ... TO ROLE <new role>` within the ceiling, `GRANT USAGE ON WAREHOUSE`, `GRANT ROLE <new> TO USER <new>`, and an attempt to attach a network policy (Q4, outcome recorded either way).

**Phase C negative tests (each expected to FAIL; a success is a stop):** `GRANT SELECT ON ... "payroll_outside"`, `GRANT MANAGE GRANTS ON ACCOUNT`, `GRANT ROLE SYSADMIN` / `ACCOUNTADMIN` to the new role, `CREATE SECURITY INTEGRATION`, `CREATE RESOURCE MONITOR`, `GRANT ALL`/ownership transfer, and a sign-in requesting `session:role:ACCOUNTADMIN` (expected to be refused at the authorize step).

**Then, as the new service user with its key pair:** `SHOW GRANTS TO ROLE <new>` and `DESCRIBE TABLE` on both tables (the CM4 check shapes), a `SELECT` on the two tables (expected to work), a `SELECT` on `payroll_outside` and any `INSERT` (expected to fail).

**Teardown by the setup role, then Phase D as `ACCOUNTADMIN` (G3):** drop the user and role the setup role created; then drop the integration, the setup role, the warehouse, the monitor and the database, revoke the setup role from your user, and run `SHOW` checks for anything tagged `compass-spike`.

## What the outcomes decide

- **Q5 negative tests pass and Q6 passes (no secret):** product-run on Snowflake is feasible as designed. Proceed to CM5a with the verified privilege list.
- **Q2 fails (no ceiling):** product-run needs `MANAGE GRANTS` or `ACCOUNTADMIN`. Recommend script-only for Snowflake and keep product-run for a partner-application route.
- **Q6 fails on both the public and key-based routes:** product-run needs a secret handoff. That conflicts with the principle of never asking for a pasted secret; recommend script-only until partner registration, and record the weakness if you choose otherwise.
- **Q1 or Q3 needs more privileges than expected:** the delegated role grows; the design and the threat T26 are re-rated before building.
- **Q11 findings** go back into CM2 to CM4 as fixes regardless.

## What this spike does not do

It does not test Databricks (different mechanism, less documented, a separate spike), the hosted callback or Compass's egress addresses, the integration `NETWORK_POLICY` from Compass's real addresses, a customer's identity provider, any account other than yours, or scale. One run on one account says only that it worked once there.

## Owner tasks and open choices for G0

1. Approve or edit the statements above and the gates.
2. Confirm the sandbox may be built in your Snowflake account (the one used for the earlier validation runs), and that granting the delegated role to your own user, then revoking it, is acceptable.
3. Confirm you will click the consent on Snowflake's own sign-in page when asked.
4. Choose the local port for the callback (default: a free one chosen at run time).

Not verified: all statement syntax, every Snowflake behaviour named above, the cost, and the time (I would expect under an hour of wall-clock for G1 to G3 with you present; that is an estimate).
