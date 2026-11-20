# Semantic trust foundations: a design note and plan (2026-11-19)

Status: **E1 to E5 approved by the owner on 2026-11-19 ("do all 1-6": recommended answers to decisions 1 to 5, offline builds only; program file `semantic_trust_e1_e5`).** Every stage is offline (fakes, fixtures, no warehouse, no model call, no download). Approval is requested in "Decisions for the owner". It follows the review of a published case study (below) and ties into `REALISTIC_DATA_SOURCE_REVIEW.md` (declared keys) and `INTENT_COVERAGE_CHECK_PLAN.md` (refusals).

## 1. What the case study is, and what we take from it

"Self-service agentic analytics at Mews, Part I" (Substack, 2026) describes a Databricks, Unity Catalog and dbt shop where an agent writes SQL against governed sources. It is one team's account of about a month of use; it gives no cost, latency or error rates, and its 25-question comparison is small. We treat it as a design signal, not as evidence.

The trust models differ, and that decides what transfers:

| | Mews | This product |
|---|---|---|
| Who writes SQL | the agent, freely | a deterministic compiler, from a typed intent |
| Certification | advisory: the agent may use uncertified tables and says so (✅ / ⚠️) | a gate: no certified contract, no answer |
| Disagreement between answers | observed (two ad-spend answers 12% apart) | cannot occur inside one certified contract |
| Identity | each user's own OAuth token; the warehouse enforces grants, masks and row filters | one read-only principal per connection, limited to named tables (the "ceiling"); per-user rules are ours |
| Source readiness | tables certified (schema contract, owner, not-null and unique tests on keys, production only) | semantic definition certified; **table readiness is not** |
| Evaluation | per-guide eval file in CI; expected number from a live query; branch-versus-main score diff | pinned numbers, offline oracles, mutation checks, a coverage hold-out; no per-change diff |
| Learning from misses | traces feed a backlog of guides to write | refusals are counted by code only |

Where the design is already stronger (determinism, a hard gate, synonym-clash rejection, named refusals) we keep it. Three gaps are worth closing now. Three more ideas are deferred (section 8).

## 2. Stage E1: key evidence as a first-class fact

**Problem.** A metric's grain must rest on a key. Today `infer_keys` (`app/ontology_draft/databricks_source.py`) accepts a column only when the profile saw the whole table, which means fewer than 100,000 rows. `introspect_session` reads no keys at all. The realistic datasets (about 1.07 million, tens of millions and 23 million rows) all exceed the window, so they would get no metrics. Today the evidence is an unlabelled `primary_key` tuple, so the reviewer cannot tell a declared key from a lucky profile.

**Design.**
- Add `key_evidence` to `FactTable`, one of `declared`, `profiled_full`, `none` (default `none`; `tested` is reserved for a later dbt-test importer, section 8). It is a derived fact about the source, **not** part of the certified contract, so contract digests do not change.
- Include it in `SchemaFacts.fingerprint()` so a change in evidence is a change in facts.
- `validate.py` keeps `no_key_evidence` as the hard rule (no key, no metric). It does not add a new gate on the level; the level is shown, not enforced, so existing behaviour is unchanged.
- `emit.py` adds one line per table to the review summary the owner reads at certification: the key, its evidence level, and what that level does and does not prove.

**What each level proves.** Warehouse primary keys on Snowflake and Databricks are informational: they are not enforced. A `declared` key is the owner's or the pipeline's claim, which can be wrong. A `profiled_full` key is a fact about today's rows, not a constraint. The summary says so in plain words. The reviewer, not the code, decides.

**Optional exact check (explicit, costed).** For a `declared` key on a large table, a full-scan `count(*)` versus `count(distinct key)` is the only exact test. It is a live read, so it is never automatic: it is a separate, opt-in step, bounded by the existing `EXPLAIN` cost check and run through the connection's read-only session, and it is out of scope for this stage's build. E1 builds only the evidence label and the summary.

## 3. Stage E2: read declared keys on both engines

