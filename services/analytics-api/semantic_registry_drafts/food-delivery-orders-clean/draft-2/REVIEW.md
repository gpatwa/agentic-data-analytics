# Food delivery over `orders_clean`, contract draft 2 (draft-2): for the owner's review. Not certified. SYNTHETIC data.

`orders_clean` is a NEW view layered on `orders_current` (loader option `--clean-view-only`, dry-run only so far). It changes nothing about `orders_current` or its certified contract. It adds two corrections:
- **Cuisine and restaurant name come from `restaurants`**, the restaurant's own value, not the order-level copy that carries the planted spelling drift (`Pizza`, `pizza`, `PIZZA`, `Pizza `, `Pizza Place`). No spelling is guessed.
- **`order_local_ts`** is the order time in the city's own time zone (the compiler does not convert time zones). The contract offers it as the only time dimension, "Order date in local time"; the UTC time is a field, not a dimension, so there is one way to ask for a month and it is the local one.

Measured on tier S (`docs/evaluation/e2e-duckdb-synthetic-report.md`): confirm-instead-of-refuse with a careful user answers **17 of 17** answerable questions correctly with **no wrong number**, including cuisine and the months in local time; with a habitual user, one question is wrongly answered (reversal rows, below). The same questions against the **certified** `orders_current` contract give 5 wrong numbers (`docs/evaluation/e2e-duckdb-synthetic-certified-report.md`).

Decisions you must make before certifying anything here:
1. **Creating `orders_clean` in the warehouse** needs an approval that names it, with the statements shown first. Until then it exists only on DuckDB.
2. **A new contract, not a change to the certified one.** A certified meaning lives in its view; certify `food-delivery-orders-clean` as its own contract, then decide whether `food-delivery-orders-current` stays.
3. **Reversal rows stay out.** Refunds and net value are still refused. A reversal-rows question answered against this view returns a confident `0`, because the view has none; a habitual confirmer is told "limited to is reversal equals True" and may not notice. A guard for a degenerate answer is worth deciding.
4. **Filter values are guessed.** The model is not shown stored values, so it picks the casing (`Pizza`, or `pizza`). The clean view makes `Pizza` the only spelling, so the guess is right; case-insensitive matching or an offered value list is a product choice.
5. **Currency** is still not converted: gross value counts US-dollar orders only (a required filter).
6. The four personal-looking fields are classified restricted and never offered to a model.
