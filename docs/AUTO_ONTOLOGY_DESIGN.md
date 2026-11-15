# Auto ontology drafter: design

Status: **Proposed. Design not approved as a whole, not built.** Written 2026-11-13 after the owner decided the ontology capability must exist.
One approval is recorded (2026-11-14): the sample-redaction rule, as written. The remaining approvals are listed at the end.

## Why

Market research (see `docs/LANDING_MESSAGING_REVIEW.md`, update of 2026-11-13) shows AI-drafted context with human certification
is becoming standard (NVIDIA Auto Ontology, Microsoft Fabric, Databricks Genie Ontology, WisdomAI). Compass has the control side
(certified definitions in Git, deterministic compiler, review, evidence) but its definitions are authored by hand. The drafter
closes that gap **without weakening the certification gate**: AI drafts, a person certifies, only certified definitions execute.

## Non-goals

- No auto-certification and no auto-merge of a draft. A draft can never be resolved for execution (`get_certified()` only).
- The model never writes SQL. It never sees credentials. It does not run queries.
- No learning from usage at answer time. Feedback-derived proposals stay in ADS-051 to ADS-053.

## What exists to build on

- `packages/platform_contracts/semantic.py`: `SemanticContract` / `SemanticRegistryDocument` (lifecycle `draft|certified|deprecated`).
- `packages/platform_contracts/ontology.py`: `OntologyNode` / `OntologyEdge` with lifecycle `candidate|certified|deprecated` and provenance.
- `app/semantic_registry`: Git-backed, read-only registry; only `get_certified()` executes.
- `app/metadata`: information-schema providers (PostgreSQL pilot via the read-only role, Snowflake and Databricks scaffolds).
- ADR-ADS-008: Apache Ossie import/export boundary (an Ossie model never executes by itself).
- `app/proposals`: the shared `ChangeProposal` contract, always `status: proposed`.

## Pipeline

1. **Introspect** (deterministic): tables, columns, types, primary and foreign keys, comments, from the information schema.
2. **Profile** (deterministic, read-only role, statement timeout, row limit): null and distinct counts, numeric and date ranges, and
   redacted samples under the rule below.
3. **Infer by rules** (deterministic): dataset grain, entity keys, measure versus dimension roles, candidate joins from key
   names and value overlap, time dimensions.
4. **Draft by LLM** (structured output only): business names and descriptions, glossary terms, and candidate metrics and joins.
   Output must parse into the existing contract types; free text is never executed.
5. **Validate** (deterministic): `SemanticContract` cross-reference validation; every metric must compile through the real
   compiler against the real schema in a dry run with `EXPLAIN` only; joins must be supported by key evidence from step 3;
   anything failing is dropped with a recorded reason, not repaired by the model.
6. **Emit**: a `draft` `SemanticRegistryDocument` and `candidate` ontology nodes carrying provenance (source, fingerprint,
   model and prompt versions), written as a Git change (pull request) for the semantic owners.
7. **Certify (human)**: owners edit and certify in Git. Only after that does the registry resolve it. Re-runs produce a diff
   against the certified version, never an overwrite.

## Known limit: joins and ratios (added 2026-11-14)

The PostgreSQL compiler supports one physical dataset per query and excludes joins and ratios (`app/compiler/postgres.py`).
So a drafted join cannot be dry-run compiled, and a drafted ratio metric cannot compile. Until the compiler supports them,
milestone 1 drafts single-dataset metrics and dimensions only, and drafted joins are emitted as `candidate` edges with key
evidence, marked not executable. See `docs/WAREHOUSE_AND_DBT_PLAN.md` for the options.

**Decided 2026-11-14:** the owner chose option (a), single-dataset now (answer "(a) single-dataset now"). Compiler join and ratio support
is a separate, later packet and is not part of milestones 1 to 4. This is a scope decision recorded here; it is not one of the
human gates in the program file and approves no live run.

## Safety rules

- Read-only database role only; no write path exists in the drafter.
- The LLM call goes to the tenant's own Azure OpenAI deployment (the one the pilot API already uses); no other egress.
- Samples are never persisted or logged. The draft records only fingerprints and aggregate stats.
- Every LLM-proposed metric or join is a *candidate* and is labelled as such, with the evidence that supports it.
- Prompt-injection hygiene: column names, comments and sample values are data, passed as quoted fields; the output schema is
  closed, so injected text cannot add operations.

