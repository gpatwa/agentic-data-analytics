# Databricks registration end-to-end check (D3): results (2026-11-18)

Statements: `CONNECT_DATABRICKS_REGISTRATION_STATEMENTS.md` (generated; the run used a fresh suffix). Approved in advance by the owner together with the offline build. Run once, as the signed-in owner, on **one** workspace (the owner's validation workspace, synthetic data). No host id, secret or API message is recorded.

## What ran

A sandbox catalog with three synthetic tables of 30 rows (two inside the ceiling, one outside); the product's generated registration script (create the service principal, its OAuth secret, `CAN_USE` on the existing Serverless Starter Warehouse chosen by name, `USE CATALOG`, `USE SCHEMA` and `SELECT` on the two named tables); the product's verifier on the real read-backs; a connection test through the product's Databricks session with OAuth M2M as the new principal; the removal script and created-only teardown; a sweep.

## What it showed

| Question | Result |
|---|---|
| Does the generated script run as written? | **Yes**, every step succeeded on the first run. |
| Does `verify_registration` accept the real read-backs? | **Yes**: no missing, unexpected or dangerous grants and no warehouse problem. The assumed `SHOW GRANTS` column order and the warehouse permission shape were correct. |
| Does a connection through the product's session work? | **Yes**: OAuth M2M, both ceiling tables returned 30 rows. |
| Is the principal held to the ceiling? | **Yes**: the table outside the ceiling was denied. |
| Does removal leave nothing? | **Yes**: grants revoked, secret and principal deleted by id, sandbox catalog dropped; the sweep found no catalog, principal or warehouse permission of ours. |
| Any secret in the report? | **No** (the scanner watched the principal's secret). |

## Not shown

The stored-secret path in the vault and the real Compass screen for Databricks (not built); the principal's behaviour after secret rotation or expiry; a second cloud or edition; a verifier reading the grant list for objects beyond the five it inspects; the independent review. The admin steps here ran with the owner's own rights; whether a customer's admin can run them is for the customer's workspace to show.
