-- Snowflake sandbox for Compass live validation (docs/WAREHOUSE_S6_LIVE_VALIDATION_PLAN.md).
-- RUN BY THE OWNER in a worksheet, as ACCOUNTADMIN (the resource monitor needs it). Compass never runs this and never writes to a warehouse.
-- Not run or tested by Compass: syntax is from Snowflake documentation. Fix any error here, not in the validation code.
-- Everything is new and prefixed COMPASS_VALIDATION, so nothing existing is touched. Teardown is at the bottom.

-- 1. A separate database, schema and the smallest warehouse, capped by a one-credit resource monitor.
CREATE DATABASE IF NOT EXISTS COMPASS_VALIDATION;
CREATE SCHEMA IF NOT EXISTS COMPASS_VALIDATION.SANDBOX;
CREATE RESOURCE MONITOR IF NOT EXISTS COMPASS_VALIDATION_RM
  WITH CREDIT_QUOTA = 1 TRIGGERS ON 80 PERCENT DO NOTIFY ON 100 PERCENT DO SUSPEND;
CREATE WAREHOUSE IF NOT EXISTS COMPASS_VALIDATION_WH
  WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE
  RESOURCE_MONITOR = COMPASS_VALIDATION_RM;

-- 2. The synthetic table, the same four columns and the same 100 rows as the pilot's sales_orders.
-- The names are quoted lower case on purpose: the Snowflake compiler quotes and keeps case, so the existing
-- certified sales-core@v1 contract (physical names sales_orders, id, amount, status, created_at) works unchanged.
CREATE OR REPLACE TABLE COMPASS_VALIDATION.SANDBOX."sales_orders" (
  "id" VARCHAR NOT NULL, "amount" NUMBER(12, 2) NOT NULL, "status" VARCHAR NOT NULL, "created_at" TIMESTAMP_TZ NOT NULL
);
INSERT INTO COMPASS_VALIDATION.SANDBOX."sales_orders"
SELECT 'o' || n, n::NUMBER(12, 2), IFF(MOD(n, 5) = 0, 'refunded', 'paid'),
       DATEADD(hour, n * 20, '2024-01-01 00:00:00 +00:00'::TIMESTAMP_TZ)
FROM (SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS n FROM TABLE(GENERATOR(ROWCOUNT => 100)));

-- 3. A read-only role: USAGE and SELECT on this one database and the warehouse, nothing else.
CREATE ROLE IF NOT EXISTS COMPASS_VALIDATION_RO;
GRANT USAGE ON DATABASE COMPASS_VALIDATION TO ROLE COMPASS_VALIDATION_RO;
GRANT USAGE ON SCHEMA COMPASS_VALIDATION.SANDBOX TO ROLE COMPASS_VALIDATION_RO;
GRANT SELECT ON TABLE COMPASS_VALIDATION.SANDBOX."sales_orders" TO ROLE COMPASS_VALIDATION_RO;
GRANT USAGE ON WAREHOUSE COMPASS_VALIDATION_WH TO ROLE COMPASS_VALIDATION_RO;

-- 4. A service user with a key pair. Generate the key on your own machine (see README.md) and paste ONLY the public key body here.
-- The private key and its passphrase never leave your machine except as the paths and values you export to the run.
CREATE USER IF NOT EXISTS COMPASS_VALIDATION_SVC
  TYPE = SERVICE DEFAULT_ROLE = COMPASS_VALIDATION_RO DEFAULT_WAREHOUSE = COMPASS_VALIDATION_WH
  RSA_PUBLIC_KEY = '<paste the public key body, no header or footer lines>';
GRANT ROLE COMPASS_VALIDATION_RO TO USER COMPASS_VALIDATION_SVC;

-- 5. Check before the run (read only): the role should list only USAGE and SELECT.
-- SHOW GRANTS TO ROLE COMPASS_VALIDATION_RO;

-- TEARDOWN (run when finished):
-- DROP USER IF EXISTS COMPASS_VALIDATION_SVC;
-- DROP ROLE IF EXISTS COMPASS_VALIDATION_RO;
-- DROP WAREHOUSE IF EXISTS COMPASS_VALIDATION_WH;
-- DROP RESOURCE MONITOR IF EXISTS COMPASS_VALIDATION_RM;
-- DROP DATABASE IF EXISTS COMPASS_VALIDATION;