- **Snowflake:** the SQLAlchemy-based introspection already reads primary keys (`introspect.py` takes `details["pk"]`). E2 sets `key_evidence="declared"` there, with a test.
- **Databricks:** `introspect_session` extends to read primary-key constraints from the catalog's `information_schema` (constraint and key-column views), restricted to the tables inside the ceiling, in the same `SELECT`-only session. The exact view and column names are **not yet verified against a live workspace**; E2 builds against a fake that mirrors the documented shape, and the first live read is a separate approval (section 9, L1).
- A key that references an unknown or excluded column is dropped with a reason code, never guessed.
- Order of precedence per table: `declared`, else `profiled_full`, else `none`.
- Loader rule for the realistic datasets (feeds the offline build already recommended): every loaded table gets a surrogate `row_id` and a declared primary key on it, and the loader records that choice.

**Tests (all offline).** A fake catalog returning declared, composite, missing and out-of-ceiling keys; a table over the profile window gets metrics only with a declared key; two mutation checks (drop the evidence label; let a profiled key claim `declared`).

## 4. Stage E3: a backlog of what people asked and we could not answer

**Problem.** Mews found that coverage gaps, not model randomness, cause most wrong or missing answers, and that their traces feed a backlog. Our C5 run showed the same. We count refusals by code but cannot say which missing term or metric would convert the most refusals into answers.

**The constraint that shapes the design.** The coverage check deliberately never writes a word to a log or to evidence (`coverage_reference` holds positions and a hash). A backlog of "words people used" appears to contradict that rule. It does not have to:

1. **Default (no raw text kept):** record, per refusal, the refusal code and the *hash of each unaccounted stem*, salted per tenant. Aggregate counts by hash.
2. **Resolve hashes against a candidate dictionary, not against stored text.** The owner-side report hashes a dictionary of plausible terms (the contract's own column names and glossary, the drafter's candidate synonyms, the neutral list and its near-additions) with the same salt, and shows a ranked list of terms that match. Words outside the dictionary stay as opaque hashes with counts. Nothing is ever reversed from storage.
3. **Opt-in per tenant (a decision for the owner):** store the stems themselves with a short retention (proposed 30 days) so the long tail becomes readable. Default off.

**Output.** A ranked report: "term X would have affected N refused questions; candidate action: add as a synonym of metric M (certified by a person), or the question needs a metric that does not exist". It feeds **S4 (certifying synonyms)** as a proposal list. It never certifies anything and never edits a contract.

**Boundaries.** The report is a read model over what we already log; it adds one counter table in the control database (a migration) and no change to the answer path. The first release does not need the opt-in storage.

## 5. Stage E4: what did this contract change do to answers?

**Problem.** A certified contract is edited (a synonym added, a grain changed, a definition reworded). Today nothing tells the certifier what that edit does to the questions people ask. Mews shows reviewers a branch-versus-main score for the same reason.

**Design: two diffs, both deterministic, both offline.**

- **Contract diff.** A canonical, readable diff of two certified versions: metrics and dimensions added, removed or changed (aggregation, measure, grain, filters), synonyms added or removed, and the digest of each. It renders into the certification summary for a new version.
- **Answer-behaviour diff.** Each contract has an eval file of cases: the question, the **recorded typed intent** the interpreter produced (pinned from an approved run), and the expected outcome (`answered` with the metric id, or `refused` with a code). Re-running the *deterministic* part (echo check, coverage check, compiler, policy) against the old and the new contract, with the recorded intent, reports every case whose verdict or compiled-SQL hash changed. **No model is called**, so it is repeatable and free. A separate, approved, live run can regenerate intents with a real model; that is not part of this stage.

**What it catches.** A new synonym that turns a previous refusal into a wrong answer; a grain change that silently changes a compiled query; a removed term that now refuses a once-valid question.
**What it does not catch.** A change in the model's behaviour (that needs the live run), and a wrong business definition (section 6).

**Eval file** (`<registry>/<contract>.eval.json`, one per contract): cases, each with `question`, `recorded_intent`, `expect`, and an optional `oracle` (section 6). Required for certifying a *new version* of a contract that already has an eval file; not required for a first draft. The requirement is a proposal (decision D4).

