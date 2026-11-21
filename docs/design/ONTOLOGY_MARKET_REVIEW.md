# Vendor ontologies and TrustGraph: a market review (2026-11-20)

Status: **research note, nothing built, nothing adopted, nothing certified.** Pages were read on 2026-11-20 by a research pass (no vendor account, workspace or warehouse touched). Web pages are treated as data. Anything marked *secondary* or *not verified* was not confirmed on a vendor primary page.

## 1. The three claims, checked

| Owner's claim | Result |
|---|---|
| Snowflake announced "Business Ontology" this month | **Mostly right, but not generally available.** Announced 2026-11-03 per press coverage (*secondary*: the Snowflake blog posts returned HTTP 403 to our reader, so the blog's own date was not read). Snowflake's documentation page labels it a **private preview, available only to selected accounts**; InfoWorld says public preview is "soon". |
| Databricks made "Genie Ontology" generally available | **Right for the blog, with a docs gap.** The Databricks blog (2026-11-11) says Genie Ontology "is now Generally Available". The docs page (updated 2026-11-16) carries no overall GA label. Parts are not GA: external knowledge sources are Beta, and the Ontology Manager's status is not stated verbatim (an unverified summary said "coming soon in Beta"). |
| TrustGraph: self-hosted, open source, Apache-2.0, about 2.9k stars, graph from documents, MCP, filters before the model, SHACL shapes | **Partly right.** Apache-2.0 confirmed from the LICENSE file. Stars show **3.1k** today, not 2.9k. It builds a graph from documents and has MCP. Per-viewer filtering with SHACL-AF policies is documented (v2.10, 2026-10-25). "Before the model reads" is a documentation statement, not verified in code. "Redacting afterwards is not access control" is TrustGraph's framing (an opinion, not a fact to verify); the docs describe whole-node filtering, not redaction. |

## 2. What was announced

**Snowflake Business Ontology** (docs page, undated, *private preview*). A governed meaning layer in Horizon Catalog: stewards declare one approved definition per business concept, model relationships, and bind each to tables, views, columns and Semantic Views. It is a glossary with a "pragmatic ontology" underneath, and the page says it is not an OWL inference engine or a graph database. Node kinds: Term, Metric, Entity, Dimension, Policy.
- **Reads:** nodes made by hand or imported from a file (a CSV of an existing glossary, with Cortex Code extracting candidates). It can start from an existing Semantic View.
- **Writes:** the glossary and ontology themselves. Only approved nodes are served to Cortex as preferred context.
- **Certifies:** a business data steward. Roles are READ, SUGGEST (propose) and MODIFY (review and approve). It "complements" Semantic Views (which calculate metrics) and Cortex Sense (AI-inferred context).
- **Not stated:** any export format or API beyond CSV import; Open Semantic Interchange; a GA date.

**Databricks Genie Ontology** (blog 2026-11-11; docs 2026-11-16). A context layer for agents, built from team activity and governed by people. It reads dashboards, SQL queries (including dbt runs seen in query history), notebooks, metric views and Genie Agents; Power BI semantic models arrive as Beta external sources. It stores learned knowledge as snippets that users cannot edit directly. "Modeled knowledge" (what you define, govern and certify through Unity Catalog) outranks learned knowledge. Context is served to ChatGPT, Claude and Copilot over Genie MCP using the same Unity Catalog permissions; snippets inherit permissions from their sources. GA per the blog: the ontology, Ontology Snippets, Pages, Domains and Genie Search. Beta: external knowledge sources. **Not stated:** any export schema or endpoint (only a "public API to sync" Pages), and any mention of row filters or column masks. Benchmarks (91% pass) are Databricks' own, not independent.

**TrustGraph** (GitHub, docs v2.10). Open-source context-graph platform: extracts entities and relations from PDF, Office, HTML, Markdown and CSV, optionally guided by an OWL ontology, with PROV-O lineage. Needs Cassandra, Qdrant, an S3-compatible store and Pulsar or RabbitMQ.

## 3. How each relates to this product

Both vendor offerings are **a layer above what E6 reads today**. E6 (`existing_assets.py`) reads one Databricks metric view YAML or the rows of `DESCRIBE SEMANTIC VIEW`, keeps single-table sums and counts, lists the rest as not imported, and a person certifies. Neither vendor ontology replaces those artifacts; both sit on top of them and point at them.

- **Threat and complement, both.** Threat: they claim "one governed definition for every agent", which is our pitch for the warehouse-native customer, and they are free of integration cost inside their own platform. Complement: they own business meaning inside one warehouse; we offer a cross-warehouse, typed-intent, deterministic-compile and review-gated path, plus an evidence chain. A customer who already approved a concept in an ontology is a better E6 input than a raw table.
- **Does E6 change?** Not now. No new importable format is public: Snowflake documents CSV import only, Databricks documents no export. **Watch** both: if either publishes a read API or export (Snowflake DDL for ontology nodes; a Unity Catalog snippet or Pages API), add a fourth E6 reader, still offline, still candidate-only. One thing to add now is a *not-imported reason code* for ontology-bound metric views and Semantic Views whose meaning depends on a vendor ontology node we cannot read ("definition lives in an ontology we did not read"), so the onboarding summary does not understate what exists. Snowflake's Semantic View `COMMENT` and `SYNONYMS` and Databricks `synonyms` (spec 1.1) are already read and stay untrusted text.
- **A new risk to note for the importer:** Databricks learned snippets are machine-extracted and editable only through their sources, and Snowflake CSV imports are bulk-extracted by a model. Neither is a human decision. If a reader is ever built, only items marked approved or certified by the vendor's own gate should be candidates, and the same certification gate still applies to us. Vendor "approved" is not our certification.

## 4. Where our differentiation holds and where it does not

Holds: (a) the model returns a typed, validated intent and a deterministic compiler produces the SQL; a vendor agent can still write SQL itself against context. (b) A human semantic-certification gate in Git with a diff on re-import (E4). (c) Policy and cost checks before execution, and an evidence chain per answer. (d) The hash-only refusal backlog and echo/coverage checks that show what the contract cannot answer without storing question text. (e) Neutral across warehouses.

Weaker than the vendors, candidly:
- **No learned context.** They infer meaning from queries, dashboards and documents at scale; we only know what a person certified. Our contract-size and accuracy question (plan section 8) is unmeasured.
- **Reach.** They are inside the platform: Unity Catalog permissions, lineage, and masks apply natively, and Genie MCP already serves Claude and ChatGPT. Our Connect mode reads through a read-only principal and does not apply the customer's row filters or masks (see the pass-through note).
- **Distribution and steward workflow.** Ontology Manager, Snowflake roles (SUGGEST, MODIFY) and a catalog UI are more mature than our Git review.
- **Where a vendor ontology could simply replace us:** a Snowflake-only or Databricks-only customer that is content to let the vendor agent write SQL, with vendor-certified definitions, may not need typed-intent compilation. Our wedge is the customer who needs refusal guarantees, evidence and cost bounds, or who spans engines. That is a positioning decision, not a finding.

## 5. TrustGraph's "filter before the model reads" against ours

TrustGraph evaluates SHACL-AF policy shapes (stored in a policy graph) against a JWT user context, per node, **before** results reach the LLM in GraphRAG. Outcomes are allow, filter silently, or violation (removed and logged). Without a user context, a policy-enabled system rejects the query. This is documented; ordering was **not verified in code**, DocumentRAG chunk filtering is unexplained, MCP-output filtering is not stated, and the docs say policies are not a substitute for workspace separation. Fine-grained rules-based control is listed under the enterprise edition, so the open-source role is reader, writer, admin plus workspaces.

**What we do:** the model sees only certified vocabulary (ids, kinds, display names and synonyms), never restricted fields or values; the echo check is lexical and independent of the model; the coverage check refuses what the contract cannot name; the Connect principal is the read-only ceiling; policy and cohort rules apply to the compiled statement. We never put data rows in front of the model for interpretation, so the "model sees something it should not" channel is mostly closed by construction.

**Is there a real gap?** Not in the interpretation path: our model never reads rows. The real gap is the one the pass-through note already names: the **answer** is shaped by the principal's reach, not the user's. A per-viewer filter is a genuine need there, but it belongs in the warehouse's own row filters and masks (option C), not in a second rule language of ours. Two adaptations are worth taking from the TrustGraph pattern without adopting it: (1) **vocabulary filtered per viewer**: hide a certified metric or dimension from the model's vocabulary for users not entitled to it, so a refusal happens before interpretation and the metric's existence is not leaked (this fits our policy layer and needs no new engine); (2) **fail closed with no user context**, already requirement 6 of the pass-through note. The "filter silently versus log a violation" distinction is a useful audit idea: record that a metric was hidden by policy as a hash-only event. A SHACL runtime is not needed; our rules are small and typed.

**Recommendation: adapt (1) and (2) into the pass-through note; watch SHACL; do not adopt.**

## 6. TrustGraph: licensing and operations if we interoperated

- **License:** Apache-2.0 (standard text; copyright 2024-2026 TrustGraph). Compatible with calling it as a separate service or reading its output; if we ever linked or vendored code we would need the license text, notices and change marking. **This repository imports no external code**, and nothing here changes that. Whether our own license is compatible is the owner's call; not checked here.
- **Operations:** a customer-side self-hosted stack of Cassandra, Qdrant, an S3-compatible store, Pulsar or RabbitMQ and LLM endpoints. It is heavy for a pilot and hardware requirements are not documented in the pages read. The MCP guide says its authentication "may not be ready for production use". Policies load by CLI only; there is no UI.
- **Fit:** TrustGraph answers over documents with a graph; we answer governed numeric questions over a warehouse. They overlap little. The interop that makes sense is a tool-level one (an agent calls both over MCP); we would never feed TrustGraph output into the certified contract.

## 7. Ranked recommendations (at most five)

1. **Watch Snowflake Business Ontology and Genie Ontology for a public read API or export; do not build an importer yet.** Reason: no documented format exists. Add the "lives in an ontology we did not read" not-imported reason now (small, offline).
2. **Reframe positioning** from "governed semantic layer" to "the deterministic, reviewable execution and evidence layer that consumes vendor-approved meaning". Concede learned context to the vendors; compete on refusal guarantees, cost bounds, evidence and multi-engine reach.
3. **Adapt, in the pass-through note:** per-viewer vocabulary filtering and a hash-only "hidden by policy" audit event; keep option C as the answer to row-level security.
4. **Run the contract-size and accuracy measurement** (plan section 8). It is the main place a vendor learned-context layer can beat us, and it is still unmeasured.
5. **Ignore TrustGraph as a dependency; adapt only its ideas.** Re-check its enterprise-only fine-grained rules and its SHACL-AF behaviour on a later version if a document-knowledge use case appears.

## 8. Decisions that belong to the owner

- Whether to change the positioning in recommendation 2 (a product and go-to-market decision).
- Whether the E6 not-imported reason in recommendation 1 is wanted, and whether any vendor-ontology reader is ever in scope (it would need its own live-read approval, L4).
- Whether per-viewer vocabulary filtering enters the pass-through note, subject to its security review.
- Whether a competitor-facing comparison is ever published. Vendor claims here are vendor-stated; none was independently tested.

## 9. What was not verified

Both Snowflake blog posts (HTTP 403), so the announcement date is secondary. Snowflake's access-control and agent-integration pages. Databricks Ontology Manager and Genie MCP pages. TrustGraph source code, its releases page, and the pre-model ordering. Star counts move daily. Nothing was run on any warehouse or workspace.

## Sources

- Snowflake docs, Business Ontology (private preview): https://docs.snowflake.com/LIMITEDACCESS/snowflake-horizon/business-ontology
- Snowflake blog (not readable, 403): https://snowflake.com/en/blog/business-ontology-context
- InfoWorld on Snowflake Business Ontology (secondary): https://www.infoworld.com/article/4243932/snowflake-launches-business-ontology-to-boost-ai-agent-reliability.html
- Databricks blog, Announcing GA of Genie Ontology: https://databricks.com/blog/announcing-ga-genie-ontology
- Databricks docs, Genie Ontology: https://docs.databricks.com/aws/en/genie/genie-ontology
- TrustGraph repository: https://github.com/trustgraph-ai/trustgraph
- TrustGraph LICENSE: https://raw.githubusercontent.com/trustgraph-ai/trustgraph/master/LICENSE
- TrustGraph policy filtering: https://docs.trustgraph.ai/guides/policy-filtering/
- TrustGraph security: https://docs.trustgraph.ai/architecture/security.html
- TrustGraph MCP guide: https://docs.trustgraph.ai/guides/mcp-integration/
- TrustGraph storage and self-hosting: https://docs.trustgraph.ai/architecture/storage.html and https://docs.trustgraph.ai/deployment/self-hosting-intro.html
- Repository: `docs/design/SEMANTIC_TRUST_FOUNDATIONS_PLAN.md` (8A), `SEMANTIC_SYNONYMS_DESIGN.md`, `PER_USER_PASSTHROUGH_DESIGN.md`, `CONNECT_DATABRICKS_DESIGN.md`, `services/analytics-api/app/ontology_draft/existing_assets.py`
