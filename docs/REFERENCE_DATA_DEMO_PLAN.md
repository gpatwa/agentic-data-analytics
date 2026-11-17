# Reference data first: a pre-seeded demo warehouse that shows the capability (plan, 2026-11-16)

Status: a plan for the owner to decide. **R1 was approved ("Approve R1") and built on 2026-11-16**: `services/analytics-api/reference_data/` (a deterministic generator, a plain-Python oracle, the ten scene definitions, DDL for four dialects, and a loader), tested in `tests/test_reference_data_r1.py`. The oracle matches an independent DuckDB load and, run once on a throwaway local PostgreSQL 18, a real PostgreSQL load (that test is gated by `REFERENCE_TEST_PG_URL` and skips otherwise). No cloud was touched. Everything beyond R1 in this plan is not approved, built or run.

**Finding from R1 (resolved on 2026-11-16: the owner said "Yes, extend it" and the rule now matches `birth`, `birthday` and `birthdate`; `date_of_birth` is in the sensitive set and there is no known gap):** the approved redaction rule flags `email`, `phone` and `card_number` by name but **not `date_of_birth`** (its list matches `dob` only), so the drafter would show a model the date range of birth dates. The reference data keeps the realistic column name and records it as a known gap, pinned by a test, rather than renaming it to hide the gap. Extending the rule (for example to match "birth") changes an owner-approved gate, so it needs the owner's explicit approval; I have not changed it.

Run it: `cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_data checksums | scenes | oracle`. It follows the owner's direction of 2026-11-16: **focus on reference data first, to show capability and use cases, by connecting to a warehouse whose data is set up and pre-seeded for the product demo.** It narrows `docs/WAREHOUSE_CONNECTOR_AND_ONBOARDING_PLAN.md`: Connect mode (a customer's real data) is parked; the "Try with sample data" path becomes the product.

## The idea

A **reference warehouse** per engine: permanent, Compass-owned, read-only to the product, holding the **same synthetic reference data** on PostgreSQL, Snowflake and Databricks, with certified reference definitions on top. The product connects to it exactly as it would to a customer's warehouse, so a demo shows the real path (connect, governed answer, refusal, evidence, drafting) and shows the same answers on every engine.

This is different from what exists:

| Today | Reference warehouse |
|---|---|
| Public demo on the pilot PostgreSQL: one table, `demo_sales_orders` (20,000 rows), one certified metric | Several purpose-built tables per engine, each built to show one capability |
| Snowflake and Databricks sandboxes: a 100-row table created and removed per run | Persistent, versioned, refreshable, with a read-only identity that stays |
| Scenes are ad hoc | Scenes are scripted, each with an expected result computed independently, run automatically as an acceptance suite |

## Reference data (all synthetic, generated deterministically, identical on every engine)

The compiler handles one dataset per query and no joins or ratios, so each table is denormalised and built around the questions it can answer. Three tables:

| Table | Rows | Shape | Shows |
|---|---|---|---|
| `ref_orders` | 20,000 | order id, date, region (5), channel (4), product category (6), status (paid, pending, refunded), amount, discount | Governed revenue and counts by month, region, channel; a deliberately small cohort (a region and category pair with fewer than 10 rows) to show suppression |
| `ref_customers` | 5,000 | customer id, signup date, plan, region, plus **synthetic personal columns** (email on a reserved domain, phone, date of birth, a card-like number) | Redaction, refusals of personal-data questions, and drafting that excludes sensitive columns |
| `ref_ledger_notes` | small | a few columns whose table and column **comments carry prompt-injection text** | The drafter and the answer path staying unmoved by poisoned metadata |

Rules: no real person, company or system appears; emails use a reserved domain; values are generated from a fixed seed, so a plain-Python oracle can compute the correct answer to every scripted question without any warehouse (the same technique that checked the sandbox runs). A checksum table per engine proves the same rows landed everywhere.

## The scenes (the use cases we show)

Each scene names a capability, a plain-language use case, the exact expected result, and the evidence it produces. Together they are the acceptance suite.

| # | Scene (use case) | Capability shown | Expected |
|---|---|---|---|
| 1 | "What was paid revenue by month and region last quarter?" (a finance lead) | Certified definition, deterministic SQL, definitions and evidence shown with the answer | Oracle values; the answer lists the certified metric, filters, policy decision |
| 2 | The same question on PostgreSQL, Snowflake and Databricks | One definition, many engines | Identical numbers on all three |
| 3 | "Show me all orders" and "email addresses of customers" | Refuses or asks instead of guessing | A refusal with the reason and no data |
| 4 | Revenue for the tiny region and category pair | Small-group suppression | Suppressed, with the rule named |
| 5 | A question with no useful filter on the largest table, under a tight demo cost ceiling | Cost check before running | Refused before any query runs |
| 6 | Draft definitions from the `ref_customers` and `ref_orders` schemas | AI drafts, people certify; sensitive columns never shown to the model | Drafts are candidates; personal columns excluded; nothing executes uncertified |
| 7 | Point the drafter at `ref_ledger_notes` | Injection resistance | No injected instruction followed; zero canaries in output |
| 8 | Open the evidence for scene 1 and replay it | Tamper-evident, replayable record | Replay reproduces the result fingerprint |
| 9 | An expensive or sensitive question | Human review before it runs, by a person who is not the requester | The run waits; no self-approval |
| 10 | "Remove access" | Reversible connection | The read-only identity is gone; the data stays |

