# Connect mode threat model (draft, 2026-11-17)

Status: **DRAFT by the implementing agent. Not reviewed. Not a security approval.**
Security reviewer: not yet assigned. Engineering reviewer: not yet assigned. Decision: none.

This is the document the owner asked for under decision G6 of `docs/DECISION_REGISTER_2026-11-16.md` ("No approval now; write the threat model now"). It covers a **designed, unbuilt** capability: Connect mode, in which Compass connects to a *customer's* real warehouse through a read-only identity. It follows the form of `docs/security/ADS-009-m0-threat-model.md`. It approves nothing, changes no code, and contacts no account. A security approval has to cover built and reviewed code, so this document's job is to say what that approval would have to cover and what must exist first.

## What Connect mode is, and is not

Designed in `docs/ONBOARDING_DESIGN.md`: a customer's admin signs in (SSO, a key they control, or by running exported SQL), and Compass creates **only an identity and grants**: a read-only role or service principal with `SELECT` on the schemas the admin names. It creates no database, copies no data, and leaves the customer's tables where they are. After setup the product's query path (already read-only, deterministic, certified-definitions-only) runs against that warehouse.

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
| T1 | A customer admin's token or session is stolen or misused inside our process | Admin sign-in is browser SSO/OAuth; no password handled; token held in memory only (`warehouse_provisioning.py` docstring) | A hosted multi-tenant setup service holding admin tokens is a large blast radius. Decide where setup runs (see decision 1). If hosted: an isolated short-lived worker per run, no logging of tokens, scoped network | H |
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
| T15 | A customer runs a tampered or wrong script on the export-SQL path | The SQL is generated by the same code as automatic setup, so the two cannot differ | Sign or hash the script, show it against the consent record, and run the same grants verification after the admin says it is done | M |
| T16 | Remove access is incomplete: an identity, grant or stored credential survives | Teardown, the sweep and a post-teardown permission check exist (P1, unverified live) | Customer-initiated revoke; proof that Compass deleted its stored credential; retention aligned with `docs/compliance/EA-076-data-lifecycle.md` | M |
| T17 | A Compass operator with secret-store access reads customer credentials | Not assessed | Least-privilege access, break-glass with audit, no standing human access to tenant secrets | H |
| T18 | Supply chain: the optional Snowflake and Databricks drivers, the SDK, certifi | Pinned version ranges in `requirements-warehouses.txt`; not in the default image; `docs/security/EA-073-release-supply-chain.md` | Hash pinning, an SBOM, scanning; the Databricks connector once failed silently on a missing CA store, so transport settings are part of the review | M |
| T19 | Availability: a customer warehouse stalls or a tenant monopolises workers | Timeouts, cancel and a wall-clock bound on every check (live-found and fixed) | Per-tenant concurrency limits, fail-closed on connection errors, a per-tenant kill switch | M |
| T20 | A control we claim does not exist: claims outrun evidence | The claims ladder (decision G10); records state what is not verified | A security approval must list each control with the evidence for it, not a description | M |

## Required before a security approval (acceptance criteria, not a schedule)

1. **A decision on where admin setup runs** (customer-side only, or hosted with an isolated per-run worker) and, if hosted, a design for T1.
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

## Residual risks that remain even then

Compass cannot control the customer's own warehouse configuration, group memberships, network policy or other users with the same role; a name-based redaction rule will always miss some sensitive columns; suppression does not stop differencing; and a read-only identity can still read everything its grants allow. These must be stated to a customer, not engineered away.

## Decisions for the owner (new; none is made here)

1. **Where admin setup runs:** customer-side only (the export-SQL path, the lowest-risk start), or hosted with an isolated worker. Suggestion: customer-side only for the first design partner.
2. **Model egress for customer data:** off by default with a per-customer opt-in, or not offered at first. Suggestion: not offered at first; Connect mode answers questions without drafting.
3. **Default minimum group size:** on (10, as the pilot) or off. Suggestion: on.
4. **Who reviews:** the independent security reviewer and the budget for a penetration test.
5. **A first design partner's profile** (engine, size, data sensitivity) to scope the first build.

Approving this document approves nothing. The security gate for Connect mode is separate and comes only after the build and the evidence above exist.

## Not verified

The ratings are judgment. Whether the P1 tag comments, existence checks, sweep and permission check behave on live Snowflake and Databricks is unverified. I did not assess operator access to the secret store (T17), the customer-side network policy options on each engine, or any legal text. I found no per-tenant connection store in `app/config.py`, but did not read every module, so confirm before relying on that statement.
