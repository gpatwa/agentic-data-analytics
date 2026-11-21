# A real-model drafting run over the synthetic schema: checklist (2026-11-21)

Status: **prepared, not run.** No model has been called and no credential is held by this repository. The driver exists (`services/analytics-api/app/ontology_draft/databricks_draft_run.py`, PR 118); it refuses to run without `--allow-live` and without an injected session and model client. Every item below is an owner action or an owner decision.

## What the model would see, and what it would not

It would receive the prompt the product builds from schema facts (`app/ontology_draft/drafter.py`): table and column names and types, declared or profiled keys, column comments, and redacted aggregate statistics. **Never** raw rows, credentials or hosts. Columns that look sensitive are marked restricted and excluded. On this schema every value is synthetic, so the data risk is low; the run still exercises the egress path a customer would use, which is why the opt-in matters.

## Decisions for you

| # | Decision | Suggestion |
|---|---|---|
| 1 | **Which model and where it runs.** The product's drafter uses an Azure OpenAI deployment (`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`, set in the environment of the run, never in a file or argument). | Use the pilot's existing deployment if you have one; otherwise do not create one just for this. |
| 2 | **Model-egress opt-in for this schema.** Per-customer opt-in is the recorded policy (`connect_mode_cm1_decisions`). Synthetic data is yours, so you can opt in for it. | Opt in for `dev.compass_synthetic_food_delivery` only, in writing. |
| 3 | **Which session reads the schema.** Either the owner's signed-in `databricks` profile (read-only metadata, no new principal) or the Connect principal (needs the registration grant first). | Your profile for the first run: no grant is needed and the driver restricts reads to the listed tables. |
| 4 | **Which table.** `orders_current` (the certified-contract's view) is the useful one. | `orders_current` only. |
| 5 | **Caps.** Driver defaults: at most 3 model calls, a reply token limit, a wall-clock cap. | 1 call, 4,000 reply tokens, 5 minutes. |

## What I need from you to run it

1. The environment variables set in the shell that runs it (I will not read or print them), or a statement that they are not available.
2. An approval sentence naming the model deployment, the table, the call cap and the stop rule.

Suggested sentence:

> Approve D: one run of the Databricks draft driver on `dev.compass_synthetic_food_delivery.orders_current` through my signed-in profile and the Serverless Starter Warehouse, with the model deployment I name in the environment, at most 1 model call and 5 minutes, draft only, stopping on any leak, validator escape, injected canary or table outside the list. This does not approve certifying anything, any grant, or any other table.

## Stop rules and what is written

The run stops and writes nothing on a leak, a validator escape, an injected canary, a failed EXPLAIN or a table outside the list. On success it writes a draft under `semantic_registry_drafts/` (never a registry folder) and a REVIEW.md. Nothing is certified by the run.

## What a real model would test that the stand-in did not

The earlier drafts were written by a Claude agent standing in for the model, which exercised the validator, not the product's prompt against its production model. A real run shows whether that model proposes the unsafe amount sums the stand-in also proposed, and whether the prompt's wording steers it away from cross-currency sums. Expect it to propose them anyway: the validator does not know about mixed currencies, which is why the USD view exists.
