# Certified synonyms: a design note (2026-11-18)

Status: **a design note only. Nothing is built and no gate is approved.** It needs the owner's review (S0 below). Any change to a certified definition, including a synonym, stays the owner's explicit act (CLAUDE.md: semantic certification is a human gate).

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
| **S5 Live re-check (optional)** | The C5 question set again on the sandbox, with the pilot's model (about 8 calls), one browser sign-in. | yes | a separate approval with the statements shown first |

S1 to S3 build and test offline with no live access.

## What this does and does not solve

It lets a certified contract say what its metrics and dimensions are called, keeps those words under the same human gate as the definitions, and lets the drafter do the typing for the reviewer. It does **not** make the system understand meaning: matching stays lexical and conservative, a phrasing nobody certified is still refused, and the quality of a customer's vocabulary is only as good as the reviewer's. It does not address per-user vocabularies, other languages, or definitions that differ between teams.

## Decisions for you (S0)

1. **Fields on the contract itself** (recommended) or a separate certified synonyms file per contract.
2. **The rules in section 2**, including the whole-word-overlap rule that blocks one-word synonyms such as `order` when a longer metric term contains it. (Recommended: yes.)
3. **The drafter proposes at most 5 synonyms per metric or dimension from names and comments only.** (Recommended: yes.)
4. **Evaluation:** build the phrasing corpus and report in S3 before you certify any synonym for the sandbox. (Recommended: yes.)
5. **Out of scope for now:** suggesting synonyms from refusals, other languages and per-user terms. (Recommended: yes.)

Not verified: how many pinned digests move, how often a real model's proposed synonyms pass the rules, and the real false-abstention rate; each is a measurement in S1 to S3.
