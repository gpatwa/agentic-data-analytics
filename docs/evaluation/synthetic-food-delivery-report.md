# Synthetic food-delivery data: generator report

SYNTHETIC DATA. Generated, not real, not customer data and not data from any named company. Regenerate this page with `python -m reference_stack.synthetic.report`.

- Generator `fd-1.0.0`, tier `S` (10,000 orders), seed `20260101`, start 2025-01-01, 365 days
- Config digest `5b19965e8e94a939`, logical digest `33b366fc5ce0dffa`
- The rates are plausible guesses by the authors, not measured from any real platform.

## Tables

| Table | Rows |
|---|---|
| `restaurants` | 80 |
| `couriers` | 60 |
| `customers` | 2,000 |
| `orders` | 11,126 |
| `order_items` | 25,975 |
| `payments` | 9,813 |
| `orders_flat` | 11,126 |

## Defects, by how well their real analogue is evidenced

| ID | Evidence | Defect | Rate parameter | Visible in the output |
|---|---|---|---|---|
| D1 | reviewed | cancellations and refunds are separate reversal rows with negative amounts | structural | 404 |
| D2 | reviewed | cash tips are not recorded in the tip field | structural | 988 |
| D3 | partly reviewed | late-arriving versions: an order is re-recorded with a corrected status | 0.7 | 681 |
| D7 | partly reviewed | nulls and placeholder values (-1) in minutes columns | 0.005 | 80 |
| D8 | partly reviewed | cuisine spelling drift | 0.15 | 48 |
| D4 | general | duplicate events: an identical row written twice | 0.003 | 41 |
| D5 | general | order_ts is stored in UTC while the business day is local | structural | 3,133 |
| D6 | general | mixed currencies in one amount column | structural | 2 |
| D9 | general | test orders at a test restaurant with 0.01 amounts | 0.0005 | 5 |
| D10 | general | orphan restaurant references | 0.002 | 23 |

## Two answer keys

The generator's ledger (from the clean order table, in integer cents) and a DuckDB recomputation from the Parquet alone: **0 disagreement(s)** over 15 values and 3 breakdowns.

| Definition | Unit | Value | What it says |
|---|---|---|---|
| `cancelled_orders` | count | 591 | orders whose latest version has status cancelled |
| `day_boundary_orders` | count | 2,810 | orders whose UTC calendar day differs from their local calendar day |
| `delivered_orders` | count | 9,000 | orders whose latest version has status delivered |
| `delivery_minutes_count` | count | 9,359 | number of orders counted in delivery_minutes_sum |
| `delivery_minutes_sum` | minutes | 221,108 | sum of delivery_minutes over each order's latest version, excluding null and the -1 placeholder |
| `gross_value_usd_cents` | cents | 54,501,203 | sum of total_amount of each order's latest non-reversal version, USD only, excluding cancelled orders and test orders |
| `naive_row_count` | count | 11,126 | count of every row, as a certification that ignored versions, reversals, duplicates and test orders would give |
| `naive_sum_total_cents` | cents | 79,865,099 | sum of total_amount over every row and currency, as a naive certification would give |
| `net_value_usd_cents` | cents | 52,772,308 | gross value minus refunds |
| `orders_distinct` | count | 9,995 | distinct orders, excluding the test restaurant |
| `partially_refunded_orders` | count | 160 | orders whose latest version has status partially_refunded |
| `refunded_orders` | count | 244 | orders whose latest version has status refunded |
| `refunds_usd_cents` | cents | 1,728,895 | minus the sum of total_amount over reversal rows, USD only |
| `reversal_rows` | count | 404 | rows with is_reversal true |
| `tip_card_usd_cents` | cents | 2,471,749 | sum of tip_amount of the latest version, card payments only, USD, excluding cancelled orders (cash tips are not recorded) |

Hidden from the data by D2: tips really paid in USD on every payment method come to 3,864,099 cents, against 2,471,749 cents recorded on card orders alone.

Neither key uses the product. A certified definition must choose among these (for example whether revenue includes reversals); the naive values show what an unexamined certification would report.
