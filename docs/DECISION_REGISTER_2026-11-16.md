# Decision register (2026-11-16): panel recommendations, and what needs the owner

Status: **the panel's text below is advisory; the owner decided all fifteen gates on 2026-11-17 (see "Owner decisions" at the end and the program file, `decision_register_gate_decisions`).** Until then nothing here was a recorded human-gate approval. On 2026-11-16 the owner asked for a separate Opus agent in a specialist role to make the pending decisions, and offered Fable 5.2 on low effort as an alternative. Four read-only agents answered independently: a Senior Data Architect, a Security and Governance Architect and a Product and Go-to-Market Lead (all Opus), and a Chief Architect second opinion (Fable 5.2, low effort, which read less of the repo). The assistant checked the technical claims below against the code before relying on them.

**Delegation is not approval.** `CLAUDE.md` makes semantic certification, security, policy, thresholds, licensing, public wording and go/no-go the owner's gates, and says never to infer one. So:
- **Part A** lists decisions that are not gates. The owner delegated them, so they are adopted as working decisions and are reversible.
- **Part B** lists gate decisions. The panel's view is a recommendation; each becomes final only on the owner's explicit yes, which is then recorded in the program file in the owner's words.

## Part A: adopted as working decisions (not gates; reversible)

| ID | Decision | Basis |
|---|---|---|
| D2 | Keep the three reference tables and sizes. Do **not** change the data now; at R3, if the runner shows scenes 5 and 9 need another many-valued groupable column, add it then and bump to `ref_v2` | Data Architect, Fable |
| D3 | Keep **nine** scenes in the test suite and rework four (see Part E, P2). The public demo leads with four or five: the governed answer, the refusal, the evidence, the drafting | Data Architect, Fable |
| D5 | Seed **one engine at a time, Databricks first** (its automatic provisioning path is the one verified live) | Data Architect; Fable agrees on one at a time but would start with Snowflake |
| D10 | **Sample data first.** Connect mode stays designed and unbuilt | all four agree |
| D11 | Object names carry a **configurable prefix plus an ownership tag or comment on every object**, with a collision check. The literal prefix waits for the trademark check (G12) | GTM, Fable, Security |
| D15 | The owner names two or three data admins from their own network; 45 minutes, no pitch, the prototype on fake data. Add questions: what did your security team require last time, would you use Export SQL, what name would you want, what would make you click Remove access | GTM |
| D16 | ADBC spike: **later** | all |
| D17 | Next engine: **none** until the reference path is proven; a design partner's engine if one appears | all |
| D18 | dbt: **defer the build**; ask about dbt in the buyer interviews | Fable, Data Architect, GTM |

## Part B: gate decisions: the panel's recommendation, awaiting the owner

Each line ends with exactly what the owner would be approving.

