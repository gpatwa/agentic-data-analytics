# Certified synonyms: a design note (2026-11-18)

Status: **S0 was approved by the owner on 2026-11-19 with the recommended answers (program file `semantic_synonyms_s0`). S1, S2 and S3 are built offline (see "S1 to S3 built" at the end); S4 (certification), S5 (live re-check) and S6 (build) are not approved.** It includes S6 (confirm instead of refuse), added at the owner's request. Any change to a certified definition, including a synonym, stays the owner's explicit act (CLAUDE.md: semantic certification is a human gate).

## Why now

Two findings point at the same gap.

1. **C5 (`CONNECT_C5_RESULTS.md`).** On the certified sandbox contract, "How many orders are there?" and "What is the total order amount?" were refused as `unsupported_request` before any SQL existed, whatever the model returned. The intent-echo check (`app/runtime/intent_echo.py`) accepts a metric only if the question names it by one of its **certified terms**, and a drafted contract's only terms are its ids (`order count`, `order amount sum`).
2. **Market baseline.** ThoughtSpot's Claude announcement lists synonyms as a core part of its governed semantic model (with certified metrics, joins, definitions and row-level security). Business users do not say `order_amount_sum`. A governed layer that only understands its own ids will refuse most real questions, and a refusal is the right result only when the question is truly outside the contract.

## What exists today (verified in the code)

- The echo check, the alias resolver in the ontology node and the context search index already read `attributes["aliases"]` and `attributes["synonyms"]` of **certified ontology nodes**.
- **Nothing fills them for a registry contract.** `context_from_contract` builds each node with `label = display_name or id` and no attributes, and `SemanticMetric`, `SemanticDimension` and `SemanticField` have no `display_name` or `synonyms` field (only datasets and owners have `display_name`). So a certified contract currently has no place to say "revenue is also called sales".
- The drafter proposes a `glossary` of terms with definitions, but they become candidate `glossary_term` ontology nodes in the draft, not part of the contract the runtime reads.
- `certified_vocabulary` (what the model is shown) carries ids and kinds only, no business words.

So the runtime is ready for synonyms; the **contract schema and its two producers (the certified document and the drafter) are not.**

## Design

### 1. Where synonyms live: in the certified contract, next to the definition they name

Add optional fields, all defaulting to empty so every existing certified document stays valid:

| Object | New optional fields |
|---|---|
| metric, dimension, dataset | `display_name` (metrics and dimensions only; datasets have it), `synonyms: list[str]` |
| field | `synonyms: list[str]` (used by filters, for example "refunded" naming the status field) |

Reasons: a synonym changes what a question means, so it must be certified, versioned and reviewed exactly like the definition (one Git change, one certification), not live in a side file or a database an admin can edit. `context_from_contract` then copies `display_name` into the node label and `synonyms` into `attributes["synonyms"]`; the echo check, the resolver, the search index and the vocabulary all work without further change.

**Compatibility.** Additive and optional; the contract version constant stays. One side effect to handle deliberately in S1: `context_from_contract` fingerprints `document.model_dump_json()`, so adding fields changes the snapshot id of every existing contract once. S1 lists every pinned digest or snapshot id that moves (tests, corpora) and updates each on purpose.

### 2. Rules a certified synonym must pass (a validator in the contract, so a bad one cannot be certified)

- **Shape:** at most 10 per object, 2 to 60 characters, letters, digits, spaces and hyphens only, at least one letter. No punctuation, quotes, braces or sentence-like text, because synonyms are shown to the model.
- **Normalised the same way the runtime matches** (lower case, light plural fold). One shared function is used by the validator, the echo check and the resolver, so "what was certified" and "what matches" cannot drift.
- **Unique within a kind:** a normalised synonym may belong to only one metric (or dimension, or field). A clash is rejected at certification, not discovered as `ambiguous_semantic_alias` at run time.
- **No term may be a whole-word part of another metric's term** (for example `order` for a count metric when `order amount sum` exists). Otherwise one question names two metrics and the echo check, which only asks "is the chosen metric named?", would pass the wrong one. This protects the echo check's precision.
- **Not equal to a certified id of another kind** and not a reserved word the echo check treats specially (time words, `by`, `per`, `each`).

