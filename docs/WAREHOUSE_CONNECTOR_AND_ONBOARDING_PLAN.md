# Warehouse connectors and onboarding: plan (2026-11-15)

Status: a plan for the owner to decide. **C1 was approved ("Approve C1") and built on 2026-11-15**: `app/execution/connector_conformance.py` (the library: capability records, the eight checks, capability gating), `docs/WAREHOUSE_CONNECTOR_CAPABILITIES.md` (generated from it, and a test fails if the two drift) and `tests/test_connector_conformance.py`; `warehouse_validation.py` is now only the live runner around it. **C2, Databricks OAuth M2M, was approved ("Yes, build C2 M2M") and built the same day**, in part: the Databricks session now signs in with a service principal's client credentials (the SDK exchanges them for short-lived tokens and refreshes them), `DatabricksConfig` reads `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET`, and the sandbox provisioner creates a one-hour service-principal secret by default (`--databricks-auth token` keeps the legacy token). It was then run live on the owner's Databricks sandbox on 2026-11-16 (owner: "Approve the run"): 8 of 8 checks ok, 22 statements, teardown verified through the API (no principal, secret or schema left). The setup reported the same step count as the token path, and the default method is OAuth M2M; no token was created by this run. The other C2 items (External OAuth for Snowflake, a key-pair or typed-password admin sign-in) are not built and still need the owner's decisions. Everything else in this plan is not approved, built or run. It follows the owner's two asks of 2026-11-15: "we should be able to use some connector framework" and "these setup steps should be part of the product onboarding experience". It builds on `docs/WAREHOUSE_AND_DBT_PLAN.md` and the recorded sandbox results in `docs/WAREHOUSE_S6_LIVE_VALIDATION_PLAN.md`.

## What we learned that shapes the plan

From the first live run (Snowflake and Databricks sandboxes, 8 of 8 checks each, both removed afterwards):

- **Every bug was connector glue, not governance.** Five of them: Snowflake column names appear only after the first fetch; a Databricks service principal must be allowed to use tokens before one is created for it; a Python with no default CA bundle made the Databricks connector retry for minutes (it looked like a hang); `cursor.cancel()` does nothing until the driver has a command id; and the runner had no hard time limit. A mature connector layer absorbs some of these. The eight-check harness found all of them in two runs.
- **Admin sign-in is the weak point.** The Snowflake account had no SSO identity provider (error `390190`), so browser sign-in could not work. A product cannot assume SSO.
- **Auth guidance has moved.** Databricks recommends OAuth machine-to-machine with a service principal and treats personal tokens as legacy; Snowflake favours External OAuth, then key pair, for service users (sources are vendor documentation and vendor-adjacent blogs; check the current pages before building).
- **What no framework supplies** is the product's actual value: read-only execution, a cost ceiling, a table allowlist, cancellation, and a compiler that never runs model-written SQL.

## Position on connector frameworks

The connector framework is **our own session interface plus the live conformance harness**. Frameworks supply transports, not governance.

| Option | Verdict | Why |
|---|---|---|
| Keep `WarehouseSession` and make the eight checks a reusable acceptance suite | **Do** | It already caught five bugs, it is engine-neutral, and any new engine or driver must pass it |
| ADBC (Arrow) drivers behind the same interface | **Spike, time-boxed** | One API across engines, but the Snowflake and Databricks ADBC drivers moved out of Apache to a community foundation in the ADBC 24 notes, so maturity, cancel and timeout behaviour must be proven by our suite |
| Ibis or another query layer in place of our compiler | **No** | Our deterministic compiler, golden SQL and single-dataset guarantee are the product |
| ELT frameworks (Airbyte, Meltano, dlt, Fivetran) | **No** | They copy data; we query in place, read-only |
| Vendor MCP servers (Snowflake managed, Databricks managed) | **Not a connector** | They let agents write SQL. Possible later as an extra front door, or as an execution backend only behind our compiler |
| Gateways for SaaS sources (CData, Nango, Composio and similar) | **Out of scope** | For SaaS APIs, not governed warehouse queries |

## Workstreams

### C1. Connector contract and conformance suite (local, no cloud)
Make the contract explicit and testable: the `WarehouseSession` protocol, a **capability record** per engine (statement timeout, cancel, cost estimate, read-only proof, supported auth methods, TLS and retry settings), and the eight checks as a library that any session factory runs, with fakes for CI and a live mode behind the existing guards.
Exit evidence: the existing Snowflake and Databricks sessions pass the suite against fakes; a deliberately broken fake fails each check; the capability table is generated, not hand-written.

### C2. Authentication (local build; live only with approval)
- **Databricks: move from a personal token to OAuth M2M** (client id and secret for a service principal; short-lived tokens refreshed by the SDK).
- **Snowflake: keep key pair; add External OAuth** where the customer has an identity provider (later).
- **Admin sign-in methods**, in this order: browser SSO (works today where SSO exists), a key-pair admin credential, and a password typed by the person into a prompt (never stored, never in the environment). The password method is a **security gate**: it needs the owner's explicit approval (decision 3 of the warehouse plan).
Exit evidence: fake-driver tests per method; a live sandbox run for each method that is approved.