| # | Decision | Panel recommendation | The owner would approve |
|---|---|---|---|
| G1 (D1) | Who certifies the reference definitions | **The owner**, through a reviewed pull request, plus a record naming the contract id, version and file digest. Add an outside reviewer before anything a customer sees | "I certify each named reference contract and version" (at R2, per contract) |
| G2 (D4) | Where the permanent reference warehouse lives | **Compass-owned demo accounts, decided now**, because the read-only identity, grants and naming depend on the account. Isolated schemas in the owner's accounts only for private, time-boxed parity runs. The owner creates the accounts and owns billing; the assistant cannot | "Compass-owned demo accounts, owner is the billing owner" |
| G3 (D6, D22) | Public demo scope and the "runs on" wording | The **public demo stays PostgreSQL-only until R5** (all agree). On the wording the panel split: GTM says publish narrow wording **now**, in the FAQ and roadmap only and never the hero ("sandbox validated, our own accounts, synthetic data, November 2026; not yet available on your data"); Fable and the earlier suggestion say wait for R5 | one of the two wordings, or "wait" |
| G4 (D7) | Pull requests #28 and #32 (uncertified drafts) | **Close both without merging, keep the branches.** The evidence is already in the program file, both cover the table the reference path replaces, and merging adds dead uncertified files. (Fable: merge neither yet; GTM and Data Architect did not object.) | "Close #28 and #32 without merging" |
| G5 (D8) | Admin sign-in methods | **SSO, a key you control, and export-SQL. No typed password in this phase** (all agree) | the list of offered methods |
| G6 (D9) | Provisioning a customer's warehouse (Connect mode) | **No approval now.** A security approval has to cover built, reviewed code. **Write the threat model now** (Fable). A separate security gate when Connect mode exists | nothing now |
| G7 (D12) | Spending caps | Keep the validated default (Snowflake one credit, smallest warehouse). An override may **lower** it freely; **raising** it needs a confirmation recorded with the consent. On Databricks, require the warehouse **by name** and bound the run with the cost and statement caps | the default values and the override rule |
| G8 (D13) | Consent record | **Yes**: the exact object list in clear text plus its hash, the code commit, the admin role used, the cap, and a paired teardown record with its outcome. Never a credential | the record's fields |
| G9 (D14) | Where the setup tool lives | A **separate entry point, identity and secret-store scope, in the same repository and release manifest** (Fable's lighter form of Security's "separate app") | the architecture split |
| G10 (D19) | Narrative spine and claims ladder | **The claims ladder binding from today**; the "Draft. Certify. Run." section published after R2, when a real drafted-then-certified reference contract exists | both statements |
| G11 (D20) | First buyer | **Data or analytics leader**, recorded as a hypothesis (no buyer research exists) | "First buyer: data or analytics leader" |
| G12 (D21) | Lead word and names | "Business definitions" first. A **person runs the trademark and domain check now**: it blocks the object prefix, the domain and the SEO. Retitle the roadmap card from "Auto ontology" to **"Drafted definitions"** (the narrative's own naming advice). Resolve the `kavachiq.com` contact address against the Compass name | the lead word, who does the check, the card title |
| G13 (D23) | Landing wording | Rename the card label "In pilot" to **"In internal testing"**; move "The model never writes SQL" into the subhead; add a proof example using the refusal already seen on the live pilot; **pull the Finance, RevOps, Engineering and Every-team persona pages until each claim maps to a test**, keep Data and Security rewritten to match the homepage; extend the CI copy guard to `Solutions.tsx` (see P3) | the exact label, subhead, and the persona plan |
| G14 (D24) | Publish the milestone 4 demo | **Hold** (all agree). When published: every screen labelled "draft, not certified", no host or account identifiers | the exact published content |
| G15 | Demo values for minimum group size, cost ceiling and review threshold (a threshold gate) | Set per scene profile, not globally; the owner approves the demo values at R3 | the demo threshold values |

## Part C: where the panel disagreed, and the assistant's read

| Topic | Views | Read |
|---|---|---|
| Engine order | Data Architect: Databricks first. Fable: Snowflake first | Databricks first: its automatic path is the one verified live; Snowflake setup is manual today |
| "Runs on" wording | GTM: now, narrow. Fable: after R5 | GTM's reasoning is the stronger of the two: the wording is exact and the site currently understates evidence. Owner's call |
| #32 | merge as draft / close both / merge neither | Close both, keep branches |
| Connect mode | approve later (assistant) / no advance approval (Security) / write the threat model now (Fable) | Security's rule plus Fable's action |
| Accounts | "not blocking until R5" (assistant) / decide now (Data Architect, Fable) | Decide now: it shapes R4 |
| Prefix | fixed `COMPASS_` / configurable plus tag | Configurable plus tag |

## Part D: decisions the panel found that were missing

1. **Parity comparison contract** (blocks R3 and R5): the canonical form for decimals, timestamps and month keys, the row order, and whether matching is exact. PostgreSQL's session time zone is unverified.
2. **Per-engine contract bindings** (blocks R2): Snowflake needs quoted lower-case physical names, Databricks a catalog and schema. One certified contract with bindings, or one contract per engine, changes what the owner certifies.
3. **Scene configuration profiles** (blocks R3): scenes 4, 5 and 9 each need different minimum group size, cost ceiling and review threshold (a gate, G15).
4. **Reference version and refresh policy** (blocks R4): what turns `ref_v1` into `ref_v2`, and whether certified contracts pin a data version.
5. **Demo-account operations:** credential custody and rotation, who pays, and a kill switch.
6. **Public-demo abuse limits** once traffic reaches paid engines.
7. **Draft isolation:** where drafts live in Git, and a CI test that a draft cannot execute.
8. **Licence of the synthetic reference data.**
9. **Buyer interviews:** about five before R6, since G11 and G12 are otherwise guesses.
10. **Connect-mode drafting egress:** the redaction approval permits model calls only to the pilot's Azure OpenAI deployment; a customer's data needs its own decision.

## Part E: corrections the panel found in existing work (work packets, none started)

**P1: Provisioning safety (the Security architect's top five; each claim checked in `warehouse_provisioning.py`).** *Built 2026-11-17 (owner: "Yes, fix P1"), tested against fakes only; the new existence checks, tag comments, sweep and permission check are not yet verified on a live account. See the notes after each item.*
1. *Fixed: plain `CREATE` with a tag comment, a preflight that stops with `name_taken` or `leftovers_found` before creating anything, teardown that drops only what this run created, and the Databricks schema flag set only after the schema was created.* Setup used `IF NOT EXISTS` throughout, so it can quietly adopt an existing object with the same name, replace an existing service user's key, run `CREATE OR REPLACE TABLE` over an existing table, and later **drop** what it did not create; the Databricks `_schema_created` flag is set even when the schema already existed. Fix: refuse when a name exists unless it carries this tool's run tag, tag everything created, drop only tagged objects.
2. *Fixed in part: `--sweep` finds and removes tagged leftovers; Databricks principals now carry a run id. A crash leaves no usable credential (the key or secret died with the process). A Snowflake user expiry was not added: its support is unverified.* A crash leaves a live credential and unlisted objects. Fix: a sweep that finds leftovers by tag, and an expiry or network limit on the Snowflake user (support unverified).
3. *Fixed: the warehouse must be named (no default, duplicates refused); teardown checks the `CAN_USE` entry is gone and reports it if not. It does not rewrite the warehouse's permissions, which would be riskier than reporting.* `pick_warehouse` defaulted to the **first** warehouse, and setup grants it `CAN_USE` although the docstring says it never edits the warehouse; teardown does not revoke it. Fix: require the name; revoke explicitly; correct the docstring.
4. *Fixed in part: `--admin-role` makes it configurable (default unchanged); narrower roles for Connect mode remain future work.* The admin role was hardcoded to `ACCOUNTADMIN`. Fine for a sandbox; Connect mode needs narrower roles.
5. *Fixed: the stale docstring is replaced by an explicit list of what is and is not verified live, and a failed teardown now lists the leftover objects by name.* Records overstated: the module's "not run against a live account" is stale, the Snowflake automatic path is **not** verified live, and on a failed teardown the runner prints name prefixes though the design promises a named list.

**P2: Scenes.** Verified in the code: scene 5 ("revenue for each individual order") is refused by the compiler for grouping on a grain key, **before** any cost estimate, so it would refuse for the wrong reason, and a ceiling in MiB has no meaning on PostgreSQL, whose gateway compares planner cost (cost units differ per engine). Scene 4 relies on `ANALYTICS_MIN_COHORT`, which defaults to off and silently drops small groups (`HAVING COUNT(*) >= k`), so "suppressed, with the rule named" is unverified, and the differencing attack (a region total minus the visible categories) is not covered. Scene 9's trigger is undefined. Scene 10 conflicts with a permanent read-only identity and needs a per-demo identity that is created then revoked. Also confirm at R3 that all scenes compile under the single-dataset limit.

**P3: Landing page.** Verified: the Finance page says "Every number ships with the rows it came from" while the homepage says "Result rows are not stored in the audit record"; the RevOps page says "across CRM, warehouse, and product" (no CRM connector, no joins); "answered in seconds", "recognition rules applied automatically" and Engineering's "Trace ownership, lineage, and incidents" are unevidenced. The CI copy guard only searches the built homepage, so persona pages are not covered.

**P4: Threat model for Connect mode** (a document, no build). *Written 2026-11-17 (owner: "do G6"): `docs/security/CONNECT-MODE-threat-model.md`, a draft that is not reviewed and approves nothing.*

## Suggested order

P1 and P3 first, because they fix real exposures (a teardown that can destroy objects it did not create; claims on the live site that contradict each other). Then G1, G2 and G4 unlock R2 and the account work; P2 lands with R3.

## Not verified

No buyer research, no vendor cost or trial terms, no Snowflake user-expiry support, and no contents of PRs #28 and #32 beyond local branches. Fable read less of the repository than the Opus agents and its picks 7, 23 and 24 rested on the assistant's description.

## Owner decisions (2026-11-17)

The owner chose "Go one by one" and answered each gate in turn. Answers are the options as selected; the full record is in the program file.

| # | Decision | Owner's answer |
|---|---|---|
| G1 | Who certifies | Me, via reviewed PR |
| G2 | Where the reference warehouse lives | Compass-owned demo accounts |
| G3 | "Runs on" wording | **Wait until R5** (the panel's other option; the narrow-wording-now option was not chosen) |
| G4 | PRs #28 and #32 | Close both, keep branches |
| G5 | Admin sign-in | SSO, a key you control, export-SQL |
| G6 | Connect mode | No approval now; write the threat model now |
| G7 | Spending caps | Default, override can lower freely |
| G8 | Consent record | Full list plus hash, never a credential |
| G9 | Setup tool location | Separate entry point, same repo |
| G10 | Spine and ladder | Ladder now; spine after R2 |
| G11 | First buyer | Data or analytics leader |
| G12 | Names | All three: lead word, check, card title |
| G13 | Landing changes | All of it |
| G14 | Milestone 4 demo | Hold |
| G15 | Demo thresholds | Per-scene profiles; approve values at R3 |

Still open for the owner: who runs the trademark and domain check (G12), creating the demo accounts and owning the billing (G2), and approving the actual demo threshold values at R3 (G15).

**Added 2026-11-17:** the threat model (G6) raised five further decisions. The first is decided: **Connect-mode admin setup is product-run after the admin signs in and approves, with the exported script as a fallback** (owner: "Product-run, script as fallback", which superseded an earlier "Customer-side only" the same day; program file `connect_mode_admin_setup_product_run`). The agreed runtime direction is identity federation, not yet verified. Still open: model egress for customer data, the default minimum group size, who reviews and the penetration-test budget, and a first design partner's profile.
