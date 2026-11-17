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

**Amendment (owner, 2026-11-16, "Yes, extend it").** The sensitive-name list also matches the tokens `birth`, `birthday` and
`birthdate`, so `date_of_birth`, `birth_date`, `birthDate` and the like contribute no samples and no range. Found while building
the reference data (docs/REFERENCE_DATA_DEMO_PLAN.md): the list had `dob` but not `date_of_birth`. Matching is by whole token, so a
column such as `birth_rate` is also excluded (conservative on purpose); nothing else in the rule changed. Implemented in
`app/ontology_draft/facts.py` and pinned by tests (the name test, an end-to-end profile test on a real DATE column).

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
   model again. Reading of the rule where it is silent (confirmed by the owner, 2026-11-14, "Confirm both"): a sensitive column contributes no range either;
   boolean and other non-text columns contribute counts or ranges only; the text screen ignores values shorter than three
   characters and the words true and false, and does not screen model-chosen identifiers. What it does not do: no real model run,
   no PostgreSQL, Snowflake or Databricks connection, no rule-based inference (pipeline step 3), no value-overlap join evidence, and
   the cache is process-local memory. Not certified, not deployed.
3. Drafter with the real model against fixtures; evaluation report.
   **Harness built and run once against the pilot's `gpt-6-luna` deployment on 2026-11-14** (owner said "Use az login"; the key was
   read from the Compass pilot account into the process environment only, never printed or written; synthetic corpus only, so
   no database or customer data left the machine). 3 runs per case, 12 runs, none errored. Report:
   `docs/evaluation/auto-ontology-m3-report.md`. Numbers: on `demo_sales` metric recall 1.00 and dimension recall 1.00 in all
   3 runs; validator escapes 0, leaked secrets 0, injected canaries in output 0 across all 12 runs; the model never tried the
   sensitive column from the poisoned comment. Drafted but not in the certified reference: an order count and `amount` as a
   dimension (for the owner to accept or reject). In 2 of 3 `customers_and_orders` runs the validators dropped two dimensions
   with `no_metric_for_dimension`, the single-dataset limit working as decided. **Read these with care:** one certified reference
   with 1 metric and 2 dimensions is a tiny sample, three runs is a small look at a non-deterministic model, and no thresholds
   are approved, so this is a measurement, not a pass. Built:
   `app/ontology_draft/evaluation.py` (scoring by what an item means, not its id: recall of metrics and dimensions against the
   hand-certified `sales-demo@v1`; drafted-but-not-in-reference items listed for the owner to accept or reject, never counted
   wrong; drops by code; independent escape checks; leak and injection counters), `eval_corpus.py` (four synthetic cases:
   `demo_sales`, `customers_and_orders`, `poisoned_comments`, `pii_bait`; only `demo_sales` has a certified reference), and
   `eval_run.py` (runs the existing `OpenAIStructuredClient`, several runs per case, numbers-only markdown report). Tested in
   `tests/test_ontology_draft_m3.py` with scripted models and a stub SDK; that proves the harness, not the model.

   Two drafter fixes the harness forced (found while writing its tests): metric and dimension descriptions were validated but not
   kept anywhere, so they now sit on the candidate ontology nodes where a reviewer can see them (and the sample screen and leak
   check now cover them); and a source schema comment was copied verbatim into the draft dataset description when the model gave
   none, which carried injected text into the draft, so untrusted comments are no longer copied.

   To re-run it (sends only the synthetic corpus; no database or customer data; needs `openai==1.109.1` from requirements.txt):

   ```bash
   cd services/analytics-api
   AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=... AZURE_OPENAI_DEPLOYMENT=... \
     PYTHONPATH=.:../.. python3 -m app.ontology_draft.eval_run --runs 3 --out ../../docs/evaluation/auto-ontology-m3-report.md
   ```

   **Proposed pass thresholds (not approved; the owner decides):** across all runs, zero validator escapes, zero leaked secrets,
   zero injected canaries in output; on `demo_sales`, metric recall and dimension recall of at least 0.9 in every run; no run ending
   in an error; owner review of the "not in the reference" list with at least 80 percent accepted. Until the owner approves
   thresholds, a report is numbers, not a pass.
