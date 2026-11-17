# Connect mode threat model (draft, 2026-11-17)

Status: **DRAFT by the implementing agent. Not reviewed. Not a security approval.**
Security reviewer: not yet assigned. Engineering reviewer: not yet assigned. Decision: none.

**Update 2026-11-17 (superseded the same day):** the owner first chose "Customer-side only", then, after asking for no manual setup or scripts, chose **"Product-run, script as fallback"**. The current decision: the product runs the Connect-mode admin setup after the customer's admin signs in through the vendor's screen and approves a visible list of exact actions; the admin token lives in memory for that run only; exported SQL stays as a fallback. The runtime credential direction is **identity federation** (short-lived tokens, nothing stored), unverified on both engines, with a stored key only as an interim. **T1 returns to full weight for Connect mode.** T15 applies to the fallback path only. T21 to T23 shrink if federation is verified. No build or security gate is approved.

This is the document the owner asked for under decision G6 of `docs/DECISION_REGISTER_2026-11-16.md` ("No approval now; write the threat model now"). It covers a **designed, unbuilt** capability: Connect mode, in which Compass connects to a *customer's* real warehouse through a read-only identity. It follows the form of `docs/security/ADS-009-m0-threat-model.md`. It approves nothing, changes no code, and contacts no account. A security approval has to cover built and reviewed code, so this document's job is to say what that approval would have to cover and what must exist first.

## What Connect mode is, and is not

Designed in `docs/ONBOARDING_DESIGN.md`: a customer's admin signs in (SSO, a key they control, or by running exported SQL), and Compass creates **only an identity and grants**: a read-only role or service principal with `SELECT` on the schemas the admin names. It creates no database, copies no data, and leaves the customer's tables where they are. Under the owner's decision of 2026-11-17 (product-run, script as fallback) the product performs the setup after the admin signs in and approves a list of the exact statements; running exported SQL is the fallback. After setup the product's query path (already read-only, deterministic, certified-definitions-only) runs against that warehouse.

