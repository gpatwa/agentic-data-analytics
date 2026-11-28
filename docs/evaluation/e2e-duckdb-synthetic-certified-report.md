# End to end on DuckDB: Synthetic food delivery, tier S, the owner's CERTIFIED orders_current contract

Contract: certified by the owner (tenant id rewritten in memory for the harness). `food-delivery-orders-current-v1` as certified (counts and eight dimensions; amount sums are not certified); the tenant id and the table's physical name are rewritten in memory for the harness.
Model: a Claude agent standing in for the model, given the exact prompt the product builds and nothing else
Answer keys: computed outside the product (plain Python over the source files, or an independent statement), never through the compiler.
30 questions: 13 the contract can answer and 17 it should refuse (a refusal that names a missing definition is a gap, not a failure).

## Results by setup

| Setup | Correct (of answerable) | Wrong numbers | Wrongly answered | Correct refusals | False refusals |
|---|---|---|---|---|---|
| **A** As authored (cost bounds raised for a table this size, coverage check on) | 0 of 13 | 0 | 0 | 17 | 13 |
| **B** A plus synonyms proposed blind by a fresh agent (kept only if the validator accepts each) | 0 of 13 | 0 | 0 | 17 | 13 |
| **C** B with the product's own default cost bounds (a run budget of 10,000 units) | 0 of 13 | 0 | 0 | 17 | 13 |
| **D** B plus confirm-instead-of-refuse (S6) answered by a careful user (confirms only a right reading) | 8 of 13 | 5 | 0 | 17 | 0 |
| **E** B plus S6 answered by a habitual user (always says yes) | 8 of 13 | 5 | 1 | 16 | 0 |

Synonyms in B to E: 0 kept, 0 rejected by the validator.

## Questions that were not answered correctly or refused correctly, by setup

| Setup | ID | Question | Expected | Result | Detail |
|---|---|---|---|---|---|
| A | A01 | How many orders were placed? | answer | false_refusal | refuse: unsupported_request |
| A | A02 | How many orders were delivered? | answer | false_refusal | refuse: unsupported_request |
| A | A03 | How many orders were cancelled? | answer | false_refusal | refuse: unsupported_request |
| A | A04 | How many orders were refunded in full? | answer | false_refusal | refuse: unsupported_request |
| A | A05 | How many orders were partially refunded? | answer | false_refusal | refuse: unsupported_request |
| A | A11 | How many orders were placed in New York? | answer | false_refusal | refuse: unsupported_request |
| A | A12 | How many orders were placed in London? | answer | false_refusal | refuse: unsupported_request |
| A | A13 | How many orders were placed in Chicago? | answer | false_refusal | refuse: unsupported_request |
| A | A14 | How many orders were placed in January 2025? | answer | false_refusal | refuse: unsupported_request |
| A | A15 | How many orders were placed in July 2025? | answer | false_refusal | refuse: unsupported_request |
| A | A16 | How many orders were placed in December 2025? | answer | false_refusal | refuse: unsupported_request |
| A | A17 | How many orders were for pizza? | answer | false_refusal | refuse: unsupported_request |
| A | A18 | How many orders were for sushi? | answer | false_refusal | refuse: unsupported_request |
| B | A01 | How many orders were placed? | answer | false_refusal | refuse: unsupported_request |
| B | A02 | How many orders were delivered? | answer | false_refusal | refuse: unsupported_request |
| B | A03 | How many orders were cancelled? | answer | false_refusal | refuse: unsupported_request |
| B | A04 | How many orders were refunded in full? | answer | false_refusal | refuse: unsupported_request |
| B | A05 | How many orders were partially refunded? | answer | false_refusal | refuse: unsupported_request |
| B | A11 | How many orders were placed in New York? | answer | false_refusal | refuse: unsupported_request |
| B | A12 | How many orders were placed in London? | answer | false_refusal | refuse: unsupported_request |
| B | A13 | How many orders were placed in Chicago? | answer | false_refusal | refuse: unsupported_request |
| B | A14 | How many orders were placed in January 2025? | answer | false_refusal | refuse: unsupported_request |
| B | A15 | How many orders were placed in July 2025? | answer | false_refusal | refuse: unsupported_request |
| B | A16 | How many orders were placed in December 2025? | answer | false_refusal | refuse: unsupported_request |
| B | A17 | How many orders were for pizza? | answer | false_refusal | refuse: unsupported_request |
| B | A18 | How many orders were for sushi? | answer | false_refusal | refuse: unsupported_request |
| C | A01 | How many orders were placed? | answer | false_refusal | refuse: unsupported_request |
| C | A02 | How many orders were delivered? | answer | false_refusal | refuse: unsupported_request |
| C | A03 | How many orders were cancelled? | answer | false_refusal | refuse: unsupported_request |
| C | A04 | How many orders were refunded in full? | answer | false_refusal | refuse: unsupported_request |
| C | A05 | How many orders were partially refunded? | answer | false_refusal | refuse: unsupported_request |
| C | A11 | How many orders were placed in New York? | answer | false_refusal | refuse: unsupported_request |
| C | A12 | How many orders were placed in London? | answer | false_refusal | refuse: unsupported_request |
| C | A13 | How many orders were placed in Chicago? | answer | false_refusal | refuse: unsupported_request |
| C | A14 | How many orders were placed in January 2025? | answer | false_refusal | refuse: unsupported_request |
| C | A15 | How many orders were placed in July 2025? | answer | false_refusal | refuse: unsupported_request |
| C | A16 | How many orders were placed in December 2025? | answer | false_refusal | refuse: unsupported_request |
| C | A17 | How many orders were for pizza? | answer | false_refusal | refuse: unsupported_request |
| C | A18 | How many orders were for sushi? | answer | false_refusal | refuse: unsupported_request |
| D | A14 | How many orders were placed in January 2025? | answer | wrong_answer | got 865, key 873 |
| D | A15 | How many orders were placed in July 2025? | answer | wrong_answer | got 811, key 817 |
| D | A16 | How many orders were placed in December 2025? | answer | wrong_answer | got 872, key 876 |
| D | A17 | How many orders were for pizza? | answer | wrong_answer | got 10, key 200 |
| D | A18 | How many orders were for sushi? | answer | wrong_answer | got 22, key 676 |
| E | A14 | How many orders were placed in January 2025? | answer | wrong_answer | got 865, key 873 |
| E | A15 | How many orders were placed in July 2025? | answer | wrong_answer | got 811, key 817 |
| E | A16 | How many orders were placed in December 2025? | answer | wrong_answer | got 872, key 876 |
| E | A17 | How many orders were for pizza? | answer | wrong_answer | got 10, key 200 |
| E | A18 | How many orders were for sushi? | answer | wrong_answer | got 22, key 676 |
| E | R08 | How do orders this week compare with the week before? | refuse | wrongly_answered | answered: The validated result is returned without narrative. |

