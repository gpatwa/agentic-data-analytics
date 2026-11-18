# Product-run live rollback check: plan (2026-11-18)

Status: **All gates (G0 to G3) were approved and done on 2026-11-18, see `CONNECT_PRODUCT_RUN_ROLLBACK_CHECK_RESULTS.md`; nothing from the check remains in the account. G0 was approved by the owner on 2026-11-18 with the recommended defaults (program file `connect_product_run_rollback_g0`): R1 and R2 are both included. The offline pre-work is built and tested on the fake; nothing here has been run live, and approving this plan approved no live step.** Pre-work: the harness's one-shot fault option (`--inject-failure-on-step N`, `app/connect/e2e/harness.py`), the decoy statements and the generated statements (`CONNECT_PRODUCT_RUN_ROLLBACK_CHECK_STATEMENTS.md`), and `tests/test_connect_e2e_rollback.py`. Each live gate below needs its own explicit approval in the task that runs it (CLAUDE.md). It follows the end-to-end check (`CONNECT_PRODUCT_RUN_E2E_RESULTS.md`), which showed the product-run path working and its drift refusal holding, and left one piece of CM5e unshown: **what happens on a real account when a setup fails part-way through.**

## What the code does today, and what is unproven

When a run fails after it has started creating things, the worker (`app/connect/worker.py`, `_fail` and `_rollback`) runs the generated drop statements **only for the kinds it recorded as created**, attempts every drop even if one fails, reports anything it could not drop, deletes the credential and marks the connection removed. Everything below was tested only on a fake Snowflake that keeps state.

| # | Question | Why it is still open |
|---|---|---|
| R1 | Can the setup role, signed in through OAuth, actually **drop the role and user it just created**, and does the rollback leave nothing behind on a real account? | The drops have only run against a fake. The setup role has `CREATE ROLE` and `CREATE USER` but no `MANAGE GRANTS`; whether it can drop what it owns is expected, not shown. |
| R2 | On a real failure, does the rollback drop **only what this run created**, and never a pre-existing object that happens to share a name? | Tested on fakes only. This is the property that stops a failed setup from deleting a customer's object. |
| R3 | Do the audit events and the screen tell the truth after a failed run (what was created, what was dropped, nothing left)? | Fakes only. |

**Not in scope, and said up front:**
- A failed **connection test** after a successful setup is a different path: by design the objects stay and the screen says "Set up, not tested yet". It is not a rollback and is not tested here.
- A rollback that itself fails part-way (a drop refused), the process being killed mid-run, a network drop, and the hard deadline: not provoked live. The first is covered on fakes (`test_a_drop_that_fails_is_reported_as_a_leftover`); a killed process leaves objects for the sweep to find.
- Everything the end-to-end check already listed as unshown (an `https` redirect, a real identity provider, durable stores, process isolation, Databricks, scale, independent review).

## How a real failure can be provoked safely

The registration's identity check refuses a run whose setup role has changed, so a failure that **changes the ceiling cannot be used** (it is refused before anything is created, which is the drift result already shown). A failure part-way through has to come from somewhere the checks do not look. Two ways, used together:

1. **R1, an injected statement failure (deterministic).** The harness gets a one-shot, off-by-default option that makes the admin connection raise on a chosen statement of the **next** run: the first seven statements (role, user, key, usage on database and schema, select, usage on warehouse) run **for real**, the eighth (`GRANT ROLE ... TO USER`) raises as a driver error would. The failure is simulated; **the rollback that follows is real**: the product's own drop statements run as the setup role through the real OAuth session. This answers R1 and R3 exactly, and it is repeatable.
2. **R2, a real Snowflake error from a decoy.** After a connection is planned, the review screen shows the names the run will create. As `ACCOUNTADMIN` I create a **decoy user with the planned user's exact name** (no key, no login, tagged as a decoy). Then the run is approved and signed in as usual. Two outcomes are both informative and both are recorded:
   - the setup role **cannot see** the decoy in its preflight `SHOW USERS`, so `CREATE USER` fails with "already exists" **after** `CREATE ROLE` succeeded: a real mid-run failure. The rollback must drop the role it created and **leave the decoy untouched**;
   - the setup role **can see** it, so the preflight refuses with `name_taken` before anything is created: the protection works earlier than expected, the decoy is untouched, and R2's rollback part stays unshown. (I do not know which will happen; Snowflake's `SHOW USERS` visibility for a role that only holds `CREATE USER` is not something I have verified.)

Either way the decoy must still exist, unchanged, after the run. If it does not, that is a **stop and fix before anything else** result.

## Safety design (same as the end-to-end check, plus)

