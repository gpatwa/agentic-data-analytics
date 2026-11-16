# Databricks, Snowflake and dbt: integration plan

Status: **Proposed. Not approved. No code written, no warehouse contacted, nothing deployed.** Written 2026-11-14 from `origin/main`.

**Update 2026-11-15.** The owner said, in chat, "go ahead and build Snowflake and Databricks". Recorded here from those words and
read narrowly: it authorizes stage **S5 for both engines, local only**. Built: `app/execution/warehouse_sessions.py` (real
Snowflake and Databricks sessions behind the existing gateways: connect with a read-only role, session settings, a cost estimate,
cancel, row streaming), `requirements-warehouses.txt` (optional drivers, not in the default image) and
`tests/test_warehouse_sessions.py` (28 tests against recorded-response fake drivers, plus two that check the installed real
drivers still expose the calls the sessions use). It does **not** authorize S6 (any live warehouse, which needs credentials and a
separate approval), the authentication-method security review (decision 3: the sessions implement Snowflake key-pair and
Databricks access token only, so adding OAuth or SSO is a separate review), or anything else in S1 to S4 and S7 to S8. Nothing
selects these gateways at runtime and no contract targets them. Still unverified on a live engine: marker syntax, the timeout
error code, cancel, and the shape of `EXPLAIN` output (cost units are MiB scanned, an engine estimate).
Extends `docs/ANALYTICS_SNOWFLAKE_DATABRICKS_SCOPE.md` (the W-packets) and ties it to `docs/AUTO_ONTOLOGY_DESIGN.md` and
`docs/AUTO_ONTOLOGY_NARRATIVE.md`. The W-packet names below are that document's placeholders, not manifest IDs.

## Position

Data engineers already live in dbt, Snowflake and Databricks, and two of the three now ship their own semantic features
(reported in vendor material, not tried hands-on: Databricks metric views and Genie Ontology; Snowflake semantic views). We do
not replace them. Compass is the governed layer above: it **reads** what they already hold (schemas, dbt descriptions and tests,
native semantic definitions), **drafts** definitions for people to certify, and **runs** only certified ones through a
deterministic compiler. The integration work is therefore mostly about reading well and writing back as pull requests.

## Where we are (checked in the repository)

| Area | State | Evidence |
|---|---|---|
| PostgreSQL, DuckDB | Live path. PostgreSQL runs on the Azure pilot. | Pilot validation of 2026-11-13 |
| Snowflake, Databricks | Compiler adapters, metadata providers, gateway **policy layer** and (2026-11-15) **real driver sessions** exist, tested against fakes only. No live run, no contract targets them, and nothing selects them at runtime. | Scope doc, W1 to W7; `app/execution/warehouse_sessions.py` |
| dbt, read | `DbtManifestProvider` reads a dbt manifest for metadata. | `app/metadata/providers.py` |
| dbt, write | ADS-053 proposes docs and test changes to approved `models/**/schema.yml` files as a reviewable diff. It never edits a file and never runs dbt. Evaluated on the reference stack only; the packet is at `review`. | `app/proposals/dbt_*.py` |
| dbt semantic layer, MetricFlow | Not verified in our code. Apache Ossie has converters to the dbt semantic layer upstream (per its project updates); ours is an import/export boundary only (ADR-ADS-008). | |
| Compiler joins and ratios | **Not supported.** The PostgreSQL compiler handles one physical dataset per query and excludes joins and ratios; `join_validation.py` checks join-path safety on intents but nothing compiles a join. | `app/compiler/postgres.py` header |

## Consequence for the auto-ontology design (needs a decision)

The design says every drafted metric must compile in a dry run and joins need key evidence. Because the compiler has no join or
ratio support, a drafted **join** cannot be dry-run, and a drafted **ratio** metric cannot compile at all. Options:
(a) milestone 1 drafts single-dataset metrics and dimensions only, and joins are emitted as `candidate` graph edges with key
evidence but flagged "not executable yet"; (b) add join support to the compiler first, which is a separate, larger packet;
(c) both, in that order. Recommendation: (a) now. It keeps the promise that nothing unvalidated looks runnable, and it matches
the claims ladder. The design doc should say this explicitly before milestone 1.

## Plan, in order

Each stage is local and fake-model or fixture based unless it says otherwise.