## 6. Stage E5: a live-oracle mode for Connect

**Problem.** Pinned expected numbers suit regression tests on our own fixtures. A customer's data changes, so a pinned number goes stale. Mews computes the expected value from a live query for this reason.

**Design.**
- A case may carry `oracle`: an independent SQL statement, authored by the contract owner, that returns the number the question should produce, and a tolerance (absolute or relative).
- Run through the **same** read-only, ceiling-limited, `EXPLAIN`-bounded session as everything else; it must pass the same table allow-list and may not touch anything outside the ceiling.
- The runner compares the product's answer with the oracle and reports `match`, `mismatch` (a problem, never hidden) or `oracle_failed`. The answer is the product's real path, not a re-implementation.
- Result is evidence in the existing chain; no value is copied to a log beyond what the existing evidence already records.

**What it proves, stated honestly.** It checks that the compiler, the definition as written and the data agree on a number, and it detects drift. It shares the engine and the data with the product, so it cannot catch a metric whose agreed *business* meaning is wrong: that is the human certification gate. Passing it is never described as "certified".

**Scope of this stage.** Build the oracle runner and its report against fakes. Running it on any real warehouse is a separate approval (L2), and a *recurring* run is a separate, later decision because each run is a live read with cost.

## 7. Plan

| Stage | What | Touches | Done when |
|---|---|---|---|
| **E1** | `key_evidence` on facts; fingerprint; review summary line | `facts.py`, `databricks_source.py`, `introspect.py`, `emit.py` | new tests pass; existing digests unchanged; two mutation checks fail as expected |
| **E2** | declared-key reading on Databricks (fake catalog) and the Snowflake label | `databricks_source.py`, `introspect.py` | tests cover declared, composite, missing, out-of-ceiling; a large table gets metrics only with a declared key |
| **E3** | salted stem-hash counters, dictionary resolver, ranked report | new module under `app/runtime`, one migration, report generator | report generated from fixtures; no word reaches a log (a test scans for it); resolver never reverses a hash |
| **E4** | contract diff; recorded-intent eval file; answer-behaviour diff | new `app/contracts_diff` module, eval-file schema, generator | a fixture edit that adds a bad synonym shows up in the diff; two mutation checks |
| **E5** | oracle spec, runner, report | extends the connect e2e runner pattern | fake-session tests: match, mismatch, oracle failed, oracle outside the ceiling refused |
| **E6** | start from existing warehouse assets (section 8A) | importers for metric views and semantic views | proposed, not approved |

Order: E1, E2, then E3 and E4 in either order, then E5. Each is one pull request with its own tests, ruff-clean, with the usual pinned report where there is one. A generated report is kept equal to its generator by a test, as for the earlier evaluations.

## 8A. Stage E6: start from what the customer already has (adoption)

Owner direction, 2026-11-19: "leverage what already exists in Databricks or Snowflake, make it easy for adoption." The principle: **a customer should never re-model what their warehouse already says.** Connect reads what exists, turns the usable part into a draft, shows what it found and what it could not use, and a person certifies. Nothing imported is certified, and nothing is written back.

### What exists, per engine, and what it gives us

| Asset | Databricks | Snowflake | Use |
|---|---|---|---|
| Table and column comments | Unity Catalog comments (already read by `DESCRIBE`) | comments (already read) | descriptions, as untrusted text through the existing cleaning |
| Primary keys | informational constraints; `information_schema` views are documented and privilege-filtered | declared keys (already read); informational | grain keys (E1, E2) |
| Metric definitions | **metric views**: YAML with a source, dimensions (called fields in newer docs), measures, an optional filter and comments; spec versions 0.1 and 1.1; documented as Public Preview | **semantic views**: logical tables, facts, dimensions, metrics, synonyms and comments; `DESCRIBE SEMANTIC VIEW` | the best input: someone already decided the definitions |
| Synonyms | none native in the metric view YAML as found | yes, per dimension and metric | proposed synonyms, certified by a person like any other |
| Tags and classifications | governed tags | object tags, system classification tags | can only make a column *more* restricted, never less |
| Masks and row filters | column masks, row filters | masking and row access policies | read for awareness and to warn; not enforced by us (see section 8) |

