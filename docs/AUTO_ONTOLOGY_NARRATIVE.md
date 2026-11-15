# Auto ontology: narrative plan

Status: **Proposed. Not approved. No public copy changes in this document.** Written 2026-11-14, updated the same day with the owner's direction to reference NVIDIA and others as trend evidence for the owner, the CEO, Founder, CPO and CMO roles.
Companion to `docs/AUTO_ONTOLOGY_DESIGN.md` (what we build) and `docs/LANDING_MESSAGING_REVIEW.md` (what the site says today).
Every claim below is tied to the evidence that must exist before it is made. Nothing here is a claim the product can make today.

## The spine

> Agents need context. Context is not the same as truth. Compass lets AI draft your business definitions, lets your people
> certify them, and only then lets anything run.

The three verbs are the whole story, and each maps to something we can show:

| Verb | What it means | What proves it |
|---|---|---|
| **Draft** | AI reads the schema and proposes definitions, metrics and joins | A draft pull request from a real schema, with provenance (model, prompt version, schema fingerprint) |
| **Certify** | Named people review and approve in Git; the gate is human | The diff view, the certifier recorded in Git history, no auto-merge path in code |
| **Run** | Only certified definitions execute, through a compiler the model never writes | Refusal on a draft, the policy decision and fingerprint on every answer |

## Why this story and not another

- The "context for agents" layer is filling up with funded players (Databricks Genie Ontology, WisdomAI, NVIDIA Auto Ontology,
  Microsoft Fabric, and as an adjacent layer DigitalOcean Agent Data Services). Sources are vendor material (see the messaging
  review). We cannot win by being another place where context lives.
- Most of those pitches end at "the system learns your context". Gartner-style survey findings in the messaging review say the
  open buyer problem is trust, governance and control. The unclaimed ground is the part after the AI drafts: who approves, what
  is allowed to run, and what proof remains.
- The DigitalOcean newsletter review (2026-11-14) is a useful tell: even a neutral reviewer concluded that storing what agents
  learn does not make answers better without feedback, evaluation, controlled changes and validation. Our draft, validate,
  certify loop is that missing control, so the narrative is "learning proposes, people certify".
- Our edge is the combination (no model-written SQL, designed refusals, separation of duties, a tamper-evident evidence trail),
  not any one feature. We have not verified that competitors lack comparable gates, so we never say "no one else does this".

## Audience (open decision)

The same story reads differently to each buyer. Recommended first buyer: **the data or analytics leader who owns the semantic
layer and is asked by the business to "turn on AI"**. They feel both pains: authoring definitions by hand is slow, and
ungoverned AI answers are a risk they own. Security and compliance are the second audience (they care about the redaction rule
and the evidence trail). Decision needed from the CEO; see the end.

## Claims ladder

