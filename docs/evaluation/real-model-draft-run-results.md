# The first real-model drafting run over the synthetic schema: results (2026-11-21)

Approved terms (program file `orders_usd_gross_create_and_model_deployment` and `real_model_run_key_from_az_login`): one run on `dev.compass_synthetic_food_delivery.orders_current` through the owner's signed-in `databricks` profile, the pilot Azure OpenAI deployment `gpt-6-luna` (account `compass-pilot-openai`, `2025-04-01-preview`, reasoning effort medium), at most 1 model call, 4,000 reply tokens, 5 minutes, draft only. The key was read from the owner's `az` session into the environment of that one process: never printed, written or passed as an argument.

| Attempt | Outcome | Model calls |
|---|---|---|
| 1 | stopped at `profile_failed` after 2 statements: the session adapter did not answer `prepare` (a bug in the new wiring, fixed and tested) | 0 |
| 2 | stopped inside the SDK before any request: the local `openai` package was 1.10.0 and the repo pins 1.109.1 (`max_completion_tokens` unsupported). The pinned version was installed in a throwaway folder in the scratchpad, not into the user's packages | 0 |
| 3 | **written**: 17 statements, 1 table read, **1 model call**, draft `food-delivery-orders-current-real/draft-214826a4` | 1 |

## What the real model produced

- **One metric:** `order_count` (a count of rows). **Five dimensions:** order timestamp, city, country, order status, platform. No synonyms. No joins. Nothing dropped by the validator. Leaked secrets 0, injected canaries 0, validator escapes 0.
- It proposed **no amount sums, tip sums or averages at all**, whereas the agent that stood in for the model earlier proposed a total amount that added three currencies and a delivery average over a `-1` placeholder. Whether that caution comes from the model or from the redacted profile it was shown is not established by one run.
- Every item it proposed is already in the certified contract `food-delivery-orders-current` 2026-11-21: `order_count` and the dimensions on `order_ts`, `city`, `country`, `status` and `platform`. It did not propose `cuisine`, `payment_method` or `currency`, which the certified contract carries.
- The profile chose `row_id` as the key of the view (unique and never null across the table). The certified contract uses `order_id`, which is also unique in the view. Both are valid grains for one row per order; confirm the grain when comparing.

## What this does and does not show

- It shows the product's own drafting path (prompt `m2-v2`, the real validator, the redaction and leak scans, the guarded session) working end to end against a Databricks view with a real model, within the caps, writing only a draft.
- It does **not** show draft quality in general: one table, one run, one model, no repeat for variance. The cost was not measured (the run records calls and statements, not tokens).
- Nothing was certified or granted. The draft is not in a registry folder. The earlier statement that a real run "would probably propose the unsafe amount sums" was a guess and the run did not bear it out; it should not be repeated.