### C3. Provisioning as a product capability (local build; live only with approval)
Turn `warehouse_provisioning.py` from a validation helper into the product's setup service, kept apart from the query path:
- **One source of the statements.** The same code that runs setup can **export it as SQL** for a customer's admin to run (some organisations will not let a third party create users). No second hand-written copy to drift.
- **Safe by construction:** fixed, prefixed names; a collision check before creating anything (a name that exists and is not ours stops the run); idempotent and resumable; teardown always; leftover detection after an interrupted run (this happened once in the live run and was cleaned by hand).
- **Spending guard** created with the sandbox (Snowflake resource monitor and the smallest warehouse; on Databricks the warehouse is chosen, never edited, and its auto-stop is shown).
- **Audit events** for every setup and teardown step: names and counts only, never a credential.
- **Boundary:** a separate module and permission from the runtime; a test already enforces that only the validator imports it, and the setup service would keep that rule.
Exit evidence: collision, resume, leftover and export tests; the exported SQL equals what the runner executes.

### C4. Onboarding experience (design first, then build)
Two people use it: an **admin** once, and **analysts** daily. The admin path must be short, reversible and explain itself.
1. **Choose the warehouse** (PostgreSQL, DuckDB, Snowflake, Databricks).
2. **Connect:** sign in as the admin by the best available method; offer the export-SQL path and "I will run it myself" beside it.
3. **Consent preview:** the exact objects to be created, the permissions, the cost cap, and what Compass will never do, before anything runs. One page a customer security team can forward.
4. **Live progress:** the eight checks as the connection test, in plain words, with a clear result and a one-click remove.
5. **Choose tables:** a schema and table allowlist; show what the model would see under the approved redaction rule before any draft (links the auto-ontology work: draft, then a person certifies).
6. **First governed question**, with a target of under five minutes from "connect" to a certified-definition answer on the sample table.
Error handling is a feature: a **code-to-fix catalogue** ("this account has no SSO, use a key", "you need a workspace admin", "cannot reach the workspace; check your network or certificate store"). The five-minute silent retry we hit would be a churn event.
Exit evidence: wireframes and the error catalogue reviewed by the owner; a click-through prototype; usability checks with two or three real admins (we have none yet); measures defined up front (time to connect, completion rate, failure codes).

### C5. ADBC and driver spike (time-boxed, local)
Run the C1 suite against the ADBC Snowflake and Databricks drivers and a vendor driver side by side. Decide on cancel, timeout, parameter binding, install weight, licence and maintenance. Time-box: two working days; the output is a recommendation, not a migration.

### C6. Next engines (owner's choice)
BigQuery, Redshift or others only on the owner's say. The rule is the same for each: implement the session, pass the C1 suite on fakes, then a sandbox run with approval. Compiler limits (single dataset, no joins or ratios) still apply.

## Order and approvals

C1 first (it protects everything else), then C2 and C3 in parallel, C5 beside them, C4 design in parallel with C1 to C3 and its build after. Everything is local and fake-driver based until a stage says otherwise. A live run needs the owner's explicit approval in the task that does it, naming the engine, host and cap, as in S6. Provisioning **a customer's** warehouse (as opposed to the owner's sandbox) is a new risk class and needs its own security approval before any build that touches one.

## Safety rules that do not change

Only certified definitions execute; the model never writes SQL; the product's query path stays read-only; credentials never reach the control store, logs, evidence or snapshots; no secret is pasted, stored or exported by the onboarding flow; a customer's production warehouse is never used for validation; and no public claim moves ahead of its evidence ("Runs on Snowflake (sandbox validated)" is still its own reviewed change).

## Risks

- Wizard scope creep: onboarding can swallow the roadmap. Keep C4 to connect, consent, check, choose tables, first question.
- Admin credentials in a hosted product are a large trust ask. The browser sign-in and the export-SQL path exist to avoid holding them.
- Vendor behaviour changes (driver versions, auth deprecations). The conformance suite and a pinned extras file are the defence; the capability table shows what each version supports.
- ADBC may prove immature on cancel or timeouts. That is a finding, and the answer is to keep the vendor drivers.
- Usability claims need real admins; we have none yet, so early UX decisions are reasoned, not measured.

## What stays out of this plan

OAuth or SSO for **end users** (a separate identity topic), joins and ratios, native-semantics import, ELT, vendor MCP as a connector, any deploy, and any Terraform change.

## Decisions for the owner

1. **Approve C1 (the connector contract and conformance suite) as the first build?** Recommended: yes.
2. **Databricks auth:** move to OAuth M2M now (recommended), or keep the token for the pilot only.
3. **Admin sign-in methods:** which to support, and is a typed-password prompt acceptable (a security gate)?
4. **Export-SQL path:** keep a manual route generated from the same code (recommended), or hosted-only?
5. **Spike C5 (ADBC):** run it now, later, or skip.
6. **Onboarding C4:** start design now (wireframes and the error catalogue) even before any build? Recommended: yes.
7. **Which engine after these two**, if any (C6)?

Approving any of this approves no live run, no customer provisioning, no deploy and no public wording.

## Not verified

Everything about ADBC drivers, External OAuth and OAuth M2M behaviour comes from vendor documentation and vendor-adjacent articles read on 2026-11-15, not from running them. Funding and market claims in the research that motivated this plan are from press coverage, not primary sources. No usability evidence exists.
