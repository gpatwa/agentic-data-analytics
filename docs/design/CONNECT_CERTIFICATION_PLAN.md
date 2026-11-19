# Making a connected Databricks workspace answerable: the certification plan (2026-11-18)

Status: **C0 was approved by the owner on 2026-11-18 with the recommended answers (program file `connect_certification_c0`). C1 is built offline and tested on fakes (below); nothing has run against a workspace or a real model, and C2 to C4 are not approved.** Each live gate needs the owner's explicit approval, with the exact statements shown first (CLAUDE.md). The semantic-certification gate stays human: nothing below certifies a definition, and nothing may claim one is certified because code or a fixture says so.

## The problem

A connected workspace now has a verified connection, a secret in custody and a gateway (`CONNECT_DATABRICKS_DESIGN.md`). A governed question still fails closed for that tenant, because a run needs, for the tenant's own tables: a **certified semantic contract**, a **context and ontology snapshot**, and a **compiler for the workspace's dialect** whose output the gateway's allowlist accepts. None exists for a customer's tables, and the pilot runtime builds one compiler (PostgreSQL), one contract list and one gateway at start-up from settings.

## What already exists and can be reused

- **The drafter** (`app/ontology_draft/`): introspect, profile with the approved redaction rule, draft with a structured-output model, validate (every metric compiled through the real compiler plus a read-only SQL check), emit a `draft` registry document and `candidate` ontology nodes. Run against a synthetic corpus and once against the pilot warehouse; **never against Databricks and never through a connection's principal**. The evaluation thresholds were approved for the pilot.
- **The Databricks compiler adapter** (`DatabricksCompilerAdapter`, backtick quoting, `catalog.schema.table`) and the conformance tests.
- **The Git-backed registry** (`SemanticRegistry.get_certified`): only certified documents execute.
- **The connection** (record, ceiling as the allowlist, per-tenant gateway) and the per-tenant resolver.

## The gaps, and the proposed design for each

| # | Gap | Proposed design |
|---|---|---|
| 1 | The drafter reads through SQLAlchemy against Postgres | A Databricks metadata and profile source that reads **only through the connection's own principal** (it already holds `SELECT` on exactly the named tables) via the product's session: columns and types from `DESCRIBE`/`information_schema`, profiles as bounded `SELECT`s under a row limit and statement timeout. It can only ever see the ceiling. |
| 2 | The probe for the compile check is Postgres `EXPLAIN` | A Databricks `EXPLAIN` probe through the same session (the gateway already uses `EXPLAIN COST`). |
| 3 | One physical binding per contract | **One contract per connection** (recommended): a contract's physical names are the connection's `catalog.schema.table` names, and a check at activation requires every table the contract uses to be inside the connection's allowlist, so a contract can never reach beyond the admin's ceiling. This avoids certifying one contract with per-engine bindings. |
| 4 | The runtime has one compiler and one contract list, built at start-up | A per-tenant runtime assembly: the tenant's active connection selects the dialect adapter, the registry folder for the tenant's certified contracts, and the tenant's context. Missing any of them keeps today's fail-closed result. Contracts are loaded by tenant and a contract's `tenant_id` must equal the caller's. |
| 5 | Where drafts and certified contracts live, and who certifies | See decision 1. A draft is written as a Git change for the semantic owners, never executed until a person certifies it. |
| 6 | Sending a customer's schema to a model | Governed by the CM1 decision already recorded: **off by default, per-customer opt-in after a data processing agreement and a redaction review** (`docs/security/CONNECT-MODE-threat-model.md`). The drafter sends the model only the redacted view (names, types, comments screened, aggregate stats, at most five cohort-safe samples). A connection with the opt-in off can still be drafted by hand. |

## Stages and gates (each live step approved by the owner)

| Stage | What | Live? |
|---|---|---|
| **C0 Review** | You read this plan and decide the questions below. | no |
| **C1 Build offline** | Items 1 to 4 on fakes: the Databricks source and probe, the per-connection contract check, the per-tenant runtime assembly, tests for each failure (contract outside the ceiling, wrong tenant, wrong dialect, missing snapshot, uncertified document all fail closed). | no |
| **C2 Draft on the sandbox** | One run of the drafter on **synthetic sandbox tables** in the owner's validation workspace, through a connection made by the product, with the real model (the pilot's own Azure OpenAI deployment, as in M3/M4b), producing a draft and a numbers-only evaluation report against a hand-written reference. | yes |
| **C3 Certify** | **You** review the draft and certify (or reject) it in Git. I do not certify. | human |
| **C4 Ask a question** | A governed question over the sandbox connection end to end, through the screen or API, with the run's evidence, plus the failure cases live (suspended connection, secret rotated mid-run). | yes |

