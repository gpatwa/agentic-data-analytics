# Warehouse sandbox for live validation

For stage S6 of `docs/WAREHOUSE_AND_DBT_PLAN.md`; the plan is `docs/WAREHOUSE_S6_LIVE_VALIDATION_PLAN.md`. Nothing here has been
run. **You** run the setup scripts and create the key and token; Compass never writes to a warehouse and never needs an admin
credential. Do this only in a sandbox, never in a customer's warehouse.

## Snowflake (account admin, once)

1. Make a key pair on your machine, with a passphrase, and keep the private key out of the repository:

   ```bash
   openssl genrsa 2048 | openssl pkcs8 -topk8 -v2 aes256 -inform PEM -out ~/compass_validation_rsa.p8
   openssl rsa -in ~/compass_validation_rsa.p8 -pubout -out ~/compass_validation_rsa.pub
   chmod 600 ~/compass_validation_rsa.p8
   ```

2. Paste the public key body (without the header and footer lines) into `sandbox_setup.snowflake.sql` where it says so, run the whole
   script in a worksheet as `ACCOUNTADMIN`, then run the `SHOW GRANTS TO ROLE COMPASS_VALIDATION_RO` line and check it lists only
   `USAGE` and `SELECT`.
3. Set, in your own shell for the run: `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER=COMPASS_VALIDATION_SVC`,
   `SNOWFLAKE_PRIVATE_KEY_PATH`, `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`, `SNOWFLAKE_WAREHOUSE=COMPASS_VALIDATION_WH`,
   `SNOWFLAKE_DATABASE=COMPASS_VALIDATION`, `SNOWFLAKE_SCHEMA=SANDBOX`, `SNOWFLAKE_ROLE=COMPASS_VALIDATION_RO`.
   The account value is the identifier the connector expects (for a URL like `app.snowflake.com/<org>/<account>` it is
   `<org>-<account>`); check Snowflake's documentation for your account.

## Databricks (workspace admin, once)

1. Run `sandbox_setup.databricks.sql` in the SQL editor on your SQL warehouse.
2. Create a service principal (account console, User management), give it `CAN USE` on the SQL warehouse, uncomment and run the three
   `GRANT` lines for it, then create an access token for that principal (Databricks documentation: token management for service
   principals). Do not use your personal token: it can read everything you can.
3. Set, in your own shell for the run: `DATABRICKS_SERVER_HOSTNAME` (the workspace host without `https://`), `DATABRICKS_HTTP_PATH`
   (the SQL warehouse's connection details), `DATABRICKS_TOKEN`, `DATABRICKS_CATALOG=compass_validation`,
   `DATABRICKS_SCHEMA=sandbox`.
4. Set a short auto-stop on the SQL warehouse.

## Running it (after you approve the run in the task that does it)

```bash
cd services/analytics-api
pip install -r requirements-warehouses.txt
python3 -m app.execution.warehouse_validation snowflake --allow-live --expect-target <your account> --out report.md
python3 -m app.execution.warehouse_validation databricks --allow-live --expect-target <your workspace host> --out report.md
```

The run stops at the first failed check and writes numbers and codes only. When finished, run the teardown block at the bottom of the
setup script, revoke the token or key, and suspend or stop the warehouse.