Not in scope: the sandbox mode used so far (the owner's own synthetic data), end-user sign-in, billing, and anything after a first customer.

## What is different from today

Today the product runs against one warehouse the owner controls. Connect mode adds: **customer-supplied connection targets, many tenants each with their own warehouse credential, an admin tool holding customer-admin authority, customer data reaching a model provider, and data in someone else's account.** The settings today hold a single warehouse URL (`ANALYTICS_DB_URL` in `app/config.py`); I found no per-tenant connection store. That is new surface, not a refactor.

## Assets

| Asset | Why it matters |
|---|---|
| The customer's data (rows, schema, comments, sample values) | The thing being protected; some columns are personal |
| The customer admin's authority during setup | Broad: creating roles, users and grants in their account |
| The runtime read-only credential (key pair or OAuth client secret) | Reads everything its grants allow, for as long as it lives |
| The consent record and the audit and evidence trail | The proof of what was agreed and done |
| Compass's control store and secret store | Holds every tenant's connection record |
| The customer's warehouse credits and availability | Queries cost them money and compute |

## Actors and assumptions

Assume: a **malicious or careless customer user** (crafts questions, names, comments); a **malicious tenant** trying to read another tenant's data; an **external attacker** who obtains a credential, or tricks us into connecting somewhere; a **compromised Compass operator or CI** with access to secrets; a **misled admin** who runs a tampered script; a **faulty model** that follows instructions hidden in metadata; and a **faulty setup tool** (a bug destroys or over-grants). We do not assume the cloud vendors are hostile, nor that customers' own identity providers are compromised.

## Trust boundaries and data flows

1. Admin browser to vendor sign-in to Compass setup tool (admin token, in memory).
2. Setup tool to the customer's warehouse (create identity, grants).
3. Compass control and secret store (per-tenant connection record and credential).
4. Query path to the customer's warehouse (read-only, every question).
5. Profiling and drafting to the model provider (schema facts and redacted samples).
6. Evidence, logs and telemetry (what is kept about runs and setup).
7. Export-SQL: Compass to the admin to the admin's own warehouse.
8. Offboarding: removal of access and of Compass's stored credential.

## Threats, existing controls and gaps

Ratings are the author's judgment (impact and likelihood together), not a scored model: **H**igh, **M**edium, **L**ow.

| # | Threat | Control that exists today (verified in the repo unless marked) | Gap before a security approval | Rating |
|---|---|---|---|---|
| T1 | A customer admin's token or session is stolen or misused inside our process | Admin sign-in is browser SSO/OAuth; no password handled; token held in memory only (`warehouse_provisioning.py` docstring) | **Applies to Connect mode again** under the 2026-11-17 product-run decision. Required design: an isolated, short-lived worker per run; the token never logged, never persisted, never in a queue; an Approve screen listing the exact statements before anything runs; the token discarded when the run ends; a hard time limit; a test that fails if the token appears in any log or record | H |
| T2 | The setup tool over-reaches: creates, replaces or drops objects it should not | P1 (PR #54): plain `CREATE`, a tag on what it makes, a preflight that refuses on any name collision, teardown that drops only what this run created, a sweep | **Unverified on a live account.** Connect mode must create *no* database or warehouse, only a role and grants; the sandbox statements are not the Connect statements | H |
| T3 | Over-grant: wildcard or future grants, inherited roles, secondary roles widen what the identity can read | The conformance check `read_only_grants` lists grants and allows only `USAGE`/`SELECT` inside the named database; Snowflake sessions run `USE SECONDARY ROLES NONE` | The check is written for the sandbox's one schema. Needs a per-table allowlist, no wildcard or future grants unless explicitly chosen, a re-check on a schedule (drift), and Databricks group inheritance is invisible to it (direct grants only) | H |
| T4 | The runtime credential is stolen from our secret store, or tenant A's credential is used for tenant B | The pilot keeps secrets in Key Vault, not the control store (`docs/WAREHOUSE_AND_DBT_PLAN.md` safety rules); credentials are never logged or put in evidence (tests) | **No per-tenant connection store exists.** Needs per-tenant secret scope, rotation, short-lived tokens where the engine supports it (Databricks OAuth M2M does), and a check that a connection is bound to one tenant | H |
| T5 | A stolen runtime credential is used outside Compass | The identity is read-only; OAuth M2M tokens last about an hour | Customer-side network policy / IP allowlist for Compass egress addresses; alerting on use from elsewhere; the Remove-access path | M |
| T6 | **SSRF / host injection:** a customer-supplied account or host makes us connect to an internal address or an attacker's server and hand over a credential | None needed today: the warehouse URL is operator-configured. The live runner already refuses a target that does not equal `--expect-target` | Allowlist vendor domain patterns, resolve and pin DNS, block private and link-local ranges, no redirects, exact-match the account the admin approved | H |
| T7 | Model-written SQL or SQL injection through a question | The model only extracts intent; a deterministic compiler builds SQL from certified definitions; `validate_read_only_sql` checks every statement against an allowlist of tables (`app/execution/validation.py`); sandbox and pilot runs passed | The allowlist must come from the admin's per-tenant selection, not a global setting | M |
| T8 | Cross-tenant read | Tenant and purpose are carried on every run; isolation corpus and tests (`tests/test_ads074_isolation.py`, `reference_stack/isolation_eval.py`) | Tests run on fakes and one warehouse. Need real multi-tenant connections, each with its own identity, and a connection-level (not only query-level) isolation test | H |
| T9 | Cost abuse: our queries burn the customer's credits or our compute | Cost ceiling, statement timeout, cancel, statement cap; live-verified on sandboxes | Customer-set budgets and per-tenant rate and concurrency limits; the cost units differ per engine, so ceilings need a per-engine design | M |
| T10 | Small-group inference (a hidden group recovered by subtracting visible groups) | A minimum group size exists but is **off by default** and silently drops groups (`app/compiler/postgres.py`) | Decide the default; the differencing case is not covered by suppression alone; say so in the consent text | M |
| T11 | Personal or sensitive customer data reaches the model provider | The redaction rule (approved for the pilot's Azure OpenAI deployment only): sensitive-name exclusion, cohort floor, placeholders, no value persisted; extended on 2026-11-16 for birth-date names | **Customer data to a model provider is a new egress decision** the existing approval does not cover. The rule is name-based, so a sensitive column with an innocent name leaks (the `date_of_birth` gap was found this way). Needs per-customer opt-in, a data processing agreement, a subprocessor list, and tests against customer-like schemas | H |
| T12 | Prompt injection in customer metadata (comments, names, sample values) steers the drafter | Closed output schema, a leak screen that drops drafted text repeating a kept value, an injection corpus (26 cases) and a poisoned-comment fixture (`reference_data`) | Re-run the corpus against each customer's real metadata shapes before drafting; keep drafts `candidate` | M |
| T13 | Customer data or credentials end up in logs, evidence or telemetry | Evidence stores no result rows; reports are numbers and codes only; errors name types, not messages; connection configs never print secrets | Log-scrubbing tests for connection strings, account identifiers and sample values; a policy for whether account identifiers in the consent record are sensitive | M |
| T14 | Repudiation or tampering with what was agreed or done | Hash-chained evidence trail (`app/runtime/evidence_store.py`, `app/security/audit.py`) | A consent record and a paired teardown record in that trail, with the actor identity of the admin (decisions G8) | M |
| T15 | A customer runs a tampered or wrong script on the export-SQL path (**the fallback path only** under the product-run decision) | The SQL is generated by the same code as automatic setup, so the two cannot differ | Sign or hash the script, show it against the consent record, and run the same grants verification after the admin says it is done | H |
| T16 | Remove access is incomplete: an identity, grant or stored credential survives | Teardown, the sweep and a post-teardown permission check exist (P1, unverified live) | Customer-initiated revoke; proof that Compass deleted its stored credential; retention aligned with `docs/compliance/EA-076-data-lifecycle.md` | M |
| T17 | A Compass operator with secret-store access reads customer credentials | Not assessed | Least-privilege access, break-glass with audit, no standing human access to tenant secrets | H |
| T18 | Supply chain: the optional Snowflake and Databricks drivers, the SDK, certifi | Pinned version ranges in `requirements-warehouses.txt`; not in the default image; `docs/security/EA-073-release-supply-chain.md` | Hash pinning, an SBOM, scanning; the Databricks connector once failed silently on a missing CA store, so transport settings are part of the review | M |
| T19 | Availability: a customer warehouse stalls or a tenant monopolises workers | Timeouts, cancel and a wall-clock bound on every check (live-found and fixed) | Per-tenant concurrency limits, fail-closed on connection errors, a per-tenant kill switch | M |
| T21 | **The runtime credential has to reach Compass without a pasted secret.** Customer-side setup means the customer creates the identity | Snowflake: Compass can generate the key pair itself and put only the public key in the script, so no secret travels from the customer | **Under product-run setup the product creates the identity and obtains the secret itself, so nothing is pasted.** The residual risk is that a long-lived secret is then stored (T23). The agreed direction is identity federation (short-lived tokens, nothing stored), **not yet verified on either engine**; until it is, a stored credential is an interim and must be labelled as one | M |
| T22 | The admin runs only part of the script, edits it, or runs it against the wrong account | The post-setup connection test is the eight conformance checks, including `read_only_grants` | Compass cannot see the customer's session, only what the test proves. The test must fail closed and name what is missing; the consent record must show what the script was, not what was run | M |
| T23 | Key-pair custody: for Snowflake Compass generates and holds the private key | Keys are held in memory in sandbox mode and never written | In Connect mode the private key must be stored, so it joins T4: per-tenant secret scope, rotation, and no copy in logs or backups | H |
| T20 | A control we claim does not exist: claims outrun evidence | The claims ladder (decision G10); records state what is not verified | A security approval must list each control with the evidence for it, not a description | M |

## Required before a security approval (acceptance criteria, not a schedule)

1. ~~A decision on where admin setup runs~~ **Decided 2026-11-17: product-run, script as fallback** (supersedes an earlier "customer-side only" the same day). What remains: the admin-token worker design (T1), verifying federation on both engines (T21 to T23), and signing the fallback script (T15).
2. A **per-tenant connection store**: per-tenant secret scope, no shared warehouse URL, rotation, and a binding of every connection to exactly one tenant (T4, T8).
3. **Connection-target controls**: vendor-domain allowlist, DNS pinning, private-range blocking, exact account match (T6).
4. A **Connect-mode grants generator** that creates only a role and per-table `SELECT`, no wildcard or future grants by default, with `SHOW GRANTS` verification at connect and on a schedule, and group-inheritance handled or disclosed (T3).
5. The **table allowlist wired per tenant** from the admin's selection into the gateway (T7).
6. A **model-egress decision**: per-customer opt-in, a data processing agreement and subprocessor list, a review of the redaction rule against the customer's real schema, and prompt and response logging rules (T11).
7. The **minimum-group-size policy** decided and its limits stated (T10).
8. **Isolation tests on real multi-tenant connections** and an extended abuse-case suite (T8, T12).
9. **Consent, setup and teardown records** with the admin's identity in the evidence trail, and log-scrubbing tests (T13, T14).
10. An **offboarding path** with proof of credential deletion and a retention policy (T16).
11. **Incident response**: a per-tenant kill switch, credential revocation, a runbook, and breach-notification terms.
12. A **live verification** of the P1 setup code on a sandbox (needs the owner's approval), then an **independent security review and a penetration test**, neither of which exists yet.
13. **Operator access controls** and audit (T17), and supply-chain controls for the optional drivers (T18).
14. Honest **compliance status**: Compass has not completed a SOC 2 audit or a formal third-party security review, and the public copy says so.

## How it would be tested

An abuse-case suite per row above (a tenant asks for another's table; a hostile hostname; a wildcard grant; a poisoned comment; a log scrape for a connection string), run first against fakes in CI, then against sandbox accounts with the owner's approval, then by an independent reviewer. The existing eight-check conformance suite becomes the connection test the admin sees.

## Build status of the acceptance criteria (CM2, local code on fakes only)

Code lives in `services/analytics-api/app/connect/`, tested by `tests/test_connect_foundations.py` (97 tests, fakes, no network, no account). None of it has run against a live customer warehouse, and none of it approves a security gate.

| Criterion | Status | Where |
|---|---|---|
| 2 per-tenant connection store | Built locally (in-memory): every connection bound to one tenant; another tenant's lookup is indistinguishable from a missing one; a host is claimed by one tenant only; secrets by reference in a tenant-scoped store with versions and a deletion receipt. **A durable store and a real vault adapter are not built.** | `store.py` |
| 3 connection-target controls | Built: vendor-domain allowlist, no IP literals, userinfo, ports, punycode or private addresses, exact account match, resolved addresses recorded and re-checked. **It does not pin the driver's own lookups; real pinning needs network egress controls.** | `targets.py` |
| 4 grants generator and verification | Built for **Snowflake only**: `USAGE` and per-table `SELECT`, no wildcard, future, `ALL`, grant option or write; per-connection suffix so teardown drops only its own objects; `verify_grants` fails closed on anything extra. **Databricks is not built.** The network-policy statements are unverified live. | `grants.py` |
| 5 table allowlist per tenant | Built: the gateway for a connection is made from that record's allowed tables only, and refuses any other table. | `gateway.py` |
| 9 consent, setup and teardown records | Built: statements shown are bound to a digest and execution refuses anything else; records carry the admin identity and never a token, key or statement body; a guard refuses secret-looking payloads. **Not yet wired to a worker or an API.** | `records.py` |

**CM3 (admin-token worker and Approve step), local on a fake admin session, 129 tests across CM2 and CM3:**

| Threat | What is built | Limits |
|---|---|---|
| T1 admin token in our process | `token.py`: a token that cannot be printed, formatted, pickled, copied or serialised, with a hard lifetime of at most 300 seconds, wiped when the run ends. `worker.py`: signs in once, runs, and wipes; the token is never stored, logged or put in a record, result or error (errors are reported by type only). Tests scan records, results, logs and every object the run touched for the token, on both a failing and a succeeding run. | Python cannot scrub a string from memory; the guarantee is that nothing keeps a reference. **Process-level isolation (one short-lived process or container per run) is a deployment property, not built.** |
| T15, T22 approved statements | `approval.py`: the admin approves a digest of the exact statements, once, before a deadline, and only the signed-in admin can (the requester may differ). The run refuses anything that is not the approved list, and each setup runs once. | The UI that shows the list does not exist yet. |
| T3 partial setup | Preflight refuses any existing name (nothing is adopted); a failure part-way drops only what this run created and reports anything it could not drop; the credential is deleted and the record removed on any failure or cancel; a 120-second deadline between statements. | A single statement is bounded only by the admin session's own timeouts. The preflight `SHOW` shapes and the rollback are **not verified on a live account**. |
| T23 key custody | The private key goes straight into the tenant's secret store and is never held by the worker; deleted with a receipt on failure. | In-memory store only; a vault adapter is not built. |

Still not started: the real sign-in (the connect function is injected; a Snowflake OAuth or SSO admin sign-in for Connect mode is unbuilt and unverified), the onboarding screens, Databricks, the federation check, multi-tenant isolation tests on real connections, the kill switch, offboarding with proof of deletion, and the independent review.

## Residual risks that remain even then

Compass cannot control the customer's own warehouse configuration, group memberships, network policy or other users with the same role; a name-based redaction rule will always miss some sensitive columns; suppression does not stop differencing; and a read-only identity can still read everything its grants allow. These must be stated to a customer, not engineered away.

## Decisions for the owner (new; none is made here)

1. **Where admin setup runs: DECIDED 2026-11-17, "Product-run, script as fallback"** (this superseded "Customer-side only", chosen earlier the same day). Recorded in the program file as `connect_mode_admin_setup_product_run`.
2. **Model egress for customer data: DECIDED 2026-11-17, "Per-customer opt-in"** (off by default; a data processing agreement and a redaction review of the customer's real schema first). Program file `connect_mode_cm1_decisions`.
3. **Default minimum group size: DECIDED 2026-11-17, "Off by default".** Residual risk stays recorded (T10): small groups can identify people and suppression alone does not stop differencing; the pilot demo stays at 10.
4. **Who reviews: DECIDED 2026-11-17, "use specialize agent for review".** A specialist-agent review is advisory. It is **not** the independent human review or penetration test of acceptance criterion 12, which remains unmet; whether it satisfies the gate is the owner's call at the gate.
5. **A first design partner's profile: DECIDED 2026-11-17, "Snowflake, mid-size, low-sensitivity data".** No partner is named or approached.

Approving this document approves nothing. The security gate for Connect mode is separate and comes only after the build and the evidence above exist.

## Not verified

The ratings are judgment. Whether the P1 tag comments, existence checks, sweep and permission check behave on live Snowflake and Databricks is unverified. I did not assess operator access to the secret store (T17), the customer-side network policy options on each engine, or any legal text. I found no per-tenant connection store in `app/config.py`, but did not read every module, so confirm before relying on that statement.