### 3. What the model sees

`certified_vocabulary` gains the display name and synonyms next to each id, so the model maps "how many orders" to `order_count` instead of guessing. The echo check stays the independent, lexical backstop: the model proposes, the certified words decide. Synonyms are human-certified text, screened by the shape rule above; they never come from data values.

### 4. The drafter proposes; a person certifies

- The proposal schema gains `synonyms` (at most 5) and `display_name` per metric and dimension. The prompt tells the model to derive them from the **names and screened comments only**, never from sample values.
- The existing validation applies: the redaction screen on every string, the shape and uniqueness rules above, and a new **no-value rule** (a synonym equal to any profiled sample value, or that looks like an identifier or email, is dropped and counted).
- A draft keeps synonyms as part of the **draft** document; `REVIEW.md` lists them in one block ("terms you would be certifying") with the metric each names. Nothing runs until a person certifies the document. Rejected or dropped synonyms are listed with their reason code.

### 5. Closing the loop later (not in this note's build)

Refusals already carry `unsupported_request`. A later step can turn repeated refusals that match an existing metric closely (for example many "how many orders" refusals) into **suggested** synonyms for the owner's review. It would only suggest; the same certification applies. It also needs a privacy decision (question text is user data), so it is explicitly out of scope here.

## How we will know it works (evaluation, before anything is certified)

Extend the ADS-073 abstention corpus (`reference_stack/abstention_eval.py`) with a **phrasing corpus** per contract metric: natural paraphrases that should answer, near-misses that must still be refused ("total profit" for a revenue metric), and cross-metric questions ("count and sum").

| Measure | Why |
|---|---|
| False abstention rate on the paraphrases, with and without synonyms | the gain |
| Wrong-metric acceptance rate on the near-misses (must stay 0) | the precision the echo check protects |
| Drafter synonym acceptance, drop reasons and the share a reviewer would keep | quality of proposals |

Thresholds are **proposed, not approved**, as for ADS-073. On the sandbox the first real case is the C5 pair: with reviewed synonyms Q1 and Q2 should answer and Q3 to Q5 must still be refused. The model is scripted for the corpus; a live re-run of the C5 questions is a separate, approved step.

An open gap this note found, not solved here: the echo check asks whether the chosen metric is named in the question, but not whether the question names **another** metric the intent omits ("order count and total amount" answered with the count only). Fixing it is a separate small change to the echo check; it matters more once synonyms make more questions pass.

## Stages and gates

| Stage | What | Live? | Needs |
|---|---|---|---|
| **S0 Review** | You read this note and decide the questions below. | no | your approval |
| **S1 Schema and runtime** | The optional fields, the shared normaliser and validator, `context_from_contract` and `certified_vocabulary`, the pinned-digest updates, tests with deliberate breaks. | no | none beyond S0 |
| **S2 Drafter** | Proposal schema, prompt, no-value rule, `REVIEW.md` block, tests on the scripted model. | no | none beyond S0 |
| **S3 Evaluation** | The phrasing corpus, the three measures, a report; proposed thresholds. | no | none beyond S0 |
| **S4 Certify** | **You** review the proposed synonyms and certify a new version of the sandbox contract. I do not certify. | human | you |
| **S6 Confirm instead of refuse** | The `interpretation` clarification, human-only confirmation, evidence, corpus and report with the habitual-confirmer risk figure. Offline, on the scripted model. A behaviour change to a safety gate, so it waits for your decision 6. | no | your decision 6 |
| **S5 Live re-check (optional)** | The C5 question set again on the sandbox, with the pilot's model (about 8 calls), one browser sign-in. | yes | a separate approval with the statements shown first |

S1 to S3 build and test offline with no live access.

## S6: ask the person to confirm, instead of refusing (the model understood, the words did not match)

