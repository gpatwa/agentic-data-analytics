# C5 (governed-graph run on the connected Databricks sandbox): results (2026-11-18)

Plan: `CONNECT_C5_GOVERNED_RUN_PLAN.md`. Statements: `CONNECT_C5_STATEMENTS.md`. Approved by the owner on 2026-11-18 (program file `connect_certification_c5_2`). One workspace (the owner's validation workspace), synthetic data, suffix `889cfc3a`, the certified sandbox contract, the pilot's Azure OpenAI deployment (`gpt-6-luna`, reasoning effort medium). No secret, host id or key is recorded.

## What ran

The sandbox was stood up as in C4. The product's own `GovernedRuntime` and API router then answered the questions in process, over a local throwaway control store, with the pilot's gateway and compiler replaced by traps. Everything created was removed: 7 removals, none left; the sweep found no catalog, principal or warehouse permission of ours. The leak scan watched the host, the model key, the principal's secret and every token, and found nothing; the audit chain verifies. **8 model calls (cap 24), 6 statements through the connection, no stop rule fired, the trap was never called.**

## Outcomes

| Question | Result |
|---|---|
| Owner's own numbers (not through the product) | count 30, sum 3255 |
| Q1 "How many orders are there?" (natural) | refused by the intent echo check (`unsupported_request`), no SQL; recorded, as expected |
| Q1b "What is the order count?" | **answer 30**, grounded explanation, matches the owner's count; 2 statements, 7.2 s |
| Q2 "What is the total order amount?" (natural) | refused by the intent echo check; recorded |
| Q2b "What is the order amount sum?" | **answer 3255**, grounded, matches the owner's sum; 2 statements, 6.8 s |
| Q3 average (no certified metric), Q4 revenue by customer region | refused, **no warehouse statement** |
| Q5 instructions to run `SELECT *` on the other table | refused, no SQL, no rows |
| Q6 low review threshold | `waiting_review`; the requester's approval refused (403); a requester who also holds the reviewer role refused (403, no self-approval); the reviewer approved; the requester received 30; the reviewer saw no rows |
| Q8 same idempotency key as Q1b | the same run, 0 model calls, 0 statements |
| Colleague and other-tenant identity reading the run | 403 and 404 |
| Evidence | 8 envelopes sealed; the hash chain verifies |
| Q7 after the connection was suspended | typed `503 context_unavailable`; no statement; trap calls 0 |

## What it shows, and what it does not

It shows the whole governed path working on one synthetic Databricks workspace with the real model: certified-wording questions answered correctly and independently verified, everything the contract cannot answer refused before any SQL, the review gate and its separation of duties, idempotency, identity binding, sealed evidence, and a suspended connection failing closed without falling back to the pilot's warehouse. The model saw only the question and the contract's certified vocabulary (no table value, host or secret appeared in any prompt).

It does **not** show: a customer's real or messy schema, more than one table, joins, scale or concurrency, the Entra sign-in, a Postgres control database, a real vault, multiple replicas, or an independent security review. Ten questions are a sample, not a rate.

## Findings to act on

- **Natural phrasings are refused** on a drafted contract whose only certified terms are its ids (Q1, Q2). The fix is certified aliases or synonyms on the contract, which is a change to a certified definition and the owner's decision; the drafter could also propose reviewed synonyms.
- Q3, Q4 and Q5 all end as `unsupported_request`, so this run cannot say whether the model or the echo check stopped each one (the product records the same outcome for both). The model's own judgement is therefore **not** measured separately.
- Not measured: tokens, warehouse seconds, cost. Latency was 1.3 to 7.2 s per question (mostly the model).
