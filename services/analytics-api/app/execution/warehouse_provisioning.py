"""Sandbox provisioning for the warehouse live validation: creates the whole sandbox, then removes it. No manual steps.

This is an ADMIN tool and is deliberately separate from the product's query path, which stays read-only. It is used only by
`warehouse_validation --provision`, never imported by the API, and it refuses to touch anything but the objects named
below (every identifier is a fixed constant, never built from input).

- Admin sign-in is a browser SSO prompt (Snowflake `externalbrowser`, Databricks OAuth `external-browser`). No admin password
  or token is typed, exported or stored; the person approves one sign-in in their already-signed-in browser.
- The read-only identity is created here with a fresh credential held only in memory: a new RSA key pair for Snowflake, a
  short-lived token for a new service principal on Databricks. Nothing is written to a file or the environment, and
  teardown deletes the identity, so the credential dies with the run.
- Setup is idempotent and teardown always runs (the caller wraps it in `finally`). Teardown drops only the names below.

Not run against a live account. The statements follow vendor documentation; the Databricks calls were checked against the
installed `databricks-sdk` signatures only.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.execution.warehouse_sessions import DatabricksConfig, SnowflakeConfig

SNOWFLAKE_DB = "COMPASS_VALIDATION"
SNOWFLAKE_SCHEMA = "SANDBOX"
SNOWFLAKE_WAREHOUSE = "COMPASS_VALIDATION_WH"
SNOWFLAKE_MONITOR = "COMPASS_VALIDATION_RM"
SNOWFLAKE_ROLE = "COMPASS_VALIDATION_RO"
SNOWFLAKE_USER = "COMPASS_VALIDATION_SVC"
DATABRICKS_CATALOG = "compass_validation"
DATABRICKS_SCHEMA = "compass_validation_sandbox"  # distinctive, because teardown drops it with CASCADE
DATABRICKS_PRINCIPAL_NAME = "compass-validation-svc"
TOKEN_SECONDS = 3600
ROWS = 100


class ProvisioningError(Exception):
    """Setup or teardown failed. `code` names the step and the error type only, never a message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Provisioned:
    config: SnowflakeConfig | DatabricksConfig
    steps: int


# ------------------------------------------------------------------------------------------------------------ Snowflake

_T = f"{SNOWFLAKE_DB}.{SNOWFLAKE_SCHEMA}"
SNOWFLAKE_SETUP = (
    f"CREATE DATABASE IF NOT EXISTS {SNOWFLAKE_DB}",
    f"CREATE SCHEMA IF NOT EXISTS {_T}",
    f"CREATE RESOURCE MONITOR IF NOT EXISTS {SNOWFLAKE_MONITOR} WITH CREDIT_QUOTA = 1 "
    "TRIGGERS ON 80 PERCENT DO NOTIFY ON 100 PERCENT DO SUSPEND",
    f"CREATE WAREHOUSE IF NOT EXISTS {SNOWFLAKE_WAREHOUSE} WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE "
    f"INITIALLY_SUSPENDED = TRUE RESOURCE_MONITOR = {SNOWFLAKE_MONITOR}",
    f"USE WAREHOUSE {SNOWFLAKE_WAREHOUSE}",
    # Quoted lower case on purpose: the Snowflake compiler quotes and keeps case, so the certified sales-core@v1 works unchanged.
    f'CREATE OR REPLACE TABLE {_T}."sales_orders" ("id" VARCHAR NOT NULL, "amount" NUMBER(12, 2) NOT NULL, '
    '"status" VARCHAR NOT NULL, "created_at" TIMESTAMP_TZ NOT NULL)',
    f"INSERT INTO {_T}.\"sales_orders\" SELECT 'o' || n, n::NUMBER(12, 2), IFF(MOD(n, 5) = 0, 'refunded', 'paid'), "
    "DATEADD(hour, n * 20, '2024-01-01 00:00:00 +00:00'::TIMESTAMP_TZ) "
    f"FROM (SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS n FROM TABLE(GENERATOR(ROWCOUNT => {ROWS})))",
    f"CREATE ROLE IF NOT EXISTS {SNOWFLAKE_ROLE}",
    f"GRANT USAGE ON DATABASE {SNOWFLAKE_DB} TO ROLE {SNOWFLAKE_ROLE}",
    f"GRANT USAGE ON SCHEMA {_T} TO ROLE {SNOWFLAKE_ROLE}",
    f'GRANT SELECT ON TABLE {_T}."sales_orders" TO ROLE {SNOWFLAKE_ROLE}',
    f"GRANT USAGE ON WAREHOUSE {SNOWFLAKE_WAREHOUSE} TO ROLE {SNOWFLAKE_ROLE}",
    f"CREATE USER IF NOT EXISTS {SNOWFLAKE_USER} TYPE = SERVICE DEFAULT_ROLE = {SNOWFLAKE_ROLE} "
    f"DEFAULT_WAREHOUSE = {SNOWFLAKE_WAREHOUSE}",
    f"GRANT ROLE {SNOWFLAKE_ROLE} TO USER {SNOWFLAKE_USER}",
)
SNOWFLAKE_TEARDOWN = (
    f"DROP USER IF EXISTS {SNOWFLAKE_USER}",
    f"DROP ROLE IF EXISTS {SNOWFLAKE_ROLE}",
    f"DROP WAREHOUSE IF EXISTS {SNOWFLAKE_WAREHOUSE}",
    f"DROP RESOURCE MONITOR IF EXISTS {SNOWFLAKE_MONITOR}",
    f"DROP DATABASE IF EXISTS {SNOWFLAKE_DB}",
)


