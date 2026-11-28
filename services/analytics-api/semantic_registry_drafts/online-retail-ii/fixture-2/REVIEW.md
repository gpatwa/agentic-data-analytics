# Online Retail II, contract draft 2 (fixture-2): for the owner's review. Not certified.

Source: Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository, https://doi.org/10.24432/C5CG6D (CC BY 4.0).

What it adds to fixture-1: a **sales invoices** metric, a **product** dimension (the line description), and `customer_id` classified **restricted** (the model is never offered a restricted field, so a per-customer filter cannot be asked for).

Measured on the real data (`docs/evaluation/e2e-duckdb-online-retail-draft2-report.md`): with confirm-instead-of-refuse and a careful user, 13 of 13 answerable questions correct and every refusal held; with a habitual user, also 13 of 13 and **no** question wrongly answered (the per-customer question is no longer reachable).

Decisions you must make before certifying anything here:
1. **Product is not only merchandise.** The top products by sales include `Manual` and `DOTCOM POSTAGE` (lines that are adjustments and postage, not goods). Should the product dimension exclude them (a filter on stock code), or should they stay and be named?
2. **"Revenue".** The blind synonym proposal did not include it. Does "revenue" mean sales amount, or net of cancellations? Certify one meaning or none.
3. **Average invoice value stays refused.** It needs a ratio metric and the compiler does not support ratio metrics yet (`app/compiler/postgres.py`); that is a product feature, not a contract choice.
4. **Currency** is not stated by the source and is not asserted.
5. **Lines without a customer id** (243,007) are not counted by the customers metric; say so in the certified description if you want it explicit.