**The question this answers.** Is the model doing the semantic understanding? Yes: it reads the question and the certified vocabulary and picks the metric, dimensions and filters. The echo check is a deterministic, lexical backstop against a model that returns a well-formed, certified intent for the *wrong* question (total revenue for "total profit"). A check that asked the same model "are you sure?" could not catch that reliably. Today, when the backstop and the model disagree, the run is **refused**, which discards a correct interpretation whenever the person's wording was not certified (the C5 Q1 and Q2 case). Synonyms shrink that set; S6 handles what is left.

**Proposal.** When, and only when, every other check passes (the intent resolves against certified ids, the contract and tenant match, nothing is uncertified, policy would allow it) and the **only** failure is the echo check, the run does not end as `unsupported_request`. It pauses with the product's existing **clarification** outcome:

```text
"I read this as: order count (counts orders). Is that what you meant?"   [ Yes, use order count ]  [ No, none of these ]
```

- Uses the existing machinery: `AnalyticsClarificationState`, `POST /runs/{id}/clarify`, identity binding ("only the requester"), at most five targeted ambiguities. A new ambiguity kind, `interpretation`, carries the model's chosen certified metric (and dimension, if grouped) with its certified display name and a one-line plain-words description built from the contract (aggregation and measure), **never data**.
- **Yes** records the confirmation and resumes the run past the echo check for exactly those ids. **No** (or no answer before the run expires) ends as the same refusal as today.
- The confirmation is a **person's act on a certified id**, so it widens nothing: the run still compiles only certified definitions, within the same policy, review threshold and ceiling. It only replaces the lexical match with the requester's own statement.
- **Humans only.** A caller that is an agent or a delegated caller (`caller_kind != "human"`, as for review decisions) gets the refusal plus the candidate id in the typed response, and cannot confirm; an agent must not approve its own interpretation.
- **Evidence.** The question fingerprint, the proposed ids, the answer and who gave it are sealed in the evidence envelope like any other transition.

**What it costs, stated plainly**
- It moves the decision for a mismatch from a deterministic gate to a person, who may click "Yes" out of habit (confirmation fatigue). Mitigations: show the metric's plain-words definition, not just its name; never offer it for a model that returned several candidates or an unresolved id; measure the confirm rate, and treat a rate near 100% as a sign the wording rules are too tight, not as proof of correctness.
- A near-miss ("total profit" answered with revenue) now asks "did you mean revenue?" instead of refusing. That is the honest interaction, but it turns a zero wrong-answer gate into "zero wrong answers unless the person confirms the wrong thing", which is a **policy decision for the owner**, not an implementation detail.
- One more pause in the run; typically a few seconds of the person's time.

