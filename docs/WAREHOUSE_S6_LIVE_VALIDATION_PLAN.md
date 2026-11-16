# Snowflake and Databricks live validation (stage S6): plan (2026-11-15)

Status: a plan for the owner to decide. Nothing in it is approved or done. No warehouse, account or credential has been touched.

**Update 2026-11-15: pre-work built, then made fully automatic** (owner: "Build pre-work", then "no manual steps, it should be part of
product or setup"). Still no warehouse contacted and no live run approved.
- `app/execution/warehouse_validation.py` is the live runner (the eight checks themselves now live in `app/execution/connector_conformance.py`) (refuses without `--allow-live` and `--expect-target`; eight checks; stops at
  the first failure; numbers-only report).
- `app/execution/warehouse_provisioning.py` is the setup, so there are no scripts to run by hand (the earlier `infra/warehouse-sandbox/`
  scripts were removed). With `--provision` the run signs an admin in through the browser (Snowflake `externalbrowser`, Databricks OAuth),
  creates a separate sandbox, a read-only role or service principal and a fresh credential held only in memory (a new RSA key pair, or a
  one-hour token), runs the checks, and then removes everything in `finally`. No password, key, token, file or environment variable
  is involved, and teardown drops or deletes only the fixed `COMPASS_VALIDATION` / `compass_validation` names it created.
- It is an admin tool kept apart from the product's query path, which stays read-only: only the validation runner imports it, and a
  test checks that nothing else does. This is a narrow exception to "Compass never writes to a warehouse", for the sandbox only.
- Tests: `tests/test_warehouse_validation.py` and `tests/test_warehouse_provisioning.py`, against scripted fakes, one for each stop
  rule and each teardown path.

What is left for the owner, replacing "What I need from the owner" below: the two sandbox accounts (they exist and are signed in), one
browser sign-in approval per engine when the run starts, and the approval of the run itself. Items 3 to 6 of that list (setup scripts, the
read-only identity, how the secret reaches the run, fixtures) are now done by the tool. The Snowflake spending bound is created by the
tool (a one-credit resource monitor on a new warehouse). On Databricks the tool uses an existing SQL warehouse without editing it, so its
auto-stop is whatever the owner already set. Two details differ from the text below: the statement cap defaults to 30, not about 20; and
the Snowflake table is created with quoted lower-case names so the certified `sales-core@v1` works unchanged (no fixture contract). The
Databricks grants check sees direct grants only. Unverified anywhere: all the setup statements, the slow statements, `externalbrowser`
on this Snowflake account (it needs SSO; a password-only login would need another method), whether the workspace allows creating a
catalog and a service-principal token, and everything about the live engines.

## Result: run on 2026-11-15 (owner: "Approve both")

Both sandboxes passed all eight checks, and both were verified clean afterwards. Numbers only; no value was recorded.

| | Snowflake (account `zcpwqmj-hd11049`) | Databricks (workspace `dbc-b8672746-8e43`, catalog `dev`) |
|---|---|---|
| Checks | 8 of 8 ok, 19 statements | 8 of 8 ok, 22 statements |
| Query | 3 rows, estimate 0.002 MiB, 2.7 s | 3 rows, estimate 0.001 MiB, 2.1 s |
| Result equivalence | 3 of 3 rows equal the expected values | 3 of 3 rows equal the expected values |
| Timeout (2 s limit) | stopped, 3.9 s | stopped, 2.5 s |
| Cancel | stopped, 2.0 s | stopped, 11.1 s |
| How the sandbox was made | Run from the owner's signed-in Snowsight, because the account has no SSO identity provider (browser sign-in failed with `390190`). The statements are the ones in `warehouse_provisioning.py`, typed into a new worksheet; the key pair was generated locally and deleted afterwards | Fully automatic with `--provision` (OAuth browser sign-in, then setup, checks and teardown with no manual step) |
| Teardown verified | Database list back to the original five; no `COMPASS_VALIDATION` warehouse in the six listed; the five drop statements all succeeded | No service principal, token, schema or catalog left, checked through the API |

So the automatic path is verified live for Databricks only. For Snowflake the setup SQL and the checks are verified, but the automatic admin sign-in is not, and
cannot work on an account without SSO; a password prompt (or key-pair admin sign-in) is the missing method.

What the live runs found, each fixed in code with a test that reproduces it:
1. Snowflake: with the async path a cursor has no column names until the first fetch (the grants check raised `TypeError`).
2. Databricks: creating a token for a service principal needs that principal granted permission to use tokens first (`PermissionDenied` otherwise).
3. Databricks: some Python builds have no default CA bundle, so the SQL connector failed its TLS handshake and then retried for minutes, which looked like a hang. The session now passes `certifi`'s bundle and caps retries at 30 seconds.
4. Databricks: `cursor.cancel()` does nothing until the first server reply has given the command id ("no currently executing command"), so cancel now waits for the id.
5. The runner had no hard time limit: a statement that ignores the timeout could hang it. Every check is now bounded, and the timeout check cancels a statement that does not stop.
6. One run was interrupted by hand and skipped its teardown; the leftovers (the principal and the schema) were removed by hand through the API and confirmed gone.

Not proven: scale, joins, ratios, any other account or edition, OAuth or SSO as the runtime identity (the sessions use a key pair and a token),
and the Snowflake admin sign-in without SSO. The sandboxes held only the 100-row synthetic table. This validates the sessions and the
gateways; it does not by itself unlock a public claim, which is its own reviewed change.

## What S6 is for

Everything about the two engines so far is unverified on a live engine: marker syntax, the statement-timeout error, cancel, the
shape of `EXPLAIN` output (so the cost estimate), type and time-zone handling, and how the read-only role behaves. S6 answers
those on a **sandbox** and records the answer. It unlocks one public sentence per engine, "Runs on Snowflake (sandbox
validated)", and nothing about customers. A customer's warehouse is never used.