Claims unlock only as the matching evidence exists. Until then the site keeps the honest "not yet" already in the FAQ (PR #12).

| Stage | We may say | Evidence required first | Must still not say |
|---|---|---|---|
| Today | "Definitions are certified by people in Git; only certified definitions run." "Auto ontology is on the roadmap: AI drafts, people certify." | Already true in code and tests | That Compass builds your ontology automatically; any customer or accuracy number |
| After M1 (contract and validators, fake model) | Nothing new publicly. Internal demo only. | Validator tests incl. a drafted metric that fails the dry-run compile and is dropped | That any model drafts anything |
| After M2 (introspection, profiling, redaction) | "Samples shown to the model follow a published redaction rule." (the rule as approved 2026-11-14) | PII-bait and small-cohort tests pass; samples proven never persisted or logged | That it has run on a customer or a live system |
| After M3 (real-model drafter, evaluation report) | "On our reference dataset, AI drafted X% of the certified definitions and Y% of its drafts were wrong or dropped." State the dataset and say it is one dataset. | An evaluation report we ran, against thresholds the owner approved beforehand | A general accuracy claim; "works on any schema"; a comparison to competitors |
| After M4 (live pilot warehouse, draft PR) | "We pointed it at a real Postgres warehouse and it opened a draft pull request for review; here it is." A screenshot or recorded demo. | The PR exists, was reviewed by a person, and the owner approves publishing it | "In production at customers"; time saved, unless measured |

If M3 misses the approved thresholds, the story narrows to "AI-assisted authoring" and the claims drop one row. We decide that
rule now so the result cannot be spun afterwards.

## Words

- Buyers say "business definitions", "semantic layer", "context" and "metrics" more than "ontology". Proposal: lead public copy
  with **business definitions**; use **ontology** in the subhead and in search-facing text, defined once ("the map of your
  business concepts and how they connect"); never use "knowledge graph" for this feature.
- Use **draft** and **certified** consistently. Avoid "autonomous", "self-learning" and "self-improving": they imply the opposite
  of the gate.
- "Agentic analytics you can trust" stays the headline unless the CMO answer in the messaging review changes it. The ontology
  story is the proof line under it, not a second headline.

## Naming proposal (owner preference stated 2026-11-14; proposal, not a decision)

The owner likes "Agent Data Service", "Agentic Data Analytics" and "Auto Ontology". Checked against what is already in use:

| Term | Proposal | Why |
|---|---|---|
| **Agentic Data Analytics** | Use as the category descriptor under the product name "Compass" | Descriptive, close to existing market language, and the name of this repository. |
| **Auto ontology** | Use as plain description (subhead, search text, docs), not as a feature or product name | "Auto Ontology" is the name of NVIDIA's open-source project (`NVIDIA/auto-ontology`). We plan to cite NVIDIA as a trend reference, so a Compass feature of the same name risks confusion or an implied affiliation. Feature name instead: **Drafted definitions** (or **Certified Ontology**). |
| **Agent Data Service(s)** | Do not use as a name | It is the name of DigitalOcean's product (announced 2026-10-12) and would be mistaken for it. It also clashes with our internal `ADS-###` task IDs. For the idea behind it (agents call Compass through typed tools and MCP) use **Compass for Agents**, or "Agent access" as a section label. |

Proposed structure: **Compass** (product) / **Agentic Data Analytics** (what it is) / **Compass for Agents** (how agents use it) /
**Drafted definitions**, with "auto ontology" as the descriptor (the draft, certify, run feature).

Not done and needed before any of these is used as a name on the site: a trademark and domain check by someone qualified (I
cannot clear trademarks), and the CMO's open brand and naming answer in `docs/LANDING_MESSAGING_REVIEW.md`. No site copy is
changed by this proposal.

## Proof assets to build (cheapest first)

1. **A draft-to-certified walkthrough** on the demo dataset `sales-demo@v1`: the draft diff, a dropped candidate with its reason,
   the certifying commit, then the same question refused before certification and answered after. This is the central demo.
2. **The evaluation report** from M3, published as a short page with the method, the dataset limits and the failures, not only
   the wins.
3. **The redaction rule as a one-page explainer** ("what the model sees of your data"), because security readers will ask first.
4. **A refusal clip**: ask a question that only a drafted, uncertified metric can answer; Compass declines and says why.

## Objections to answer

| Objection | Answer, if the evidence supports it |
|---|---|
| "Why not just let it learn from usage?" | Usage shows what people asked, not what is right. Learned context can be wrong and no one signed for it. We can propose from usage later, but a person still certifies. |
| "Isn't human certification a bottleneck?" | It is the point, and the drafter is there to shrink the work. We will measure drafts accepted without edit versus edited versus rejected and report it, not assert it. |
| "What does the AI see of my data?" | Only what the approved redaction rule allows, sent only to the pilot's own Azure OpenAI deployment, never persisted or logged. |
| "Can the model write bad SQL from the ontology?" | It never writes SQL. Every drafted metric must compile and dry-run before it is even shown to a reviewer. |
| "Is this just Databricks or Fabric?" | Do not argue the comparison on the site. Internally: they draft or learn context inside their platform; our pitch is a certification gate and a deterministic execution path that does not depend on one warehouse. Unverified beyond vendor material. |

## Referencing the trend publicly (owner direction, 2026-11-14)

The owner wants NVIDIA and others cited for attraction. Recommended way to do it:

**The line.** Cite them as evidence that the market is converging on a pattern, not as a comparison or an endorsement:

> Draft with AI, certify with people. The same pattern is now appearing across the data industry. Compass applies it to the part
> that comes after: what is allowed to run.

**Rules for any public reference**
1. **Trend, not versus.** "The industry is moving to AI-drafted, human-certified context" is safe. "Better than", "unlike" or any
   feature-by-feature table is not, because we have not tried these products hands-on.
2. **Primary source, dated, linked.** Link the vendor's own page or repository, and say "as of <month year>". These products
   change fast. Re-check every link before each copy release.
3. **Their words, short and attributed.** Describe what they say they built, with "announced" or "describes". Do not repeat their
   benchmark or speed numbers as facts (NVIDIA's "90% faster" figure is the vendor's own and the public page does not give the
   method); if cited at all, label it "NVIDIA reports".
4. **No logos, no implied relationship.** Names in plain text only. No partner, integration, "works with" or "built on" wording
   unless that is true and approved. Use the name only to refer to the product.
5. **One neutral sentence of difference, about us only.** Say what Compass does, never what they lack.
6. **Place it away from the headline.** A short "Part of a wider shift" strip or FAQ, with sources linked.

**What the references can and cannot claim about us.** The gate and the human certifier are now a shared pattern, so they are
not our differentiator on their own. What we can still point to, from our own code and tests: the model never writes SQL, a
deterministic compiler builds each query, designed refusals, separation of duties, and a replayable evidence trail. Do not
imply that others lack these (unverified; NVIDIA's own repository description also says it enables "deterministic query
generation").

**Sources checked 2026-11-14** (vendor and press material, not hands-on trials)
- NVIDIA Auto Ontology: open source under Apache-2.0, a framework that builds a semantic layer from enterprise metadata.
  Per secondary write-ups, domain experts review definitions and certify metrics before publication, and MCP tools are
  read-only. It is described as compatible with Apache Ossie. Primary: https://github.com/NVIDIA/auto-ontology . No NVIDIA blog
  post was found in search; confirm before citing a launch date.
- Databricks Genie Ontology: the Databricks blog (https://databricks.com/blog/announcing-ga-genie-ontology) announces general
  availability, while the GCP release notes still show public preview. Check the docs for the cloud before saying "GA". It has
  **two kinds of context**: curated and certified (metric views, domains, pages) and inferred from SQL, dashboards and usage.
  Docs: https://docs.databricks.com/aws/en/genie/genie-ontology . This corrects our earlier note that it was early access.
- WisdomAI: "Enterprise Context Layer" is a term used across launches (platform launch reported 2026-03-05), now described as the
  "Adaptive Context Engine". A benchmark dated 2026-10-15 is company-published. Product page: https://wisdom.ai/product/context .
- Apache Ossie (Incubating), formerly Open Semantic Interchange: vendor-neutral YAML format for metrics, dimensions and
  relationships; accepted into the Apache Incubator around mid-2026; incubation does not guarantee graduation.
  https://ossie.apache.org/updates/ossie-enters-apache-incubator/ . We already have an import/export boundary (ADR-ADS-008).
- DigitalOcean Agent Data Services: private preview, adjacent layer. Its docs do not mention a semantic layer or metrics, and
  state that row- and column-level controls are not available. Not a trend reference for this pattern; keep it internal.

**Two consequences for our own copy**
- Databricks offers certified **and** learned context, so the PR #12 FAQ "Why certified definitions instead of context learned
  from usage?" can read as arguing against a straw man. Reword it before merge to: usage can propose, a person certifies.
- NVIDIA is open source, Apache-2.0 and Ossie-compatible, so it is also a possible input or complement (through the Ossie
  boundary), not only a peer. Worth a technical look before we build a parallel path for the same job.

## Learning from DigitalOcean Agent Data Services (internal, owner direction 2026-11-14)

Agent Data Services is a different layer (see the sources above), but its published design and docs hold lessons for us. Each
lesson ends with what it would change; none is approved or built, and these are inputs to the design, not decisions. Basis: the
DigitalOcean blog and docs of 2026-10-12 and one newsletter review. Private preview, no hands-on trial.

| What they do | What we can learn | Possible change for us |
|---|---|---|
| Process once at ingestion and store the outputs with lineage back to the source asset | Expensive model work should not repeat on every read | Cache each drafter run by schema fingerprint and prompt version; re-draft only when the fingerprint changes. The design already stamps provenance; add the cache rule to milestone 3. |
| Assemble agent sessions (tool calls, events, histories) into queryable data, with configured redaction | Usage traces are a real input for improving context | Our question log could feed ADS-051 to ADS-053 proposals and later the drafter as evidence. Needs its own privacy design first; keep it out of milestones 1 to 4. |
| The newsletter notes that redaction before embedding helps, but anything embedded before removal can still leak meaning | Redact before data leaves the process, not after | Already our rule (redact before the model call, samples never persisted or logged). Add a test that proves no raw value reaches the model client or any log. |
| Separate scopes for managing resources and for querying; a token that can change query configuration can also run queries | Scope separation is easy to get subtly wrong | Use distinct credentials for the drafter: read-only introspection role, model call, and the credential that opens the draft pull request. None can do another's job. |
| A limits page that states plainly what is missing (no row-level controls, no MCP, at-least-once delivery, no SLA, no custom functions) | Published limits build more trust than adjectives, and exposed a gap between their blog ("governed catalog") and their docs | Publish a "what Compass does not do yet" page and keep it in step with the product. Keep public copy no stronger than our own docs; extend the existing CI copy guards to this. |
| At-least-once delivery, so consumers must tolerate duplicates | Make repeat runs safe | Drafter re-runs already produce a diff, never an overwrite; add an idempotency test (same schema and same inputs give the same draft). |
| Query layer is read-only; underlying storage is not directly reachable | Narrow the surface agents touch | Matches our read-only role and MCP read tools. Keep the drafter's database access to the read-only role only. |
| "Agent Learning Services": observe, detect problems, evaluate proposed changes, simulate | The loop is observe, propose, evaluate, then change | Same shape as our proposals (always `proposed`) plus evaluation. Our difference is the human certification step before anything changes execution. |

**What not to copy.** A "governed by default" claim before the controls exist. Their docs say per-dataset policies and row- and
column-level controls are not available; we should hold our own claims to what our tests show.

**Naming note.** Their product is "ADS". Our program uses `ADS-###` task IDs. Avoid public use of "ADS" for either.

## Site change plan (proposed; nothing here is built or deployed)

Page today (`apps/landing/src/pages/Landing.tsx`, `Solutions.tsx`, `index.html`): hero "Agentic analytics you can trust", trust strip,
value props, "From question to defensible answer" (how it works), personas, "Controls you can inspect", FAQ, roadmap, book-demo CTA.
Each change below is a separate, reviewable copy PR. Nothing is deployed without the owner's go-ahead in that task.

| # | Change | Where | Unlocked by | Evidence to link |
|---|---|---|---|---|
| 0 | Merge PR #12 (honest "shows the SQL" fix, auto-ontology "not yet" FAQ, FAQ now "usage can propose, a person certifies") | FAQ, hero support line, Solutions | Owner review of PR #12 | Already in the PR |
| 1 | **Spine section** "Draft. Certify. Run." between "How it works" and "Controls". Three short blocks, one per verb. The "Draft" block is labelled **Roadmap** until stage M4 is reached; "Certify" and "Run" describe what ships today | New section in `Landing.tsx` | Owner adopts the spine; no new capability needed because Draft is labelled roadmap | Code and tests for certify and run (already evidenced) |
| 2 | **Trend strip** "Part of a wider shift": one sentence plus linked, dated primary sources (NVIDIA Auto Ontology, Databricks Genie Ontology, Apache Ossie), following the reference rules above. Below the fold, not in the hero | New small section above the FAQ | Owner confirms the reference rules; links re-checked on the day | Primary links listed above |
| 3 | **Roadmap card** "Auto ontology" in the existing roadmap grid, stage "Planned" until M3, then "In pilot". One sentence: AI drafts, a person certifies, nothing runs until certified | Roadmap `items` | Today | The design doc |

**Row 3 done 2026-11-15** (owner: "Update the roadmap card"): card "Auto ontology", stage "In pilot", one sentence plus "Internal pilot on synthetic data only; not available to customers." The FAQ line now says "in an internal pilot on synthetic data, not shipped", so the two agree. It states no number and no customer claim, and does not use the M4 "opened a draft pull request" line, which stays locked until a person has reviewed the draft PR and the owner approves publishing it. Copy guard added to `ci-landing.yml`. Not deployed: deploy is manual.
| 4 | **"What the model sees of your data"** explainer (one page or a FAQ answer) stating the approved redaction rule in plain words | New FAQ item, later its own page | M2 done: redaction tests pass | The rule approved 2026-11-14; the tests |
| 5 | **"What Compass does not do yet"** limits page, kept in step with the product and linked from the footer and the trust section | New page and footer link | Owner approves the wording; no engineering needed | Program file and handoff doc |
| 6 | **Draft-to-certified walkthrough** (screenshots or a short clip) on `sales-demo@v1`: draft diff, a dropped candidate with its reason, the certifying commit, a refusal before and an answer after | New section or `/how-it-works` page | M4: a real draft PR exists and the owner approves publishing it | The pull request itself |
| 7 | **Evaluation numbers**, one dataset, method stated, failures included | Linked report page, one line in the walkthrough | M3 results against thresholds approved beforehand | The evaluation report we ran |
| 8 | Hero subhead: add "AI drafts your definitions, your people certify them" **only** when row 7 or 6 is live. Until then the subhead stays as in PR #12 | Hero | M3 or M4 | As above |

**Not changed.** The headline "Agentic analytics you can trust". Persona pages stay as in PR #12. No competitor names outside the
trend strip, no logos, no comparison table, no customer or accuracy claims, and no "automatically builds your ontology" claim
before M4.

**Guards.** Extend the existing CI copy guards (which already block "SOC 2 Type II", "GDPR-ready" and "aggregateRating") with
phrases we must not use until their row unlocks: "automatically builds your ontology", "self-learning", "autonomous", and any
numeric accuracy claim. Add a check that each external reference link in the trend strip responds, run on the release PR and not
on every build.

**SEO and structured data.** `index.html` and the `useSEO` FAQ injection should match the page text exactly, including the new
FAQ wording, so the structured data never claims more than the visible copy. Search-facing text can use "ontology" and "semantic
layer"; the visible headline keeps "business definitions".

**Sequence.** 0 now. 3 and 5 can go in the next copy PR because they need no new capability. 1 and 2 follow once the owner adopts
the spine and the reference rules. 4 after M2. 6, 7 and 8 only after M3 and M4. Each step is its own PR, reviewed by the owner
before merge, and deployed only on the owner's explicit request.

## How we will know the narrative works

- Before launch: the owner can defend each claim in the ladder to a skeptical customer, in their own words.
- Internal bar for the demo: a reviewer unfamiliar with Compass can follow draft, certify, run in one sitting without help.
- Weak signals we can collect honestly with no customers: pilot conversations asked "what does the AI see", "who approves", or
  "what if it is wrong" (the story is hitting the right fear), versus asking only about connectors and price.
- Not available yet and not to be invented: customer counts, time saved, accuracy beyond the reference dataset, ROI.

## Decisions needed (the owner's own words; none recorded)

| Decision | Owner role | Recommendation |
|---|---|---|
| Adopt the spine and the claims ladder | CEO and Founder | Yes |
| First buyer: data leader, security, or business-unit head | CEO | Data or analytics leader |
| Lead word: "business definitions" with "ontology" in the subhead | CMO | Yes |
| Naming: Agentic Data Analytics as descriptor, "Drafted definitions" as feature name, "Compass for Agents" for agent access; do not use "Auto Ontology" or "Agent Data Service" as names; trademark and domain check first | CMO and CEO | Yes |
| Headline leads with certified rather than learned context | CMO | Yes |
| Name NVIDIA and others in public copy, under the rules in 'Referencing the trend publicly' | CEO | Yes, as trend references only, primary-linked and dated, no comparisons |
| Evaluation thresholds for M3 (also a gate in the design doc) | Owner | Set before M3 runs |
| Publish the M4 demo | Owner | Only after review of what it shows |

Also not done: buyer interviews, hands-on trials of competing products, and any willingness-to-pay work. Until some of that
exists this plan is a hypothesis about what buyers care about, built from desk research.
