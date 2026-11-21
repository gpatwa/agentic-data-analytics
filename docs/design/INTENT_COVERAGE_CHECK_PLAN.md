# The intent coverage check: a plan to review (2026-11-19)

Status: **K0 was approved by the owner on 2026-11-19 with the recommended answers (program file `intent_coverage_k0`). K1 and K2 are built offline (see "K1 and K2 built" at the end); K3 (enforcing it), K4 (confirm) and S4 are not approved.** The check can only refuse (or ask) more often, never less, so it cannot weaken an existing gate; it is still a behaviour change to a safety gate, so it needs the owner's approval (K0 below). It continues `SEMANTIC_SYNONYMS_DESIGN.md` (S1 to S3 built; S4, certifying synonyms, waits for this).

## The problem, measured

The intent-echo check asks one question: *is the metric the model chose named in the question by a certified term?* Once a synonym such as "how many orders" is certified, that single test passes for **any** question that begins that way. In the S3 evaluation (`docs/evaluation/semantic-synonyms-s3-report.md`, author-written cases):

| Question the model reads as "count of orders" | Without synonyms | With synonyms |
|---|---|---|
| "How many orders were refunded?" / "...last year?" / "...placed in EMEA?" (a constraint the intent omits) | refused | **answered with the unconstrained count (4 of 4)** |
| "How many orders are there and what is the total order amount?" (a second metric the intent omits) | refused or answered by accident (1 of 2) | answered (2 of 2) |

That is a confident wrong number: the person asked about refunded orders and was told the total. The model is not the safeguard here (it may well add a filter when the contract has a field for it, and will not when it does not), and the check that exists never compares what the question *says* with what the intent *uses*. Near-misses are fine (0 of 8 accepted).

## What the check does

A deterministic, lexical **coverage** check, run right after the echo check, on the same certified ontology words:

1. Split the question into stemmed tokens (the shared normaliser).
2. **Account** for the tokens the intent explains: the phrase of each chosen metric that matched; each grouped dimension's phrase; each filter's field phrase and its values; the time words when, and only when, the intent has a time range or a time grouping; sort and "top N" words when, and only when, the intent has a sort or a limit it changed.
3. Remove **neutral** words: a small, reviewed, versioned, generic English list (articles, auxiliaries, question frames such as "what is", "how many", "show me", "please", "we", "our").
4. Whatever is left is **unaccounted**: a content word the question uses and the intent does not. Any unaccounted word means the intent may not be what was asked.

It never repeats a word in a log or evidence (counts, codes and hashes only); the requester sees their own words back.

## What happens when words are unaccounted (the decision)

| Option | What the requester gets | Cost |
|---|---|---|
| **A. Refuse** | `unsupported_request` with the unaccounted words named to the requester | strictest; every unlisted phrasing is a refusal |
| **B. Confirm (recommended for humans)** | the S6 clarification: "I understood: count of all orders, no filters, no time period. I did not use: **refunded**. Continue anyway?" with Yes / No | one extra step; Yes is a person's explicit act on a stated interpretation |
| **C. Disclose** | the answer plus a line "not applied: refunded" | risk that the line is not read |

Recommended: **B for human callers, A for agents and delegated callers** (an agent must not confirm its own interpretation, as for review decisions). C is rejected: a wrong number delivered first and corrected in small print is the failure being fixed.

A pure-A first release is a valid step: it needs none of S6.

## A prototype, with its caveats stated

I ran a throwaway version (not kept; the real one is K1) over the 25 S3 cases plus 8 more benign paraphrases I wrote, using the reviewed synonym set:

- **Caught:** 4 of 4 dropped constraints (`refunded`, `last month`, `placed ... EMEA`, `placed ... last year`) and 2 of 2 omitted second metrics; every near-miss was already refused and stays refused.
- **Valid paraphrases wrongly flagged: 5 of 15** (those the echo check answers with the right metric): two are a tokenizer artefact (`what's` leaves a stray `s`, a bug to fix), and three are real vocabulary gaps (`received`, `placed`, `in total`).
- **Overfit warning:** I wrote the neutral list after seeing those cases. The honest expectation is a *higher* false-flag rate on questions someone else writes. The remedy is not to grow the generic list: a business word such as "placed" or "received" belongs in the metric's certified synonyms ("orders placed", "orders received"), which the owner adds in the same reviewed change, so the contract's vocabulary, not a hidden list, decides what is covered.

## Rules that keep it safe