**What it adds for the synonym loop.** Every confirmed pair (a question's wording, the certified id) is exactly what an owner would want to see as a **suggested synonym**. S6 records them (hashed question fingerprint plus wording the person typed is user data, so the privacy decision in section 5 applies before any suggestion is shown).

**Evaluation.** The phrasing corpus gets a scripted "user" that confirms correct readings and rejects wrong ones. Measures: refusals that become answers after a correct confirm; near-misses correctly rejected; and the proportion of wrong readings a **habitually-confirming** user would let through (the risk figure the owner needs). Thresholds are proposed, not approved.

## What this does and does not solve

It lets a certified contract say what its metrics and dimensions are called, keeps those words under the same human gate as the definitions, and lets the drafter do the typing for the reviewer. It does **not** make the system understand meaning: the model interprets the question, and the acceptance check stays lexical and conservative; a phrasing nobody certified is still refused (or, with S6, put to the person to confirm), and the quality of a customer's vocabulary is only as good as the reviewer's. It does not address per-user vocabularies, other languages, or definitions that differ between teams.

## Decisions for you (S0)

1. **Fields on the contract itself** (recommended) or a separate certified synonyms file per contract.
2. **The rules in section 2**, including the whole-word-overlap rule that blocks one-word synonyms such as `order` when a longer metric term contains it. (Recommended: yes.)
3. **The drafter proposes at most 5 synonyms per metric or dimension from names and comments only.** (Recommended: yes.)
4. **Evaluation:** build the phrasing corpus and report in S3 before you certify any synonym for the sandbox. (Recommended: yes.)
5. **Out of scope for now:** suggesting synonyms from refusals, other languages and per-user terms. (Recommended: yes.)
6. **S6:** when the model's certified reading is valid but only the wording check fails, ask the requester to confirm instead of refusing, humans only, with the risk figure measured first. (Recommended: yes, after S1 to S3 show how many refusals synonyms alone remove; if synonyms remove most of them, S6 can wait.)

Not verified: how many pinned digests move, how often a real model's proposed synonyms pass the rules, and the real false-abstention rate; each is a measurement in S1 to S3.

## S1 to S3 built (2026-11-19), offline

**What exists.** `packages/platform_contracts/semantic_terms.py` (the one normaliser and the per-word rules); optional `synonyms` on datasets, metrics, dimensions and fields and optional `display_name` on metrics and dimensions, with the cross-object rules checked when a contract validates; the words reach the ontology labels and attributes, the model's vocabulary (never for a restricted field), the echo check and the resolver; the drafter proposes them (prompt `m2-v2`, per-word screen including the no-value rule, each word kept or dropped with a code, `REVIEW.md` block "Terms you would be certifying"); an offline phrasing and proposal evaluation (`reference_stack/synonym_eval.py`, report `docs/evaluation/semantic-synonyms-s3-report.md`). Tests: `test_semantic_synonyms.py` (39), `test_ontology_draft_synonyms.py` (18), `test_synonym_eval.py` (5), one more in `test_databricks_govern.py`; deliberate breaks of each rule were caught.

**Compatibility, better than planned.** The new fields are left out of a dump when empty, so every existing certified contract hashes and fingerprints exactly as before: **no pinned digest or snapshot id moved**, and the full backend suite passed unchanged before any new test was added. Existing contracts that already used overlapping ids are left as certified (the overlap rule applies to explicit words only; a dataset's existing display name is not treated as one).

**What the evaluation says (author-written cases; thresholds proposed, not approved).**
- With a reviewed synonym set, natural paraphrases answered rise from 2 of 11 to 7 of 11. **The proposed limit (false abstention at most 25%) is not met: 4 of 11 are still refused** ("count of orders", "how many purchases", "how much did customers spend in total", "total sales"). Synonyms alone do not remove the problem; each missing phrasing needs a reviewed word, or S6.
- **Near-misses wrongly accepted: 0 of 8, with and without synonyms.** The echo check's precision holds.
- **A new risk the synonyms expose, and the main finding:** the echo check only asks whether the *chosen* metric is named. Once "how many orders" is a certified synonym, a question that adds a constraint the intent drops ("How many orders were refunded?", "...last year?", "...in EMEA?") is **accepted and answered with the unconstrained count: 4 of 4 with synonyms, 0 of 4 without.** A question naming a second metric the intent omits is accepted 2 of 2 (1 of 2 already today). A synonym makes the check pass more often, so these gaps now matter.
- The drafter's rules kept 7 of 7 words the author considered wanted and 0 of 9 considered unwanted (generic words, ids, grouping phrases, time words, identifier-like text, collisions).

**What this changes in the plan (a recommendation, not a decision).**
1. **Do not certify synonyms for the sandbox (S4) yet.** Close the dropped-constraint gap first, or a certified synonym will turn "how many orders were refunded" into a confident wrong number. Two ways, not mutually exclusive: (a) a deterministic check that the question's remaining content words are all accounted for by the intent (conservative; it will refuse more, and needs its own false-abstention measurement), or (b) show the person what was understood ("count of all orders, no filters") and let them confirm, which is the "show what I understood" item and fits S6's confirmation step.
2. **S6 now has evidence behind it.** With synonyms, 4 of 11 reasonable paraphrases are still refused on this contract, and the cheapest way to recover them without widening the lexical gate is the requester's confirmation, shown with the constraints the system understood.
3. The omitted-second-metric gap is a small separate echo-check change.

Not verified: any real model's choices, a real reviewer's synonym choices, other contracts, or whether (a) refuses too much.
