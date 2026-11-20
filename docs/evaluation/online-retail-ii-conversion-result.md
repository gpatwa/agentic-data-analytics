# Online Retail II: conversion result (2026-11-20)

A one-off run on the real file. It is **not** pinned by a test (the file is a 45 MB download that is not in the repository); the converter and its logic are tested on small workbooks in `services/analytics-api/tests/test_loaders.py`.

- **Source:** Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository, https://doi.org/10.24432/C5CG6D. Licence: CC BY 4.0.
- **File:** `online_retail_II.xlsx`, 45,622,278 bytes, SHA-256 `bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980` (inside the zip, whose SHA-256 is `572e36277c2390fbfde10664750731e0a86f55e33470d91919085f0408e67bfb`).
- **How it was read:** unpacked into a directory of its own and converted with `python3 -I` (isolated mode), by a script kept outside that directory. Nothing was executed from the file.
- **Result:** 1,067,371 rows, as the source page states. Conversion took about 30 seconds on the developer's laptop and produced a 13.9 MB Parquet file.
- **Two answer keys agree:** a Python ledger (exact Decimal arithmetic while reading) and a DuckDB recomputation over the Parquet. Zero disagreements.

| Value | Result |
|---|---|
| Lines | 1,067,371 |
| Distinct invoices | 53,628 |
| Distinct customers (non-null) | 5,942 |
| Lines without a customer id | 243,007 |
| Cancellation lines (invoice starts with C) | 19,494 |
| Sales lines value | 20,813,918.43 |
| Cancellation value | -1,526,667.86 |
| Naive net value (sum of every line) | 19,287,250.57 |
| Total quantity | 10,608,492 |

Currency is not stated by the source and is not asserted here.

What the numbers show for certified definitions: a naive sum of every line nets cancellations against sales (a difference of about 1.53 million), and about a fifth of lines carry no customer id, so a customer-level metric must say how those lines are treated.

Nothing was loaded into a warehouse.