## What the run does (per engine, one pass, small)

| # | Check | How | Pass looks like |
|---|---|---|---|
| 1 | **Read-only role** | Read the role's grants (`SHOW GRANTS` / `information_schema`); no write is attempted | Only `USAGE` and `SELECT` on the one sandbox schema; nothing else |
| 2 | **Metadata read** | The existing warehouse metadata provider over the real session, one table | The synthetic `sales_orders` columns and types come back, stable asset ids |
| 3 | **One compiled query** | The certified `sales-core@v1` revenue-by-month intent, compiled for the engine, through the gateway (validation, cost estimate, execute) | Gateway returns rows with no error and a cost estimate |
| 4 | **Result equivalence** | Compare the rows to the expected values computed in plain Python from the seed formula | Same months, same sums, same row count (this is where decimal and timestamp handling would show) |
| 5 | **Cost ceiling** | Run once with a ceiling set below the estimate | The gateway refuses before submitting the query |
| 6 | **Timeout** | A constant slow read-only statement through the session, 2 second timeout | A timeout error, mapped to `ExecutionTimeout`, and the warehouse shows the statement stopped |
| 7 | **Cancel** | The same slow statement, cancelled from another thread after about 1 second | `ExecutionCancelled`, statement stopped on the warehouse |
| 8 | **Nothing leaks** | Search the run log and report for the secret values, the key path and the query text | None found |

Checks 6 and 7 use fixed slow statements through the session directly, because the gateway's allowlist would refuse them. They
are constants in the harness, never built from input, and read-only. At most about 20 statements per engine in total.

## What I need from the owner, per engine

1. **A sandbox account the owner creates.** I will not create an account. Snowflake: a trial or an existing dev account.
   Databricks: a workspace with a SQL warehouse (serverless if available). Trial terms, credits and free-tier limits are unchecked,
   so the owner checks them. The compute bill is the vendor's and the owner's.
2. **A spending bound the owner sets in the vendor's own controls:** a Snowflake resource monitor with a small credit quota and a
   warehouse of the smallest size with a short auto-suspend (about 60 seconds); on Databricks the smallest SQL warehouse with a short
   auto-stop. I add a statement cap on my side but the vendor-side bound is the real one.
3. **The sandbox data, loaded by the owner or an admin.** I will supply `sandbox_setup.snowflake.sql` and `sandbox_setup.databricks.sql`
   (create a schema, the `sales_orders` table with the same four columns as the pilot, the same 100 generated rows, a read-only role
   and a service user). Compass never writes to a warehouse, so I do not run them and no admin credential comes into this session.
4. **A read-only identity limited to that one schema.** Snowflake: a service user with a key pair and a role holding `USAGE` and
   `SELECT` only. Databricks: a service principal or user with a personal access token, limited the same way.
5. **The authentication decision** (decision 3 of the warehouse plan, a security gate): key pair for Snowflake and an access token
   for Databricks are what the sessions implement. If the owner wants OAuth or SSO, that is a separate build and review.
6. **How the secret reaches the run.** Either the owner exports the variables in their own shell and runs one command, and I read
   only the report; or the secret sits in a Key Vault the owner names and I read it into the process environment at run time, never
   printed. The owner chooses. The Snowflake private key is a file, so it needs a local path with owner-only permissions.