- **Only ever stricter.** It runs after every existing check and can only turn an answer into a refusal or a confirmation. Disabling it restores today's behaviour exactly.
- **The neutral list is generic, versioned and tiny** (about 60 function words), reviewed like a contract; domain words never go in it.
- **No words in logs or evidence**: unaccounted words are recorded as a count and a hash; the requester's own response may show them.
- **Numbers, quoted values and proper nouns** are never neutral. A number is accounted only by a limit, a filter value or a time range the intent holds.
- **English only.** Another language produces unaccounted words and is refused or asked to confirm, as today it is refused.

## Evaluation (before anything is enforced)

Extend the S3 corpus into a **coverage corpus** (a new suite, pinned, fakes only):

| Group | Target |
|---|---|
| Dropped constraint (a filter, a time period, a sort or a limit the question states and the intent omits) | **0 accepted** |
| Omitted second metric | **0 accepted** |
| Valid paraphrases with polite or idiomatic extra words | measured false-flag rate; proposed limit 20%, **not approved** |
| Near-misses | 0 accepted (unchanged) |
| Hold-out: 20 questions written by the owner, not by me, never used to tune the list | the honest false-flag figure |

A real-model check (the C5 questions plus the corpus, about 8 to 30 calls on the sandbox) is a **separate approved live step**; nothing here needs it.

## Stages and gates

| Stage | What | Live? | Needs |
|---|---|---|---|
| **K0 Review** | You read this plan and decide the questions below. | no | your approval |
| **K1 Build** | The check and its neutral list as one module wired after the echo check behind a switch (default off); stable codes; tests with deliberate breaks, including "disabled equals today's behaviour". | no | K0 |
| **K2 Corpus and report** | The coverage corpus, the pinned measures and a generated report; the hold-out file for you to fill in. | no | K0; the hold-out is yours |
| **K3 Enforce, option A** | Switch on for the sandbox tenant only; refusals name the unaccounted words to the requester. | no | your approval of the K2 numbers |
| **K4 Confirm, option B (S6)** | The clarification with the stated interpretation, humans only, evidence sealed. | no | your decision 6 of the synonyms note, and K3 |
| **S4 Certify** | You certify the reviewed synonyms, now that a dropped constraint can no longer pass. | human | you |

Everything up to K4 builds and tests offline.

## Decisions for you (K0)

1. **Build it:** a lexical coverage check after the echo check, only ever stricter. (Recommended: yes.)
2. **First release is option A (refuse, naming the unaccounted words); option B follows with S6.** (Recommended: yes.)
3. **Domain words belong in certified synonyms, not in the neutral list**, which stays generic and about 60 words. (Recommended: yes.)
4. **You write the 20-question hold-out** before K3, and K3 waits for those numbers. (Recommended: yes.)
5. **Certifying synonyms (S4) waits for K3.** (Recommended: yes.)

## What this will and will not do

It closes the specific hole synonyms open: a stated constraint or second metric silently dropped. It does not make the system understand meaning, it will refuse some good questions (the cost of being deterministic), and it cannot catch a wrong value inside a correct-looking filter (a model that filters on `EU` when the question says `EMEA` is caught only if the filter-value check compares the token; that is one of the K1 tests, not a guarantee).

Not verified: the false-flag rate on questions written by anyone but me, any real model's behaviour, and other contracts.

## K1 and K2 built (2026-11-19), offline

**K1.** `app/runtime/intent_coverage.py` (the check and its neutral list `neutral-v1`), wired into the ontology resolution node after the echo check, **off by default** and switched on per tenant (`ANALYTICS_INTENT_COVERAGE_TENANTS`, a comma list or `*`; a tenant not listed is untouched). On a mismatch the run ends as the existing `unsupported_request` refusal with a new internal code `intent_coverage_mismatch`. **Evidence holds positions and a hash, never words** (`unaccounted_words:2@5,6#<hash>`); the requester's response rebuilds their own words from their own question and those positions ("The question includes words the interpreted request does not use: "refunded"."). Tests: `tests/test_intent_coverage.py` (32, including end to end through the governed runtime, the evidence scan, and "only ever stricter"); deliberate breaks of every accounting rule, the switch, the tenant filter, the service mapping and the no-words rule were caught (two initial survivors got their own tests).

Two differences from the plan above: the neutral list has **84 words**, not "about 60" (generic function words plus the contraction fragments `s`, `t`, `re`, `ve`, `ll`, `d`, `m` that the tokenizer produces), and filter words such as `with`, `where`, `than` are accounted only when the intent holds a filter, like time and sort words.

**K2.** `reference_stack/coverage_eval.py`, a pinned 49-case corpus, the generated report `docs/evaluation/intent-coverage-k2-report.md`, and the **hold-out template** `reference_stack/m7/coverage-holdout.owner.json` for you to fill in (empty; K3 waits for it). The neutral list was fixed **before** the corpus was written and was not changed after seeing results. Author-written cases, so optimistic:

| Group | Echo check alone: answered | Plus coverage: answered |
|---|---|---|
| Valid plain questions (answered is good) | 20 of 20 | **16 of 20** (4 newly refused, 20%: the proposed limit is met only at its boundary) |
| Faithful intents that hold their condition | 3 of 3 | 3 of 3 |
| Dropped constraint (answered is bad) | 11 of 12 | **0 of 12** |
| Omitted second metric (answered is bad) | 5 of 5 | **0 of 5** |
| Near-misses (answered is bad) | 0 of 8 | 0 of 8 |

The four valid questions newly refused are filler and a split phrase ("right now", "Quickly,", "Hi,", "received in total"). The sandbox contract has no temporal dimension, so no intent can hold a time range there: every time condition is a dropped constraint by construction, and the time-accounting rules are covered by unit tests.

**What is left for you.** Fill in `coverage-holdout.owner.json` (20 questions with the metric and whether you expect an answer or a refusal; do not read the report first). Then K3 can be proposed with your numbers, not mine.

## K3 and S4 prepared (2026-11-20), nothing certified, nothing deployed

**The hold-out is in** (20 questions written by a fresh agent, PR #111): no refusal was wrongly answered (0 of 10); 9 of the 10 plain valid questions were refused, 8 by the existing echo check (the fixture synonym set does not name them) and 1 by the coverage check.

**S4 prepared.** A second fresh agent, which saw only the contract's metric and dimension descriptions and the validator's rules (not the corpus, hold-out or neutral list), proposed synonyms. The preparer filtered words that could mean something the contract does not define (`order volume`, `order quantity`, `order size`, `amount per order`) and those the validator rejects (a cap of 10 per item; `order amount` equals the field id). The result is a validated **draft**, `semantic_registry_sandbox/proposed/sandbox-databricks-v2.proposed.json`, built by `reference_stack/synonym_proposal.py`. The certified v1 is unchanged.

**Measured once, not tuned to:** with the proposed set the hold-out has 8 plain valid questions refused, not 9 (only "count of orders" is newly answered); the author corpus shows 0 dropped constraints, 0 omitted metrics and 0 near-misses accepted (the holes stay closed). **Synonyms alone do not fix the long tail** ("orders total count?", "num of orders pleaes", "sum of amount", "total revenue"): each would need its own reviewed word, and a word like `revenue` means something the contract does not define. That is S6's job (confirm instead of refuse), which is still the owner's decision.

**What stays with the owner:** certifying `2026-11-20` as the next version (reading the words), enabling K3 (`ANALYTICS_INTENT_COVERAGE_TENANTS=sandbox` is a deployment setting; not applied), and S6.

## K3, S4 and S6 done as far as the repository goes (2026-11-20)

The owner approved items 1 to 4 of the priority list (program file `s6_s4_k3_decisions`).

- **S4 certified.** Sandbox contract version `2026-11-20` is in `semantic_registry_sandbox_v2/contracts/sandbox-databricks-v2.json`: synonyms only, no definition changed, the words and their digest recorded in the approval. v1 is untouched and **stays the active contract**: a registry root holds exactly one certified contract per tenant (the Connect resolver refuses two as `contract_ambiguous`), so v2 sits in its own registry folder, `semantic_registry_sandbox_v2/`, and is activated only when a deployment points the tenant's registry at it (together with K3). Certified is not the same as served. The measured effect is small (the hold-out recovers 1 plain question), and the holes stay closed.
- **A guard, so synonyms cannot ship without K3.** A contract with synonyms is refused for a tenant that does not have the coverage check (`synonyms_need_coverage`). Deploying v2 without K3 therefore fails closed instead of answering a dropped constraint.
- **K3.** The sandbox certification harness runs with the coverage check. The deployment setting is `ANALYTICS_INTENT_COVERAGE_TENANTS`; **there is no deployment target for the sandbox tenant in this repository, so nothing was changed in any environment.** Setting it where the sandbox tenant is served is a deployment step the owner asks for.
- **S6 built, off by default.** `ANALYTICS_INTERPRETATION_CONFIRM_TENANTS` lists the tenants whose requesters are asked "I read this as ... Is that what you meant?" when only the wording check fails. Humans only (an agent gets `human_required`), bound to the exact reading (contract, metrics, groupings, filters, time range, sort, limit), a "no" is the refusal it would have been, evidence holds no words. Scripted-user evaluation: `docs/evaluation/interpretation-confirm-s6-report.md`. A careful user turns every refusal of a right reading into an answer; **a habitual confirmer lets every wrong reading that is asked about through (25 of 25 in the corpus, 10 of 10 in the hold-out).** The owner accepted that risk in the approval; it is not enabled for any tenant.
