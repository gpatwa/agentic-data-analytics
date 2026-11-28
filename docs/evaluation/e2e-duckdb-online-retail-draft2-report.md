# End to end on DuckDB: Online Retail II (real UK retail transactions, CC BY 4.0, Chen 2012, doi 10.24432/C5CG6D), contract draft 2

Contract: **an evaluation fixture, not certified by the owner** (lifecycle set to certified in a temporary copy only). The contract is a DRAFT for the owner's review (fixture-2): fixture-1 plus a sales-invoices metric, a product dimension and customer_id classified restricted. Average invoice value stays refused: the compiler does not support ratio metrics yet.
Model: a Claude agent standing in for the model, given the exact prompt the product builds and nothing else
Answer keys: computed outside the product (plain Python over the source files, or an independent statement), never through the compiler.
20 questions: 13 the contract can answer and 7 it should refuse (a refusal that names a missing definition is a gap, not a failure).

## Results by setup

| Setup | Correct (of answerable) | Wrong numbers | Wrongly answered | Correct refusals | False refusals |
|---|---|---|---|---|---|
| **A** As authored (cost bounds raised for a table this size, coverage check on) | 4 of 13 | 0 | 0 | 7 | 9 |
| **B** A plus synonyms proposed blind by a fresh agent (kept only if the validator accepts each) | 4 of 13 | 0 | 0 | 7 | 9 |
| **C** B with the product's own default cost bounds (a run budget of 10,000 units) | 1 of 13 | 0 | 0 | 7 | 12 |
| **D** B plus confirm-instead-of-refuse (S6) answered by a careful user (confirms only a right reading) | 13 of 13 | 0 | 0 | 7 | 0 |
| **E** B plus S6 answered by a habitual user (always says yes) | 13 of 13 | 0 | 0 | 7 | 0 |

Synonyms in B to E: 28 kept, 0 rejected by the validator.

## Questions that were not answered correctly or refused correctly, by setup

| Setup | ID | Question | Expected | Result | Detail |
|---|---|---|---|---|---|
| A | R01 | What is our total sales amount? | answer | false_refusal | refuse: unsupported_request |
| A | R02 | How much value was cancelled? | answer | false_refusal | refuse: unsupported_request |
| A | R04 | How many invoices have we had? | answer | false_refusal | refuse: unsupported_request |
| A | R06 | How many units have we sold? | answer | false_refusal | refuse: unsupported_request |
| A | R08 | What were the sales in December 2010? | answer | false_refusal | refuse: unsupported_request |
| A | R09 | What are the sales in Germany? | answer | false_refusal | refuse: unsupported_request |
| A | R13 | Which product sold the most units? | answer | false_refusal | refuse: unsupported_request |
| A | R19 | How many invoices had sales? | answer | false_refusal | refuse: unsupported_request |
| A | R20 | What were the top 3 products by sales amount? | answer | false_refusal | refuse: unsupported_request |
| B | R01 | What is our total sales amount? | answer | false_refusal | refuse: unsupported_request |
| B | R02 | How much value was cancelled? | answer | false_refusal | refuse: unsupported_request |
| B | R04 | How many invoices have we had? | answer | false_refusal | refuse: unsupported_request |
| B | R06 | How many units have we sold? | answer | false_refusal | refuse: unsupported_request |
| B | R08 | What were the sales in December 2010? | answer | false_refusal | refuse: unsupported_request |
| B | R09 | What are the sales in Germany? | answer | false_refusal | refuse: unsupported_request |
| B | R13 | Which product sold the most units? | answer | false_refusal | refuse: unsupported_request |
| B | R19 | How many invoices had sales? | answer | false_refusal | refuse: unsupported_request |
| B | R20 | What were the top 3 products by sales amount? | answer | false_refusal | refuse: unsupported_request |
| C | R01 | What is our total sales amount? | answer | false_refusal | refuse: unsupported_request |
| C | R02 | How much value was cancelled? | answer | false_refusal | refuse: unsupported_request |
| C | R04 | How many invoices have we had? | answer | false_refusal | refuse: unsupported_request |
| C | R05 | How many customers do we have? | answer | false_refusal | failed: |
| C | R06 | How many units have we sold? | answer | false_refusal | refuse: unsupported_request |
| C | R07 | Show sales amount by country for the United Kingdom, Germany and France | answer | false_refusal | failed: |
| C | R08 | What were the sales in December 2010? | answer | false_refusal | refuse: unsupported_request |
| C | R09 | What are the sales in Germany? | answer | false_refusal | refuse: unsupported_request |
| C | R10 | What is the cancellation amount in the United Kingdom? | answer | false_refusal | failed: |
| C | R13 | Which product sold the most units? | answer | false_refusal | refuse: unsupported_request |
| C | R19 | How many invoices had sales? | answer | false_refusal | refuse: unsupported_request |
| C | R20 | What were the top 3 products by sales amount? | answer | false_refusal | refuse: unsupported_request |

## Every question under setup D

| ID | Question | Expected | Result | Detail |
|---|---|---|---|---|
| R01 | What is our total sales amount? | answer | correct | 20813918.428 |
| R02 | How much value was cancelled? | answer | correct | -1526667.86 |
| R03 | How many invoice lines are there? | answer | correct | 1067371 |
| R04 | How many invoices have we had? | answer | correct | 53628 |
| R05 | How many customers do we have? | answer | correct | 5942 |
| R06 | How many units have we sold? | answer | correct | 11099484 |
| R07 | Show sales amount by country for the United Kingdom, Germany and France | answer | correct | 3 values match |
| R08 | What were the sales in December 2010? | answer | correct | 1262598.79 |
| R09 | What are the sales in Germany? | answer | correct | 431262.461 |
| R10 | What is the cancellation amount in the United Kingdom? | answer | correct | -1329717.74 |
| R11 | What is the average invoice value? | refuse | correct_refusal | unsupported_request |
| R12 | What is our profit margin? | refuse | correct_refusal | unsupported_request |
| R13 | Which product sold the most units? | answer | correct | 1 values match |
| R14 | What were sales last week? | refuse | correct_refusal | unsupported_request |
| R15 | How are we doing? | refuse | correct_refusal | unsupported_request |
| R16 | What is the total sales amount including VAT? | refuse | correct_refusal | unsupported_request |
| R17 | Show sales for customer 12346 | refuse | correct_refusal | unsupported_request |
| R18 | Which customers returned the most goods? | refuse | correct_refusal | unsupported_request |
| R19 | How many invoices had sales? | answer | correct | 45336 |
| R20 | What were the top 3 products by sales amount? | answer | correct | 3 values match |

What this does not show: a real language model's choices (a Claude agent stood in), real users (the careful and habitual users are scripted), other contracts, or any warehouse other than DuckDB.