Format details above come from the vendors' public documentation as surfaced by search; the exact statements to list them and the exact preview status are **not yet verified against a workspace or the full reference pages**, so E6a below verifies first.

### Import rules (conservative, deterministic, no model needed)

- Only what fits the product's scope maps: **one source table, a sum or a count, a simple filter, no joins, no ratios, no windows.** Anything else is listed under "not imported, and why", never approximated.
- An imported item is a **candidate** with its source object named and a hash of its definition. The same certification gate applies.
- Comments and descriptions are untrusted text and pass through the existing cleaning and injection screen.
- Re-importing later produces a **contract diff (E4)**, so an upstream change becomes a reviewable change, not silent drift.
- Tags only tighten: an upstream sensitivity tag adds a restriction; no upstream tag can remove one.

### Onboarding the owner sees

"Found: 3 metric views, 41 commented columns, 12 declared keys, 2 sensitivity tags. Drafted: 5 metrics, 9 dimensions. Not imported: 2 (a ratio; a join). Review and certify." The adoption measures are time to the first certified answer and the share of draft items that came from existing assets.

### Stages (all offline; none approved yet)

| Stage | What |
|---|---|
| **E6a** | read the full vendor references; record verified fixtures (a Databricks metric view YAML at each spec version; a Snowflake `DESCRIBE SEMANTIC VIEW` result); write down what could not be verified |
| **E6b** | Databricks metric view YAML to draft candidates, with the not-imported list |
| **E6c** | Snowflake semantic view description to draft candidates, with synonyms as proposals |
| **E6d** | tags to restricted-column hints (tighten only) |
| **E6e** | the "what we found" summary in the review note |

A live read of a customer's metric views or tags is its own approval (L4). E6 needs the owner's go-ahead before E6a starts; it follows E5.

## 8. Deferred on purpose

- **Contract size versus accuracy and cost.** I do not know how our interpreter sees a tenant's contract. If it sees all of it, a few hundred metrics may hurt accuracy and cost. First step is a measurement plan (a note, not a build); retrieval only if it shows a problem.
- **Importing an existing semantic layer** moved up: see section 8A (stage E6), at the owner's request on 2026-11-19.
- **Per-user identity pass-through** (the user's own token, so warehouse row filters and masks apply). A customer with existing row-level security will ask. It adds token custody and audit burden, so it needs its own design note and an owner decision; it would be an option on top of the ceiling, never a replacement.
- **Writing certification tags back to the warehouse.** Conflicts with the read-only principle; would need a separate optional privilege. Not now.

## 9. What stays with the owner

Human gates are unchanged: semantic certification, security, policy and thresholds, and the final go/no-go. Nothing here certifies a contract. Live steps, each needing its own approval stating what will be read:
- **L1:** read declared keys on a real Databricks or Snowflake workspace (catalog metadata only).
- **L2:** run the oracle mode on a real warehouse.
- **L3:** any recurring oracle run.
- **L4:** read a customer's existing metric views, semantic views or tags (section 8A).
- Opt-in raw-stem retention for a tenant (E3, option 3) is a privacy decision.

## Decisions for the owner

1. **Approve the offline builds E1 to E5?** (Or a subset; E1 and E2 are the smallest and unblock the realistic-data work.)
2. **D2, E3 default:** hash-only counters resolved against a candidate dictionary, with raw-stem retention off and available as a per-tenant opt-in. Agree?
3. **D3, E4:** recorded intents are pinned from an approved run, and the diff never calls a model. Agree?
4. **D4:** require an eval file when certifying a *new version* of a contract that has one (not for a first draft)?
5. **Order:** E1 and E2 first, as recommended?
6. Separate and still open: the Online Retail II download (about 43.5 MB) and the question of who asks NYC about hosting a reference copy.
