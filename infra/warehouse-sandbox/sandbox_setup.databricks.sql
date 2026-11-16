-- Databricks sandbox for Compass live validation (docs/WAREHOUSE_S6_LIVE_VALIDATION_PLAN.md).
-- RUN BY THE OWNER in the SQL editor on the existing SQL warehouse. Compass never runs this and never writes to a warehouse.
-- Not run or tested by Compass: syntax is from Databricks documentation. Fix any error here, not in the validation code.
-- Unity Catalog. Everything is new and prefixed compass_validation, so nothing existing is touched. Teardown is at the bottom.
-- If your workspace cannot create a catalog (no managed storage location), create only the schema in a catalog you already have,
-- and set DATABRICKS_CATALOG to that catalog's name.

SET TIME ZONE 'UTC';
CREATE CATALOG IF NOT EXISTS compass_validation;
CREATE SCHEMA IF NOT EXISTS compass_validation.sandbox;

-- The synthetic table, the same four columns and the same 100 rows as the pilot's sales_orders.
CREATE OR REPLACE TABLE compass_validation.sandbox.sales_orders (
  id STRING NOT NULL, amount DECIMAL(12, 2) NOT NULL, status STRING NOT NULL, created_at TIMESTAMP NOT NULL
);
INSERT INTO compass_validation.sandbox.sales_orders
SELECT concat('o', id), CAST(id AS DECIMAL(12, 2)), IF(id % 5 = 0, 'refunded', 'paid'),
       timestampadd(HOUR, CAST(id * 20 AS INT), TIMESTAMP '2024-01-01 00:00:00+00:00')
FROM range(1, 101);

-- A read-only identity: create a SERVICE PRINCIPAL in the account console (README.md), then run these grants for it.
-- Replace <principal> with the service principal's application ID, in backticks.
-- GRANT USE CATALOG ON CATALOG compass_validation TO `<principal>`;
-- GRANT USE SCHEMA ON SCHEMA compass_validation.sandbox TO `<principal>`;
-- GRANT SELECT ON TABLE compass_validation.sandbox.sales_orders TO `<principal>`;
-- Also give the principal CAN USE on the SQL warehouse (warehouse page, Permissions), and nothing else.

-- Check before the run (read only): the principal should list only SELECT, USE SCHEMA and USE CATALOG.
-- SHOW GRANTS ON TABLE compass_validation.sandbox.sales_orders;

-- TEARDOWN (run when finished):
-- DROP SCHEMA IF EXISTS compass_validation.sandbox CASCADE;
-- DROP CATALOG IF EXISTS compass_validation CASCADE;