## Redaction rule for samples (approved as written by the owner, 2026-11-14)

A column contributes samples only if all hold: its name and comment do not match a sensitive list (email, phone, name, address,
ssn, tax, dob, ip, token, password, secret, key, card, account, iban); its type is not free text beyond 40 characters; and it is
low-cardinality (at most 50 distinct values). Then: at most 5 values per column; each value must occur in at least `min_cohort`
rows (the pilot's cohort floor is 10); values are truncated to 40 characters; values matching email, phone, card, UUID or long
digit patterns are replaced by a type placeholder. Numeric and date columns contribute ranges, not values.

## Evaluation (how we will know it works)

- **Held-out check:** run the drafter on the `demo_sales_orders` schema and compare its draft to the hand-certified
  `sales-demo@v1` contract: recall of the metric, dimensions and grain; precision (nothing the owner would reject); zero
  validator escapes. Report numbers, thresholds proposed for the owner's approval.
- **Adversarial:** a poisoned-comment corpus and a PII-bait schema must yield no leaked sample and no injected operation.
- **Reviewer effort:** time-to-certify a draft versus authoring by hand, measured with the owner.

## Milestones

1. Contract and validators (draft output types, dry-run compile check), on fixture schemas with a fake model.
   **Built 2026-11-14** in `services/analytics-api/app/ontology_draft/`, tested in `tests/test_ontology_draft_m1.py` (50 tests, no
   database, no network, fake model only). What it does: a typed proposal shape that cannot carry SQL; per-item parsing so one bad
   item cannot sink the rest; coded drops for unknown tables or columns, type mismatches, no key evidence, sensitive columns,
   links or markup in text, duplicate ids and failed compiles; every metric and dimension compiled through the real PostgreSQL
   compiler and checked by the real read-only SQL validator; an injectable EXPLAIN probe; a `draft` registry document with
   `candidate` metrics and `candidate` ontology nodes carrying provenance; drafted joins returned as non-executable candidates.
   What it does not do: it has not run with a real model, there is no EXPLAIN against a real database (the probe is injected
   and only faked in tests), there is no sampling or profiling (milestone 2), and composite-primary-key tables are handled by
   matching the full key only. Not certified, not deployed.
2. Introspection, profiling and redaction, with tests (PII-bait, small-cohort, free-text cases).
   **Built 2026-11-14** in `app/ontology_draft/` (`introspect.py`, `profile.py`, changes to `drafter.py` and `validate.py`), tested in
   `tests/test_ontology_draft_m2.py` against a local in-memory SQLite database and a fake model. What it does: reads tables,
   columns, types, primary and foreign keys and comments through the SQLAlchemy inspector (no row values); profiles with
   `SELECT` statements built by SQLAlchemy Core under a row limit (and, for PostgreSQL, a read-only transaction and a statement
   timeout, which are emitted but have not been run against a real PostgreSQL); applies the approved redaction rule exactly
   (sensitive names and comments, 50-distinct cap, 40-character free-text cutoff, cohort floor of 10, 5 values, placeholders for
   email, UUID, card, phone and long numbers, ranges only for numeric and date columns); keeps samples out of serialisation,
   `repr`, logs and the provenance (which records only an aggregate-stats fingerprint); sends the model only the redacted view;
   drops any drafted description, display name or glossary text that repeats a kept sample value (`sample_value_in_text`); caches a
   finished draft by a digest of the exact prompt, model and prompt version, so an unchanged schema and profile do not call the
   model again. Reading of the rule where it is silent (flagged for the owner): a sensitive column contributes no range either;
   boolean and other non-text columns contribute counts or ranges only; the text screen ignores values shorter than three
   characters and the words true and false, and does not screen model-chosen identifiers. What it does not do: no real model run,
   no PostgreSQL, Snowflake or Databricks connection, no rule-based inference (pipeline step 3), no value-overlap join evidence, and
   the cache is process-local memory. Not certified, not deployed.
3. Drafter with the real model against fixtures; evaluation report.
4. Live run against the pilot warehouse, read-only, producing a draft PR. Needs the approvals below.

## Approvals

- Done 2026-11-14: the redaction rule above (security and policy gate), approved as written. Recorded in the program file as
  `M7.auto_ontology_redaction_rule`. It does not approve a live run: that still needs a separate, current-task go-ahead.
- Evaluation thresholds, before milestone 3 results are called a pass.
- Semantic certification of any draft stays with the semantic owners.