- Synthetic data only, in a new sandbox database `COMPASS_E2E_DB_<suffix>` with a new run suffix; registration objects `COMPASS_REG_*`, per-connection `COMPASS_RO_*` and `COMPASS_SVC_*`. A read-only preflight **stops** if anything under those prefixes or the fixture name already exists. One X-Small warehouse on a 1-credit monitor that suspends at 100 percent; the expected use is a small fraction of a credit (not measured; the monitor is the cap).
- **No lockout, by construction:** the network policy is attached only to the OAuth integration; the run never runs `ALTER ACCOUNT` or `ALTER USER ... SET NETWORK_POLICY`, and the existing stop rule halts the run if a statement would. The decoy is a plain `CREATE USER` with no network policy, key or password.
- **The fault option is a dev tool, not product behaviour:** off unless a flag is passed with `--allow-live`, one-shot, printed when armed, lives under `app/connect/e2e/` and is never imported by the app (a test enforces that), and it can only raise on the harness's own admin connection.
- **No secrets printed or stored**, the harness is local (`127.0.0.1`) with a throwaway token, the service private key lives in its memory only, and the leak scanner from the end-to-end check watches the code, token, verifier and key throughout.
- **The decoy is dropped by exact name only**, in teardown, and only if it is the decoy (checked by its comment) so a real object is never dropped by mistake.
- **Stop rules.** Stop and report, without improvising, on: an existing object that is not ours; a decoy that is missing or changed after the run; anything created by a failed run that remains; a statement that would touch the account or a user's network policy; a token or code in any log; an error naming billing; an unexpected consent scope.
- **Teardown and sweep always run**, created-only by generated names, ending with the eight `SHOW` checks (including **all network policies**) and zero rows. A hung run is killed and swept by prefix.
- The approval window is **5 minutes** (seen in the end-to-end check). I plan a connection only when you are ready, and you then approve, sign in and click Allow straight away.

## Built first, offline, no approval needed

1. The harness fault option (`--inject-failure-on-step N`, one-shot), with tests on the fake: the rollback drops exactly the created kinds, the credential is deleted, the audit shows the failure and the teardown, and a run **without** the flag is unaffected.
2. Generated statements for the new run suffix (preflight, fixture, registration, read-backs, the decoy and its drop, teardown, sweep), published for review in `CONNECT_PRODUCT_RUN_ROLLBACK_CHECK_STATEMENTS.md` and kept equal to the generator by a test. The decoy's exact name is only known after a connection is planned, so its statement is **shown in full in chat before it runs**.
3. A test that the fake reproduces both R2 outcomes (decoy invisible, decoy visible).

## The gates (each needs your explicit approval)

| Gate | What happens | Live statements |
|---|---|---|
| **G0 Review** | You read this plan and the generated statements and approve or change them. **No account access.** | none |
| **G1 Register** | Read-only preflight (stops on anything unexpected); the fixture; the product's generated registration script; read-back fed to the verifier. Same as the end-to-end G1 with a new suffix. | about 30 |
| **G2 Run** | The harness starts with the fault armed. **R1:** plan a connection, you approve, sign in and click Allow, run: the product creates seven things, hits the injected failure and rolls back; I read Snowflake to confirm nothing is left. **R2:** plan a second connection, I create the decoy, you approve, sign in and Allow, run: either the real failure and a rollback that spares the decoy, or the `name_taken` refusal; I confirm the decoy is untouched. Leak scan. | about 25 |
| **G3 Tear down and sweep** | The product's own removal of any connection that exists, the decoy drop, the registration and fixture teardown as `ACCOUNTADMIN`, the setup role revoked from your user, the eight `SHOW` checks (zero rows), the harness stopped. | about 15 |

G1 and G2 may be approved together. G3 always runs after G2, including after a stop. You are needed for the two sign-ins in G2 (about three clicks each); the rest I drive.

## What each outcome decides

- **R1 passes (nothing left, audit accurate):** the rollback is **verified on a real account for an injected failure**; CM5e's last open live item is closed except for the rollback-itself-fails and process-kill cases.
- **R1 leaves something behind or the setup role cannot drop what it created:** stop and fix before anything else: either the delegated privilege list is missing something, or the rollback must change its approach (for example, report the leftover and give the admin the exact drop statements). Re-rate threat T3 (partial setup) in the threat model, which today says the rollback is not verified on a live account.
- **R2 shows the real failure and the decoy survives:** the "only what this run created" property is verified for a name collision.
- **R2 shows `name_taken`:** the preflight protects earlier than the design assumed; record it, and R2's rollback part stays unshown (it can be provoked another way only with product changes, which this plan does not propose).
- **The decoy is dropped or altered:** stop and fix before anything else; this is the control that keeps a failed setup from deleting a customer's object.
- **A secret appears in any log:** stop, fix, rerun G2.

## Owner tasks and decisions for G0

1. Approve or change the gates and the statements (shown in full, generated, before G1).
2. Accept that **R1's failure is simulated** (an injected driver error) while the rollback is real, and that a failure the product cannot be made to produce on its own is not tested.
3. Include **R2 (the decoy)**, or drop it and run R1 only (one sign-in instead of two).
4. **Network address source:** `CURRENT_IP_ADDRESS()` only, as before (recommended).
5. Accept the same local `http://localhost` redirect with the non-TLS flag, the sandbox in the same Snowflake account, the setup role granted to your user and revoked, and that you will sign in and click Allow on Snowflake's own page (your password is never typed by me).
6. Confirm the local web server may run on `127.0.0.1` for the duration and that I may build the fault option and run it against the fake beforehand.

Not verified: the statement forms for the decoy (new), the visibility of a decoy user to the setup role (the point of R2), the cost, and the time (I would expect about an hour with you present; an estimate).
