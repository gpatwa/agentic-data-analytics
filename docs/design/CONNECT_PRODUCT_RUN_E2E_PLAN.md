# Product-run live end-to-end check: plan (2026-11-18)

Status: **a plan for the owner to review. Nothing here has been run, and approving this plan approves no live step.** Each live gate below needs its own explicit approval in the task that runs it (CLAUDE.md). It follows the product-run spike (`docs/design/CONNECT_PRODUCT_RUN_SPIKE_RESULTS.md`), which showed the *pieces* work on one account, and the CM5a to CM5d build, which has only run on fakes. This check runs **the product's own code** (registration script, sign-in, setup, connection test, screens) against the owner's Snowflake account with synthetic data only.

## What it settles, and what it deliberately does not

| # | Question | Why it is still open |
|---|---|---|
| E1 | Does the registration script **exactly as the product generates it** (with the network policy) run on a real account, and does `verify_registration` accept what Snowflake reports? | Never run live; the spike used hand-written equivalents and no network policy |
| E2 | Does `CREATE NETWORK POLICY` plus `NETWORK_POLICY` on the integration work, and does a code exchange from an allowed address succeed and one from a disallowed address fail? | Not verified (spike Q4 and design "not verified" list) |
| E3 | Does the **hosted sign-in code** (start, binding cookie, callback, exchange, hand-off) work with Snowflake's real redirect and a real browser? | Only run against a fake token endpoint and a hand-run script |
| E4 | Do the identity checks (`CURRENT_ACCOUNT()`, `CURRENT_ROLE()`, `SHOW GRANTS TO ROLE` as the setup role) and the preflight (pre-created warehouse and monitor not flagged) work through a real OAuth session? | Role was verified in the spike; account, grants-as-setup-role and preflight shapes were not |
| E5 | **Live negative:** if the admin widens the setup role's grants after registration, does the product refuse with `registration_changed` and create nothing? | Tested on fakes only |
| E6 | Does a run through the product create exactly the approved statements' effects, pass `verify_grants`, and leave a connection that the **connection test through the real driver session** activates? | New code path (`grants_connection_test` with a real `SnowflakeSession`) |
| E7 | Do the **screens** work against a real backend: draft, register, plan, approve, sign in, return, run, "Connected and tested"? | Clicked through once against a stub only |
| E8 | After the whole run, does any token, code, verifier or private key appear in the audit chain, the logs or any response? | Tested on fakes; the live logs are the real test |
| E9 | (Optional) Is a sign-in requesting `ACCOUNTADMIN` refused? | Spike Q7, not observed |

