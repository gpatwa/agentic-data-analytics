# Food delivery over `orders_current`, contract draft 1 (draft-1): for the owner's review. Not certified. SYNTHETIC data.

The view `orders_current` (loader option `--with-curated-view`, `reference_stack/loaders/synthetic.py`) keeps one row per real order: the latest recorded version, no reversal rows, no test restaurant. `order_count` over it is 9,995, equal to the generator's key; over the raw table it would be 11,126.

Measured on the generated tier S (`docs/evaluation/e2e-duckdb-synthetic-report.md`): confirm-instead-of-refuse and a careful user answer 12 of 14 answerable questions correctly; **2 are wrong numbers, both the planted spelling-drift defect (D8)**: "orders for pizza" is 170 on the raw `cuisine` column against 200 in the key, because `pizza`, `PIZZA` and `Pizza ` are separate values. The product answered the certified column faithfully; the data is what is wrong.

Decisions you must make before certifying anything here:
1. **Cuisine spelling.** Normalise it in the curated view (trim, case-fold, map the variants), then certify `cuisine` over the cleaned column. Until then a cuisine question returns the raw spelling's count.
2. **Months.** The key counts a month in each city's local time; the compiler does not convert time zones, so a month question answered from `order_ts` uses UTC (865 against 873 for January 2025). Either the view carries a local-time column, or month questions stay refused.
3. **Refunds and net value** need the reversal rows, which `orders_current` leaves out. A second view or dataset is a certification decision.
4. **Currency.** Gross value is USD only; other currencies are excluded by a required filter. Say whether a conversion rule exists.
5. **A confident zero.** A reversal-rows question, answered against this view, returns 0 with no warning, because the view has none. A habitual confirmer is told "limited to is reversal equals True" and may not notice.
6. The four personal-looking fields (`customer_id`, `customer_email`, `courier_phone`, `delivery_address`) are classified restricted and never offered to a model. They are invented values, shaped like the real thing.