7. **Network.** Trial Snowflake accounts and default Databricks workspaces normally allow any address, but a network policy or an
   IP access list would block this machine. If so, the same pattern as M4b applies: one temporary one-address rule, created and
   removed in the same task, removal verified. I would check before assuming.
8. **The approval itself,** in the task that runs it, naming: the engine, the account or workspace host, the schema, the statement
   cap, and that it is sandbox only. One engine per approval is cleaner; two back to back is fine if both are named.

## Work I can do before any approval (no cloud contact, one pull request)

- **`warehouse_live_validation.py`**, a command-line runner like `draft_run.py`: refuses without `--allow-live` and without
  `--expect-host` (or `--expect-account`) matching the environment, reads credentials from the environment only, applies the statement
  cap, runs the eight checks in order, stops at the first stop rule, and writes a numbers-only report with no query text or value.
  Tested against the fake drivers, including every stop rule.
- **A small adapter** that gives the existing metadata providers a `QueryRunner` over the real session.
- **The two setup scripts** and a Snowflake and a Databricks variant of the fixture contract if the physical names need a different
  case (Snowflake folds unquoted names to upper case). These are test fixtures over a synthetic table, not business definitions, so
  they are not a semantic certification. The owner should confirm that reading, since only certified contracts execute.
- **The expected-rows calculator** from the seed formula, so check 4 needs no second database.

## Stop rules (the run ends and is reported, not retried)

Any secret, key path or query text in the log or report; a role with a privilege beyond `USAGE` and `SELECT` on the one schema;
any table outside the allowlist touched; a result that differs from the expected rows; an estimate above the cap; a statement
that does not stop on timeout or cancel; the statement cap reached; or any unexpected error from the engine. After the run I
unset the secrets, list anything I changed (nothing, except a temporary network rule if one was needed, which I remove and verify),
and tell the owner what to suspend or delete on the vendor side.

## What gets recorded

A numbers-only report per engine (checks passed or failed, estimate, timings, no values), and a record in the program file in the
owner's words, in the style of the 2026-11-13 pilot validation. If a check fails, that is the finding: the claim does not unlock
and the fix is a normal pull request.

## What a pass unlocks, and what it does not

Unlocks, per engine, once recorded: the sentence "Runs on Snowflake (sandbox validated)" or "Runs on Databricks (sandbox
validated)", on the site and in the FAQ, as its own reviewed change, and the roadmap card can move from planned. It does **not**
unlock: runtime selection by a real tenant, a business contract on either engine, any statement about customers, scale,
joins or ratios (still unsupported), OAuth or SSO, native-semantics import (S7), or drafting on the engine (S8). Nothing is
deployed by S6.

## Cost

A sandbox run is a few seconds of the smallest warehouse. The real cost is the owner's time to create the accounts, run the setup
scripts and set the vendor-side limits. Vendor terms were not checked.

## Risks I can see

- Cancel is the most likely thing to behave differently from the docs: Snowflake's async plus abort by query id, and Databricks'
  `cursor.cancel()` under the default backend, are both written from the drivers' APIs, not run.
- `EXPLAIN` output parsing (Snowflake JSON `bytesAssigned`, Databricks `EXPLAIN COST` text) may not match. A mismatch gives no
  estimate and the gateway refuses to run, which is the safe failure, but it would block check 3 until fixed.
- Cost units are MiB scanned, an engine estimate. The default ceiling (1,000,000 units) was chosen for PostgreSQL's units and
  should be set deliberately for each engine.
- A sandbox on a trial can expire or throttle mid-run. That would be reported as an unexpected error, not retried.

## Decisions for the owner

1. **Engine order.** Snowflake first, Databricks first, or both in one task? (Earlier recommendation: whichever a design partner
   uses; otherwise Databricks.)
2. **Do you have, or will you create, a sandbox account for each?** Who sets the vendor-side spending bound?
3. **Authentication:** key pair for Snowflake and an access token for Databricks as built, or something else (a separate review)?
4. **How the secret reaches the run:** you export it and run one command, or Key Vault (name it).
5. **Go-ahead for the pre-work pull request** (runner, adapter, setup scripts, fixtures), which touches no cloud.
6. **Fixture contracts:** are test fixtures over a synthetic table acceptable for validation, given that only certified contracts
   execute?

Approving the pre-work approves no live run. The live run needs its own approval in the task that does it, naming the items in
"What I need from the owner", point 8.