| Stage | Work | Needs | Done when |
|---|---|---|---|
| S0 | Decisions below | Owner | Recorded in the program file in the owner's words |
| S1 | Drafter milestones 1 and 2 on **PostgreSQL plus a dbt fixture project**: introspection through the existing `MetadataProvider` seam, redaction per the approved rule | S0 | Fixture tests pass, including PII-bait and small-cohort cases |
| S2 | **dbt as drafting evidence (read only).** Use manifest model and column descriptions, tests, and `relationships` tests. A passing `relationships` test is the strongest join evidence we can get from outside the data; `unique` and `not_null` tests support key and grain inference. Evidence is recorded in the draft's provenance | S1 | Drafts on the dbt fixture cite the tests they used; a column with a contradicting test is not drafted as a key |
| S3 | **dbt write-back as pull requests** (extend ADS-053): propose descriptions and tests for columns the drafter named, as a diff to approved `schema.yml` paths only. No dbt execution from Compass; validation commands appear as text for the reviewer | S2 | Same safety properties as ADS-053: approved paths only, no process spawning |
| S4 | **Interchange through Apache Ossie**: import dbt or Ossie models as draft input; export certified contracts. Imported models stay `draft` and never execute | S2, ADR-ADS-008 | Round-trip test on a fixture; an imported model cannot be resolved by `get_certified()` |
| S5 | **One warehouse driver, local only.** Finish the real session for the warehouse the owner picks first (connect, read-only settings, timeout, cost estimate, cancel, row streaming) behind an optional extra, and run the conformance harness against it with a recorded-response fake | S0, W1 to W7 | Conformance suite green; no driver in the default image |
| S6 | **Live sandbox validation** (W8): one metadata read, one compiled query, one cancel, one timeout, on a sandbox account | **Separate explicit approval and credentials from the owner**; a customer's production warehouse never | A recorded run, stored like the 2026-11-13 pilot validation |
| S7 | **Native semantics as draft input**: Databricks metric views and Unity Catalog metadata, Snowflake semantic views, read through the read-only role | S6 | Imported definitions land as `draft`; contradictions with certified definitions are reported, not applied |
| S8 | Drafter on the sandbox warehouse (this is milestone 4 of the design, now on a second engine) | S6, S7, redaction rule (approved) | A draft pull request from a non-Postgres warehouse, reviewed by a person |

Order rationale: dbt reading (S2) is cheap, offline and improves drafts on the stack we already run. Warehouse live work (S5 to S8)
needs accounts, credentials and security review, so it waits for approvals and does not block the narrative's first rows.

## Safety rules that do not change

- Compass never runs dbt, never edits a dbt file directly, never writes to a warehouse. Changes are proposals or pull requests.
- Imported or inferred definitions are `draft` or `candidate`. Only certified definitions execute.
- Warehouse and control-store connections stay separate. Credentials come from the environment or Key Vault only, never from
  the control store, logs, evidence or snapshots.
- The sample-redaction rule approved on 2026-11-14 applies to every warehouse and to dbt-sourced text sent to a model.
- Each engine's cost estimate fails closed: no estimate, no run.

## What we may say publicly, and when

This follows the claims ladder in the narrative plan. The landing FAQ already says PostgreSQL and DuckDB today, other
warehouses on the roadmap.

| Statement | Unlocks when | Wording rule |
|---|---|---|
| "Reads your dbt project to draft definitions" | S2 done and demonstrated on a real dbt project | Say "reads", never "works with" or "integrates with" unless approved |
| "Proposes dbt documentation and test changes as pull requests" | ADS-053 moves past `review` with real-project evidence | Already described in pilot terms in the FAQ |
| "Runs on Snowflake" or "on Databricks" | S6 recorded for that engine | One sentence, with "sandbox validated" until a customer runs it |
| Native-semantics import | S7 done | Name the vendor feature only as a source of input |

Do not list Snowflake or Databricks as supported, or show their logos, before S6 is recorded for that engine. Use of vendor
names follows the reference rules in the narrative plan.

## Decisions for the owner (none recorded)

1. **Which warehouse first, Snowflake or Databricks?** Is a design partner or customer driving it? (Recommendation: whichever the
   first design partner uses; if none, Databricks, because its Genie Ontology is the closest market pattern and a reading
   source for S7. This is a judgment, not a finding.)
2. **Sandbox accounts and credentials for S6:** do you have them, and will you supply them yourself?
3. **Authentication methods** to support and their security review (key-pair or OAuth for Snowflake; token or OAuth for
   Databricks). A security gate.
4. **Authorize S1 to S5 as local-only work now**, with S6 as a later, separate approval.
5. **dbt scope:** read only (S2), plus write-back proposals (S3), plus Ossie interchange (S4)? Recommendation: S2 first.
6. **Joins:** DECIDED 2026-11-14, option (a), single-dataset now (owner's answer: "(a) single-dataset now"). Compiler joins and ratios are a separate, later packet.

## Not verified

Everything about Snowflake and Databricks internals comes from `docs/ANALYTICS_SNOWFLAKE_DATABRICKS_SCOPE.md` section 2 and
vendor material, not from running anything. Not checked: driver versions, Databricks parameter syntax, read-only-role behavior,
cost-estimate APIs, cancellation under each backend, trial or free-tier terms, and whether our dbt manifest provider reads dbt
semantic models. S2 should begin by checking that last one.