def new_key_pair() -> tuple[bytes, str]:
    """A fresh 2048-bit RSA key: the private key as PKCS8 DER (held in memory) and the public key body for Snowflake."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, base64.b64encode(public).decode("ascii")


def _run_all(conn: Any, statements: tuple[str, ...], phase: str) -> int:
    done = 0
    for index, statement in enumerate(statements, start=1):
        cursor = conn.cursor()
        try:
            cursor.execute(statement)
        except Exception as exc:  # noqa: BLE001 - the type and step only; a driver message can carry SQL
            raise ProvisioningError(f"{phase}_step_{index}:{type(exc).__name__}"[:60]) from None
        finally:
            cursor.close()
        done += 1
    return done


class SnowflakeProvisioner:
    """`conn` is an admin connection (a role that can create a resource monitor, in practice ACCOUNTADMIN)."""

    def __init__(self, conn: Any, *, account: str, key_pair: Callable[[], tuple[bytes, str]] = new_key_pair) -> None:
        self._conn, self._account, self._key_pair = conn, account, key_pair

    def setup(self) -> Provisioned:
        private_der, public_body = self._key_pair()
        steps = _run_all(self._conn, SNOWFLAKE_SETUP, "setup")
        steps += _run_all(self._conn, (f"ALTER USER {SNOWFLAKE_USER} SET RSA_PUBLIC_KEY = '{public_body}'",), "setup")
        config = SnowflakeConfig(
            account=self._account, user=SNOWFLAKE_USER, warehouse=SNOWFLAKE_WAREHOUSE, database=SNOWFLAKE_DB,
            schema=SNOWFLAKE_SCHEMA, role=SNOWFLAKE_ROLE, private_key_der=private_der,
        )
        return Provisioned(config, steps)

    def teardown(self) -> int:
        failures = 0
        done = 0
        for statement in SNOWFLAKE_TEARDOWN:  # every drop is attempted even if an earlier one fails
            try:
                done += _run_all(self._conn, (statement,), "teardown")
            except ProvisioningError:
                failures += 1
        if failures:
            raise ProvisioningError(f"teardown_incomplete:{failures}")
        return done

    def close(self) -> None:
        self._conn.close()


# ----------------------------------------------------------------------------------------------------------- Databricks


class DatabricksProvisioner:
    """`client` is a `databricks.sdk.WorkspaceClient`-shaped object signed in as a workspace admin. It never edits the warehouse."""

    def __init__(
        self, client: Any, *, host: str, warehouse_id: str, catalog: str = DATABRICKS_CATALOG, sql_types: Any | None = None
    ) -> None:
        self._c, self._host, self._warehouse_id, self._catalog = client, host, warehouse_id, catalog
        self._types = sql_types
        self._schema_created = False
        self._principal_id: str | None = None
        self._token_id: str | None = None
        self._created_catalog = False
        self._steps = 0

    def _sql(self, statement: str, step: str) -> list[list[Any]]:
        try:
            response = self._c.statement_execution.execute_statement(
                statement=statement, warehouse_id=self._warehouse_id, wait_timeout="50s"
            )
            state = getattr(getattr(response.status, "state", None), "name", str(response.status.state))
            if state != "SUCCEEDED":
                raise RuntimeError(state)
            self._steps += 1
            return list(getattr(getattr(response, "result", None), "data_array", None) or [])
        except Exception as exc:  # noqa: BLE001 - never echo the driver or API message
            raise ProvisioningError(f"{step}:{type(exc).__name__}"[:60]) from None

    def setup(self) -> Provisioned:
        c = f"`{self._catalog}`"
        if self._catalog == DATABRICKS_CATALOG:  # our own catalog: create it, and drop it later only if this run created it
            existed = bool(self._sql(f"SHOW CATALOGS LIKE '{DATABRICKS_CATALOG}'", "setup_check_catalog"))
            self._sql(f"CREATE CATALOG IF NOT EXISTS {c}", "setup_catalog")
            self._created_catalog = not existed
        # An existing catalog the owner named is never created or dropped: only the schema inside it is ours.
        self._sql(f"CREATE SCHEMA IF NOT EXISTS {c}.`{DATABRICKS_SCHEMA}`", "setup_schema")
        self._schema_created = True
        table = f"{c}.`{DATABRICKS_SCHEMA}`.`sales_orders`"
        self._sql(
            f"CREATE OR REPLACE TABLE {table} (id STRING NOT NULL, amount DECIMAL(12, 2) NOT NULL, status STRING NOT NULL, "
            "created_at TIMESTAMP NOT NULL)", "setup_table",
        )
        self._sql(
            f"INSERT INTO {table} SELECT concat('o', id), CAST(id AS DECIMAL(12, 2)), IF(id % 5 = 0, 'refunded', 'paid'), "
            f"timestampadd(HOUR, CAST(id * 20 AS INT), TIMESTAMP '2024-01-01 00:00:00+00:00') FROM range(1, {ROWS + 1})", "setup_rows",
        )
        try:
            principal = self._c.service_principals.create(display_name=DATABRICKS_PRINCIPAL_NAME)
            self._principal_id, application_id = str(principal.id), str(principal.application_id)
        except Exception as exc:  # noqa: BLE001
            raise ProvisioningError(f"setup_principal:{type(exc).__name__}"[:60]) from None
        who = f"`{application_id}`"
        self._sql(f"GRANT USE CATALOG ON CATALOG {c} TO {who}", "setup_grant_catalog")
        self._sql(f"GRANT USE SCHEMA ON SCHEMA {c}.`{DATABRICKS_SCHEMA}` TO {who}", "setup_grant_schema")
        self._sql(f"GRANT SELECT ON TABLE {table} TO {who}", "setup_grant_table")
        types = self._types
        try:
            if types is None:
                types = _sdk_types()  # lazy: only a live run needs the SDK
            self._c.warehouses.update_permissions(
                self._warehouse_id,
                access_control_list=[
                    types.WarehouseAccessControlRequest(
                        service_principal_name=application_id, permission_level=types.WarehousePermissionLevel.CAN_USE
                    )
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise ProvisioningError(f"setup_warehouse_access:{type(exc).__name__}"[:60]) from None
        try:
            # A token can only be created for a principal that is allowed to use tokens (found live: PermissionDenied without this).
            self._c.token_management.update_permissions(
                access_control_list=[
                    types.TokenAccessControlRequest(
                        service_principal_name=application_id, permission_level=types.TokenPermissionLevel.CAN_USE
                    )
                ]
            )
        except Exception as exc:  # noqa: BLE001
            raise ProvisioningError(f"setup_token_permission:{type(exc).__name__}"[:60]) from None
        try:
            token = self._c.token_management.create_obo_token(
                application_id=application_id, lifetime_seconds=TOKEN_SECONDS, comment="compass validation (temporary)"
            )
            self._token_id = str(token.token_info.token_id)
            secret = str(token.token_value)
        except Exception as exc:  # noqa: BLE001
            raise ProvisioningError(f"setup_token:{type(exc).__name__}"[:60]) from None
        config = DatabricksConfig(
            server_hostname=self._host, http_path=f"/sql/1.0/warehouses/{self._warehouse_id}",
            catalog=self._catalog, schema=DATABRICKS_SCHEMA, access_token=secret,
        )
        return Provisioned(config, self._steps + 3)  # the SQL statements plus the principal, the warehouse permission and the token

    def teardown(self) -> int:
        failures, done = 0, 0
        calls: list[Callable[[], Any]] = []
        if self._token_id:
            calls.append(lambda: self._c.token_management.delete(self._token_id))
        if self._principal_id:
            calls.append(lambda: self._c.service_principals.delete(self._principal_id))
        if self._schema_created:  # nothing to drop if setup never got as far as the schema (a failed CREATE CATALOG, say)
            calls.append(lambda: self._sql(f"DROP SCHEMA IF EXISTS `{self._catalog}`.`{DATABRICKS_SCHEMA}` CASCADE", "teardown_schema"))
        if self._created_catalog:  # only a catalog this run created; one that existed before is left alone
            calls.append(lambda: self._sql(f"DROP CATALOG IF EXISTS `{DATABRICKS_CATALOG}` CASCADE", "teardown_catalog"))
        for call in calls:
            try:
                call()
                done += 1
            except Exception:  # noqa: BLE001 - keep going; report the count
                failures += 1
        if failures:
            raise ProvisioningError(f"teardown_incomplete:{failures}")
        return done

    def close(self) -> None:
        return None


def _sdk_types() -> Any:
    from types import SimpleNamespace

    from databricks.sdk.service import settings, sql

    return SimpleNamespace(
        WarehouseAccessControlRequest=sql.WarehouseAccessControlRequest,
        WarehousePermissionLevel=sql.WarehousePermissionLevel,
        TokenAccessControlRequest=settings.TokenAccessControlRequest,
        TokenPermissionLevel=settings.TokenPermissionLevel,
    )


def pick_warehouse(client: Any, name: str | None = None) -> str:
    """The SQL warehouse to run on. Chosen, never edited: by name if given, else the first one listed."""
    warehouses = list(client.warehouses.list())
    chosen = [w for w in warehouses if name is None or w.name == name]
    if not chosen:
        raise ProvisioningError("no_matching_warehouse")
    return str(chosen[0].id)


# ------------------------------------------------------------------------------------------------------- admin sign-in


def connect_snowflake_admin(account: str, admin_user: str, *, connector: Any | None = None) -> Any:
    """Browser SSO sign-in (`externalbrowser`): the person approves it in their browser; no password is handled here."""
    from app.execution.warehouse_sessions import _load_driver

    driver = _load_driver("snowflake.connector", "snowflake extra", connector)
    return driver.connect(account=account, user=admin_user, authenticator="externalbrowser", role="ACCOUNTADMIN", login_timeout=120)


def connect_databricks_admin(host: str, *, sdk: Any | None = None) -> Any:
    """OAuth browser sign-in as the person; the SDK opens the sign-in and keeps the token in memory."""
    if sdk is None:
        try:
            import databricks.sdk as sdk
        except ImportError:
            from app.execution.warehouse_sessions import WarehouseDriverMissing

            raise WarehouseDriverMissing("databricks.sdk is not installed; install requirements-warehouses.txt") from None
    return sdk.WorkspaceClient(host=f"https://{host}", auth_type="external-browser")
