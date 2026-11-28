# End to end on DuckDB: what the first run shows (2026-11-28)

Question in, number out, through the real governed path, on **real data**, on DuckDB, with answers computed outside the product. Details: `e2e-duckdb-online-retail-report.md` and `e2e-duckdb-olist-report.md` (the Olist one is **internal only**: its licence is reported as non-commercial).

**What ran.** The real runtime (identity, context, intent node, resolution, echo and coverage checks, deterministic compiler, policy and cost checks, DuckDB read-only gateway, answer validation, evidence). The language model was a fresh Claude agent given the exact prompt the product builds, never the answers. Keys: plain Python or an independent statement over the source files. Online Retail II (1,067,371 real lines, CC BY 4.0, Chen 2012, doi 10.24432/C5CG6D): 10 answerable and 8 refusal questions, against an **evaluation-fixture contract** that is not certified. Olist (about 100,000 real orders): the owner's 26 draft golden questions against the **certified** `olist-commerce-v1`, three of which it can answer today.

| Setup | Online Retail II: correct of 10 | Olist: correct of 3 |
|---|---|---|
| A: as authored | 4 | 0 |
| B: plus synonyms proposed blind by a fresh agent | 4 | 0 |
| C: B under the product's default cost bounds | 1 | 0 |
| D: B plus confirm-instead-of-refuse (S6), careful user | **10** | **3** |
| E: B plus S6, habitual user | 10 (1 wrongly answered) | 3 (1 wrongly answered) |

Wrong numbers in every setup, both datasets: **0**. Every question the contract cannot answer was refused (Retail 8 of 8; Olist 23 of 23, each a named gap needing a certified definition), except the one wrongly answered question in setup E on each dataset.

## What it means

1. **When the product answers, the number is right.** Across all five setups and 44 questions there was no wrong number. The risk is not wrong arithmetic; it is a refusal of an ordinary question.
2. **The wording gate refuses most ordinary phrasings.** Without S6, only 4 of 10 Retail questions and none of the 3 Olist questions were answered, although the model chose the right metric every time. "What is our total sales amount?" is refused because "total" is a word the request does not use; "How many invoices have we had?" because the metric is named "distinct invoices". Synonyms proposed blind did not help: the words people use ("revenue", "invoices") were not the words proposed, and proposing a bare word like "revenue" is a business decision for the owner.
3. **S6 is what makes the product usable here.** With a careful user every answerable question was answered correctly and every refusal held. This is the strongest case yet for turning S6 on for the sandbox tenant.
4. **The habitual-confirmer risk is small on this data, and for a reason that could change.** One wrongly answered question per dataset, because the model itself refused the uncovered questions before S6 was asked. S6 only sees readings the model resolved. A model that guesses more would put more in front of a person who clicks yes.
5. **Two things a habitual user got through, both worth a decision.** Retail R17, "Show sales for customer 12346": the model filtered on `customer_id`, a field classified confidential, and the product answered. The policy layer did not stop a filter on a confidential field. Olist Q26, a degenerate question the owner's draft says should be refused ("delivered revenue by order status"), was answered with one row.
6. **The default cost bounds are sized for small tables.** On 1,067,371 rows a distinct count or a grouping exceeds the 10,000-unit run budget or the 1,000,000-unit execution limit (setup C: 1 of 10 against 4 of 10 in A). The bounds should be set per connection by the owner; raising them here was a harness setting, reported.
7. **The certified Olist contract assumes a prepared column.** `payment_total` is not in the raw Olist data: payments must be summed per order first. The contract states the field but nothing in the repository performs that step. A load step for it is needed before the contract can run on a customer's raw tables.

## Not shown

A real language model's choices, real users (the careful and habitual users are scripted), other contracts, any warehouse other than DuckDB, and certification: the Retail contract is a fixture and nothing here certifies anything. Olist figures are for internal use only.

## What this opens

The same questions and keys can be run on Databricks unchanged, once the same data is loaded there (a separate approval, with the Retail and Olist tables). Decisions that are the owner's: enabling S6 for the sandbox tenant; the business vocabulary for "revenue", "invoices" and "units" in a certified contract; cost bounds per connection; whether a filter on a confidential field may be answered.
