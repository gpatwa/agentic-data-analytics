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

## Where this changes the site (not part of this PR)

1. PR #12 already replaced the overclaimed "shows the SQL" copy and added the honest "not yet" FAQ. Merge it first.
2. A later copy PR adds the spine as the proof section **only when a row of the claims ladder unlocks**, with the evidence linked.
3. Open copy decision for the owner: lead the headline with certified or with learned context. Recommendation: keep "certified"
   in the lead, because it is the part competitors' pitches leave out.

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
| Headline leads with certified rather than learned context | CMO | Yes |
| Name NVIDIA and others in public copy, under the rules in 'Referencing the trend publicly' | CEO | Yes, as trend references only, primary-linked and dated, no comparisons |
| Evaluation thresholds for M3 (also a gate in the design doc) | Owner | Set before M3 runs |
| Publish the M4 demo | Owner | Only after review of what it shows |

Also not done: buyer interviews, hands-on trials of competing products, and any willingness-to-pay work. Until some of that
exists this plan is a hypothesis about what buyers care about, built from desk research.