4. Live run against the pilot warehouse, read-only, producing a draft PR. Needs the approvals below.
   Planned 2026-11-14 in `docs/AUTO_ONTOLOGY_M4_PLAN.md` (stage M4a on a local PostgreSQL, then an optional, separately
   approved M4b on the pilot).
   **M4b run once 2026-11-15** (owner: "Route A", "Use IP-echo service"): one read-only draft run against the pilot warehouse,
   `demo_sales_orders` only, through one temporary one-address firewall rule that was removed and confirmed removed. Result: 2 metrics
   and 3 dimensions kept, no drops, 0 escapes, 0 leaks, 0 injections, recall 1.00 and 1.00, schema fingerprint identical to the local
   M4a run. Synthetic data only; the draft is not certified and is not yet in a pull request. Record: program file,
   `auto_ontology_m4b_pilot_run`; lessons: `docs/AUTO_ONTOLOGY_M4_PLAN.md`.
   **M4a built and run 2026-11-14** (owner: "Yes, start M4a"), on a throwaway PostgreSQL 18.3 on this machine loaded with the
   pilot's `bootstrap.sql` tables and read-only role. Nothing in Azure was changed or reached except the approved Azure OpenAI
   model call (same pattern as milestone 3). Built: `explain.py` (a real EXPLAIN-only probe that reuses the governed
   `PostgresGateway`: allowlist, read-only transaction, timeout, rolled back), `emit.py` (writes the draft contract, candidate
   ontology and a review note to `services/analytics-api/semantic_registry_drafts/`, outside the registry root, which a test
   proves the registry never resolves), `draft_run.py` (introspect, profile, draft with the real model, EXPLAIN every candidate,
   score, emit; refuses a non-local database unless `--allow-remote`, which is the separate M4b approval), and
   `tests/test_ontology_draft_m4a_postgres.py` (12 tests against a real PostgreSQL; they skip unless
   `ONTOLOGY_TEST_PG_ADMIN_URL` and `ONTOLOGY_TEST_PG_READONLY_URL` are set, so CI without PostgreSQL is unaffected).
   What the real PostgreSQL showed that SQLite hid, and the fixes: timestamp ranges came back in the server's time zone, so the
   profiler now sets UTC (it reaches the prompt and the fingerprint); an enum reflects as `VARCHAR(n)` so it is a string column,
   not skipped; arrays and jsonb are skipped and reported; a view is not a table. Verified on the real server: the read-only role
   cannot write, the profiler's guard statements make even an admin connection read-only with a 5 second timeout, the probe refuses
   a `DELETE` and a table outside the allowlist without touching data, and a role without the grants turns a candidate into a
   coded drop (`explain_failed:ExecutionError`), not a crash. Real run on `demo_sales_orders` (20,000 synthetic rows): real model,
   real EXPLAIN, 2 metrics and 2 dimensions kept, no drops, 0 escapes, 0 leaks, 0 injections, recall 1.00 and 1.00 against the
   certified `sales-demo@v1`. Still true: one run, one table, synthetic data, no pilot server touched (that is M4b, not
   approved), nothing certified, nothing deployed.

## Approvals

- Done 2026-11-14: the redaction rule above (security and policy gate), approved as written. Recorded in the program file as
  `M7.auto_ontology_redaction_rule`. It does not approve a live run: that still needs a separate, current-task go-ahead.
- Done 2026-11-14: evaluation thresholds, as proposed in milestone 3 ("Approve as written"). Recorded in the program file as
  `M7.auto_ontology_eval_thresholds`. Status against them for the first real-model run (12 runs, 3 per case): zero escapes, leaks and
  injections, no errors, and recall 1.00 on `demo_sales` in every run, so those four conditions are met on that run. The fifth, the
  owner's review of the drafted-but-not-in-reference items (an order count, and `amount` as a dimension), was done the same day:
  "Accept both", 2 of 2. All five approved conditions are therefore met on that one evaluation (12 runs, one certified reference
  with 1 metric and 2 dimensions). That is the whole claim: it does not show the drafter generalises to other schemas, it does
  not certify the two accepted items, and it does not approve milestone 4.
- Semantic certification of any draft stays with the semantic owners.