## Every question under setup D

| ID | Question | Expected | Result | Detail |
|---|---|---|---|---|
| A01 | How many orders were placed? | answer | correct | 9995 |
| A02 | How many orders were delivered? | answer | correct | 9000 |
| A03 | How many orders were cancelled? | answer | correct | 591 |
| A04 | How many orders were refunded in full? | answer | correct | 244 |
| A05 | How many orders were partially refunded? | answer | correct | 160 |
| A06 | What is the gross order value in US dollars? | refuse | correct_refusal | unsupported_request |
| A07 | How much was refunded in US dollars? | refuse | correct_refusal | unsupported_request |
| A08 | What is the net order value in US dollars? | refuse | correct_refusal | unsupported_request |
| A09 | How much did customers tip on card orders in US dollars? | refuse | correct_refusal | unsupported_request |
| A10 | How many reversal rows are there? | refuse | correct_refusal | unsupported_request |
| A11 | How many orders were placed in New York? | answer | correct | 2025 |
| A12 | How many orders were placed in London? | answer | correct | 565 |
| A13 | How many orders were placed in Chicago? | answer | correct | 728 |
| A14 | How many orders were placed in January 2025? | answer | wrong_answer | got 865, key 873 |
| A15 | How many orders were placed in July 2025? | answer | wrong_answer | got 811, key 817 |
| A16 | How many orders were placed in December 2025? | answer | wrong_answer | got 872, key 876 |
| A17 | How many orders were for pizza? | answer | wrong_answer | got 10, key 200 |
| A18 | How many orders were for sushi? | answer | wrong_answer | got 22, key 676 |
| A19 | What is the total delivery time in minutes? | refuse | correct_refusal | unsupported_request |
| A20 | How many orders have a recorded delivery time? | refuse | correct_refusal | unsupported_request |
| R01 | What is the refund rate? | refuse | correct_refusal | unsupported_request |
| R02 | What is the cancellation rate? | refuse | correct_refusal | unsupported_request |
| R03 | What percentage of deliveries took under 30 minutes? | refuse | correct_refusal | unsupported_request |
| R04 | What is the average tip as a percentage of the order? | refuse | correct_refusal | unsupported_request |
| R05 | What is the revenue per courier? | refuse | correct_refusal | unsupported_request |
| R06 | How many orders were delivered by each courier vehicle type? | refuse | correct_refusal | unsupported_request |
| R07 | What is the customer lifetime value? | refuse | correct_refusal | unsupported_request |
| R08 | How do orders this week compare with the week before? | refuse | correct_refusal | unsupported_request |
| R09 | Which restaurants are growing fastest? | refuse | correct_refusal | unsupported_request |
| R10 | What is the customer satisfaction score? | refuse | correct_refusal | unsupported_request |

What this does not show: a real language model's choices (a Claude agent stood in), real users (the careful and habitual users are scripted), other contracts, or any warehouse other than DuckDB.