C2 and C4 may be approved together; C3 is yours and happens between them.

## What each stage proves, and what it does not

- C1 proves the plumbing fails closed on fakes. It proves nothing about Databricks behaviour.
- C2 shows whether the drafter's output is usable on Databricks metadata and a real model, on a tiny synthetic schema. It is a measurement, not a pass: thresholds from the pilot are reused unless you set others.
- C3 is the control: no definition runs until a person certifies it.
- C4 shows one question answered on one synthetic workspace. It says nothing about a customer's real tables, a second workspace, scale or an independent review.

## Decisions for the owner (C0)

1. **Who certifies, and where do definitions live?** Recommended for now: the owner certifies the sandbox draft in this repository's Git (the same path as `sales-demo@v1`). For a real customer, certified definitions would need a per-tenant home and the customer's own approvers; that is a product and legal decision I do not propose to settle here.
2. **One contract per connection** (recommended) or one contract with per-engine bindings.
3. **Model use at C2:** the pilot's Azure OpenAI deployment on synthetic sandbox data only (as in the earlier runs), yes or no.
4. **Thresholds:** reuse the approved pilot thresholds for the first Databricks measurement, or set new ones.
5. **Sandbox shape:** the synthetic tables from the earlier checks (orders, customers) are enough for C2 and C4, or you want a richer set (for example the Olist reference, which is the deferred Snowflake demo-data question).

Not verified: every behaviour of the drafter on Databricks, the cost and time of C2 and C4, the per-tenant runtime assembly, and anything about a customer's real data.

## C1 built (2026-11-18), on fakes only

- **Databricks source** (`app/ontology_draft/databricks_source.py`): facts from `DESCRIBE TABLE EXTENDED` and bounded aggregate `SELECT`s through the connection's own session, one schema per draft, the approved redaction rule reused, a fixed `DraftError` code instead of any driver message, and a `EXPLAIN` probe through the same session. Only the named tables can be read, because the principal holds nothing else.
- **A finding the build forced:** Unity Catalog tables rarely declare primary keys, and the drafter drops any metric with no key evidence (`no_key_evidence`). So a Databricks draft needs an inferred key: `infer_keys` proposes one only when the profile saw the **whole table** (fewer rows than the row limit), the column has no nulls and every value is distinct. That is a property of today's data, not a constraint, so **the reviewer must confirm the grain at certification (C3)**. A table larger than the window gets no inferred key and its metrics are dropped.
- **Dialect-aware drafter:** `DraftContext` gained `dialect` and `physical_prefix` (defaults are the pilot's, unchanged), so a draft's physical names are `catalog.schema.table` and its dry run compiles with the Databricks adapter and checks the SQL against the qualified allowlist.
- **Per-tenant assets** (`app/connect/tenant_assets.py`, `GovernedRuntime.assets_for_tenant`): for a tenant with exactly one active Databricks connection, the run uses that connection's gateway, a Databricks compiler and the tenant's **certified** contract with the context built from it. It raises (fails closed) for: no certified contract (a draft or another tenant's contract never counts), a contract using any table outside the connection's allowlist (checked on every assembly), more than one certified contract, a missing registry, a suspended connection. A tenant without a connection keeps the pilot's assets. Each connected tenant has its own circuit breaker. `app/main.py` does not pass the resolver, so nothing changes in a running deployment.
- **Tests:** 23 new tests including an end-to-end one (a fixture contract, standing in for the owner's certification, compiles for Databricks and the real gateway runs it only inside the admin's allowlist) and eleven deliberate breaks, all caught. The `certified` documents in them are test fixtures; nothing in the product certifies anything.
- **Not built:** declared-constraint key reading, joins (still single-dataset, as decided), the C2 run script and its report, and a certification workflow for a customer's own approvers.