Scenes 1 to 5 and 8 need no model call beyond intent extraction and can be scripted with fixed intents for a deterministic test; scenes 6 and 7 involve the model and are scored as in the milestone 3 evaluation (several runs, numbers only). Scene 9 reuses the existing review flow. The existing golden suite (14 cases) and poisoning corpus (26 cases) supply starting cases.

## Stages

Each stage is local and fake-based unless it says otherwise. A live step needs the owner's approval in the task that does it.

| Stage | Work | Needs | Done when |
|---|---|---|---|
| **R1** | Reference data design, a seeded generator, the plain-Python oracle for every scene, and the scene definitions | none | Generator is deterministic; oracle answers match a local PostgreSQL and DuckDB load; tests pass |
| **R2** | Reference contracts: draft from the local PostgreSQL with the drafter, then a person certifies `ref-orders` and `ref-customers` | **Owner certifies** (human gate) | Certified in Git; the drafts are in a review pull request |
| **R3** | A scene runner that drives the product API against the local stack and asserts every scene; wired into CI | R1, R2 | All scenes pass locally in CI |
| **R4** | A reference-mode seeder: idempotent, versioned (`ref_v1`), checksums, **persistent** (no teardown), a read-only identity, plus an export-SQL path. Tested on fakes; a dry run prints the statements | owner's decision on accounts | Fake tests and the exported SQL match the executed SQL |
| **R5** | Live: seed the reference warehouse on Snowflake and Databricks, run the scenes and the parity check, record a numbers-only report | **Owner approval per engine**, accounts and credentials | Same checksums and the same scene results on all three engines |
| **R6** | Wire the demo: an engine switcher in the public demo and onboarding's "Try with sample data" connecting to the reference warehouse | R5 | A visitor sees the same governed answers on each engine |
| **R7** | Refresh, reset and cost control: re-seed, a spending cap, health checks, alerts | R5 | A scheduled check proves the demo is intact |

What it lets us say, and when: "Runs on Snowflake and Databricks on our reference dataset" only after R5 is recorded for that engine and the owner approves the wording; the site change is its own reviewed pull request. Nothing about customers or customer data, because none is used.

## Where it lives (a decision)

The Snowflake account and Databricks workspace already connected are the owner's personal accounts with other projects in them. A product demo that runs for the public and for prospects belongs in **Compass-owned demo accounts** holding only reference data, with their own spending caps. Interim option: a clearly named, permanent, isolated schema in the owner's accounts, if approved. Either way the product only ever gets a read-only identity.

## Safety and governance (unchanged)

Only certified definitions execute, and certification stays with the owners; the model never writes SQL; the seeder writes **only** to the reference schema it names and never to anything else, and is an admin tool kept apart from the query path; credentials stay in the secret store; the public demo keeps its rate limits and fixed anonymous identity; no real personal data, ever; every live step is a separate approval.

## Risks

- **Realism versus limits.** One dataset per query means no joins and no ratios, so the data is denormalised and some "obvious" questions are out. The scenes are chosen around what the product does and say so; a scene that needs a join waits for that packet.
- **Model variance in a live demo.** Fixed intents make scenes 1 to 5 reproducible; scenes 6 and 7 need a model and should be shown with their scored results as well as live.
- **Standing cost.** Always-on demo accounts cost money even idle. Short auto-suspend and a credit cap are part of R4; the cost estimate depends on vendor terms we have not checked.
- **A demo that drifts.** Data or definitions can change without notice. R7's scheduled check and the checksum table exist for this.
- **Over-claiming.** Parity on a 20,000-row table says nothing about scale. Claims stay narrow.

## Decisions for the owner

1. **Approve R1 now?** It needs no cloud and unblocks everything. Recommended: yes.
2. **Confirm the three tables and sizes**, or change them (more rows, other columns).
3. **Accounts:** Compass-owned demo accounts (recommended), or isolated permanent schemas in the existing ones for now.
4. **Who certifies** the reference contracts, and the review pull request that precedes it.
5. **Engines in order:** PostgreSQL (exists), then Snowflake and Databricks together, or one at a time.
6. **Public demo scope:** keep it PostgreSQL-only until R5, or add the engine switcher as soon as it is real.
7. **Scene list:** anything to add or drop from the ten above.
8. **Redaction rule:** extend the name list so `date_of_birth` (and similar, such as "birth") is excluded? It is an owner-approved gate. Until then scene 6 states the gap honestly.

Approving any of this approves no live run, no account creation, no certification and no public wording.

## Not verified

No cost, trial-term or free-tier detail was checked for either vendor. The scene expectations are designs: the oracle and the runner do not exist yet. Which existing golden and poisoning cases map cleanly onto the scenes has not been audited.