**Not settled, and said so up front:**
- **An `https` redirect** (the check uses a local `http://localhost` redirect with the non-TLS flag, as the spike did; the product's generator refuses that on purpose, so the harness deviates in exactly one place and the verifier must flag it, see E1).
- Deployment anywhere (no cloud is touched), durable stores, process isolation, a vault, Databricks, scale, other accounts or editions, and the independent review.
- A **live rollback after a mid-run failure** (hard to provoke safely; covered on fakes).
- Running a governed question over the new connection (no certified contract exists for the synthetic tables).

## How it runs (and what has to be built first, offline, no approval needed)

A small **local harness** starts the real FastAPI app on `127.0.0.1` with the real routers and in-memory stores, a static-token identity verifier (a fixed test token mapped to a connection manager), the built web UI served from the same origin with a single-page fallback (so the sign-in callback, the cookie and the post-callback redirect work as they would behind the product's proxy), and real Snowflake sessions. It is a dev tool under `scripts/`, never imported by the app, and refuses to start without `--allow-live` and a matching `--expect-account`.

Two small pieces it needs do not exist yet and are built and tested **offline first**:
1. A `snowflake_session_for(record, secrets)` helper that opens a read-only service-user session from a connection record (the existing code only builds a gateway). Same key handling as `snowflake_gateway_for`.
2. The harness itself, with a leak scanner that fails the run if the token, code, verifier or private key text appears in its log or any response.

## Safety design

- **Synthetic data only**, in a sandbox database `COMPASS_E2E_DB_<suffix>` with two ceiling tables and one table outside it, created by a short reviewed fixture script (reusing the spike's statements for the data part).
- **Names and tags.** Registration objects `COMPASS_REG_*`, per-connection `COMPASS_RO_*` and `COMPASS_SVC_*`, all tagged `compass-connect`. A read-only preflight **stops** if anything under those prefixes or the fixture name already exists.
- **Cost.** One X-Small warehouse on a 1-credit monitor that suspends at 100 percent. Expected use is a small fraction of a credit; not measured; the monitor is the cap.
- **No lockout, by construction.** The network policy is attached **only to the OAuth integration** (that is what the generator does). The check never runs `ALTER ACCOUNT` or `ALTER USER ... SET NETWORK_POLICY`, and a stop rule halts the run if a statement would. The integration's policy covers only the code exchange and token use, not the owner's normal sign-in.
- **No secrets printed or stored.** The service user's private key lives in the harness process memory and in no file; the OAuth token and code never reach a log or a file; no client secret is read (the client is public). The harness process is stopped, and its memory discarded, at the end.
- **The owner's user** is touched in one way: the setup role is granted to it, then revoked at the end.
- **Egress address for the policy.** The harness's token exchange comes from the owner's machine, so the policy must allow its public address. Options (decision 2 below): ask Snowflake for it with `SELECT CURRENT_IP_ADDRESS()` in the worksheet (no third party), and/or an IP-echo service as in the earlier firewall work (sends the owner's public address to a third party). Both IPv4 and IPv6 are allowed if the browser and the harness use different families.
- **Stop rules.** Stop and report, without improvising, on: an existing object that is not ours; any negative test that unexpectedly succeeds; a statement that would touch the account or a user's network policy; a token or code in any log; an error naming billing; or an unexpected consent scope.
- **Teardown and sweep always run**, created-only by generated names, ending with the same six `SHOW` checks and zero rows. A hung run is killed and swept by prefix.

## The gates (each needs your explicit approval)

| Gate | What happens | Live statements |
|---|---|---|
| **G0 Review** | You read this plan and the exact generated statements and approve or change them. **No account access.** | none |
| **G1 Register** | Read-only preflight; fixture script; the product's generated registration script (with the one declared local-redirect deviation), as `ACCOUNTADMIN` in your Snowsight worksheet; read-back of `DESCRIBE SECURITY INTEGRATION` and `SHOW GRANTS` fed to `verify_registration` (E1, E2 part 1). | about 30 |
| **G2 Run the product** | The harness starts; through the **web UI** the harness registers Compass, plans a connection for one ceiling table, you approve, then you **sign in and click Allow** on Snowflake's own page (E3); the product runs the setup and the connection test (E4, E6, E7). Then the negative checks: widen the setup role's grants as `ACCOUNTADMIN` and attempt a second connection (E5), then undo; optionally restrict the policy to a wrong address and attempt a sign-in (E2 part 2), then restore. Optional: one `ACCOUNTADMIN`-scope sign-in, looked at and not clicked (E9). Leak scan (E8). | about 25 |
| **G3 Tear down and sweep** | The product's own removal statements, then the registration teardown and the fixture teardown as `ACCOUNTADMIN`; revoke the setup role from your user; the six `SHOW` checks (zero rows). The harness is stopped. | about 15 |

G1 and G2 may be approved together. G3 always runs after G2, including after a stop.

## What each outcome decides

- **E1 to E8 pass:** product-run is **verified end to end on one account in a sandbox, with a local non-TLS redirect and a stubbed identity verifier.** The remaining list is then: an `https` redirect on a real deployment (needs a deployment, which needs your explicit ask), a real identity provider, a durable store and vault, process isolation, the independent review and a design partner.
- **E1 fails (the generated script does not run, or the verifier rejects what Snowflake reports):** fix the generator or verifier and repeat G1 only; nothing in G2 has run.
- **E2 fails (network policy form wrong or the integration rejects it):** the restriction moves to a different mechanism and the threat T26 control is re-rated; the rest can still run with the policy removed, recorded as such.
- **E5 fails (the product creates a connection under a drifted setup role):** **stop and fix before anything else.** This is the control that keeps a widened ceiling from being used.
- **E8 fails (a secret appears in a log):** stop, fix, rerun G2.

## Owner tasks and decisions for G0

1. Approve or change the gates and the statements (shown in full, generated, before G1).
2. **Network address source:** `CURRENT_IP_ADDRESS()` only (recommended), or also an IP-echo service.
3. Accept that the sign-in redirect is the local `http://localhost` one with the non-TLS flag, and that the `https` redirect stays unverified.
4. Confirm the sandbox may go in the same Snowflake account, that the setup role is granted to your user and revoked, and that you will sign in and click Allow on Snowflake's own page (your password is never typed by me).
5. Confirm the local web server may run on your machine on `127.0.0.1` for the duration, and that I may build the harness and run it against fakes beforehand.

Not verified: the statement forms (several are the spike's, two are not), the cost, and the time (I would expect one to two hours with you present; an estimate).
