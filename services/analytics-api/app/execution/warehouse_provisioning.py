"""Sandbox provisioning for the warehouse live validation: creates the whole sandbox, then removes it. No manual steps.

This is an ADMIN tool and is deliberately separate from the product's query path, which stays read-only. It is used only by
`warehouse_validation --provision` / `--sweep`, never imported by the API. Every identifier it creates or drops is a fixed
constant (or a random run id), never built from input.

Safety rules (plan P1, docs/DECISION_REGISTER_2026-11-16.md; each one came from reading this code with a security eye):
- **It never adopts an object it did not create.** Before creating anything it checks every name. A name that already exists and
  does not carry this tool's tag (`TAG`, in the object's comment) stops the run with `name_taken`; one that does carry the tag is a
  leftover of an interrupted run and stops it with `leftovers_found` (clear it with `--sweep`). Creation uses plain `CREATE`,
  never `IF NOT EXISTS` or `OR REPLACE`, so a race fails instead of taking an object over.
- **It drops only what this run created.** Teardown tracks what setup actually created and removes exactly that; a failed setup
  step, a pre-existing object and a catalog the owner named are never dropped. `--sweep` removes tagged leftovers only.
- **It never changes a warehouse's settings.** On Databricks the SQL warehouse must be named (there is no default), and setup
  only adds `CAN_USE` for the temporary principal; after teardown it checks that the entry is gone and says so if it is not.
- **No credential is written anywhere.** The read-only identity gets a fresh credential held only in memory (a new RSA key pair,
  or a short-lived Databricks secret), and admin sign-in is a browser SSO prompt. A crash therefore leaves objects behind but no
  usable credential: the private key or secret died with the process.

The admin role defaults to ACCOUNTADMIN (a resource monitor needs it); that is acceptable for a sandbox and is configurable, but a
customer-warehouse mode would need narrower roles.

Verified live on 2026-11-15/16 (the owner's accounts): the Databricks automatic path, end to end. NOT verified live: the Snowflake
automatic path (the account has no SSO, so its sandbox was set up from the signed-in console with these statements), and everything
added for P1: the tag comments, the existence checks (`SHOW ... LIKE`, `DESCRIBE SCHEMA EXTENDED`), the sweep and the permission
check. Those follow vendor documentation and the installed SDK's signatures only, and need a sandbox run with the owner's approval.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.execution.warehouse_sessions import DatabricksConfig, SnowflakeConfig

TAG = "compass-validation-sandbox"  # in the comment of every object this tool creates; the only thing it trusts when removing
SNOWFLAKE_DB = "COMPASS_VALIDATION"
SNOWFLAKE_SCHEMA = "SANDBOX"
SNOWFLAKE_WAREHOUSE = "COMPASS_VALIDATION_WH"
SNOWFLAKE_MONITOR = "COMPASS_VALIDATION_RM"
SNOWFLAKE_ROLE = "COMPASS_VALIDATION_RO"
SNOWFLAKE_USER = "COMPASS_VALIDATION_SVC"
DATABRICKS_CATALOG = "compass_validation"
DATABRICKS_SCHEMA = "compass_validation_sandbox"  # distinctive, because teardown drops it with CASCADE
DATABRICKS_PRINCIPAL_PREFIX = "compass-validation-"  # the run id follows, so a principal name never collides
TOKEN_SECONDS = 3600
ROWS = 100


class ProvisioningError(Exception):
    """Setup, teardown or a check failed. `code` names the step and the error type only, never a message. `names` lists the objects
    involved by name (fixed constants or run ids, never a credential), so the caller can tell the person exactly what is affected."""

    def __init__(self, code: str, names: Sequence[str] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.names = tuple(names)


@dataclass
class Provisioned:
    config: SnowflakeConfig | DatabricksConfig
    steps: int


@dataclass
class SweepResult:
    dropped: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # exists, but not carrying our tag: left alone


# ------------------------------------------------------------------------------------------------------------ Snowflake

_T = f"{SNOWFLAKE_DB}.{SNOWFLAKE_SCHEMA}"
_C = f"COMMENT = '{TAG}'"
# (kind created by this step or None, statement). Plain CREATE on purpose: never IF NOT EXISTS, never OR REPLACE.
SNOWFLAKE_SETUP: tuple[tuple[str | None, str], ...] = (
    ("database", f"CREATE DATABASE {SNOWFLAKE_DB} {_C}"),
    (None, f"CREATE SCHEMA {_T}"),
    ("resource monitor", f"CREATE RESOURCE MONITOR {SNOWFLAKE_MONITOR} WITH CREDIT_QUOTA = 1 "
     "TRIGGERS ON 80 PERCENT DO NOTIFY ON 100 PERCENT DO SUSPEND"),
    ("warehouse", f"CREATE WAREHOUSE {SNOWFLAKE_WAREHOUSE} WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE "
     f"INITIALLY_SUSPENDED = TRUE RESOURCE_MONITOR = {SNOWFLAKE_MONITOR} {_C}"),
    (None, f"USE WAREHOUSE {SNOWFLAKE_WAREHOUSE}"),
    # Quoted lower case on purpose: the Snowflake compiler quotes and keeps case, so the certified sales-core@v1 works unchanged.
    (None, f'CREATE TABLE {_T}."sales_orders" ("id" VARCHAR NOT NULL, "amount" NUMBER(12, 2) NOT NULL, '
     '"status" VARCHAR NOT NULL, "created_at" TIMESTAMP_TZ NOT NULL)'),
    (None, f"INSERT INTO {_T}.\"sales_orders\" SELECT 'o' || n, n::NUMBER(12, 2), IFF(MOD(n, 5) = 0, 'refunded', 'paid'), "
     "DATEADD(hour, n * 20, '2024-01-01 00:00:00 +00:00'::TIMESTAMP_TZ) "
     f"FROM (SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS n FROM TABLE(GENERATOR(ROWCOUNT => {ROWS})))"),
    ("role", f"CREATE ROLE {SNOWFLAKE_ROLE} {_C}"),
    (None, f"GRANT USAGE ON DATABASE {SNOWFLAKE_DB} TO ROLE {SNOWFLAKE_ROLE}"),
    (None, f"GRANT USAGE ON SCHEMA {_T} TO ROLE {SNOWFLAKE_ROLE}"),
    (None, f'GRANT SELECT ON TABLE {_T}."sales_orders" TO ROLE {SNOWFLAKE_ROLE}'),
    (None, f"GRANT USAGE ON WAREHOUSE {SNOWFLAKE_WAREHOUSE} TO ROLE {SNOWFLAKE_ROLE}"),
    ("user", f"CREATE USER {SNOWFLAKE_USER} TYPE = SERVICE DEFAULT_ROLE = {SNOWFLAKE_ROLE} "
     f"DEFAULT_WAREHOUSE = {SNOWFLAKE_WAREHOUSE} {_C}"),
    (None, f"GRANT ROLE {SNOWFLAKE_ROLE} TO USER {SNOWFLAKE_USER}"),
)
# (kind, name, SHOW statement, whether the object has a comment to read). A resource monitor has no comment property.
SNOWFLAKE_OBJECTS: tuple[tuple[str, str, str, bool], ...] = (
    ("user", SNOWFLAKE_USER, "SHOW USERS LIKE '{}'", True),
    ("role", SNOWFLAKE_ROLE, "SHOW ROLES LIKE '{}'", True),
    ("warehouse", SNOWFLAKE_WAREHOUSE, "SHOW WAREHOUSES LIKE '{}'", True),
    ("resource monitor", SNOWFLAKE_MONITOR, "SHOW RESOURCE MONITORS LIKE '{}'", False),
    ("database", SNOWFLAKE_DB, "SHOW DATABASES LIKE '{}'", True),
)  # also the order teardown drops them in
_DROP = {"user": "DROP USER IF EXISTS {}", "role": "DROP ROLE IF EXISTS {}", "warehouse": "DROP WAREHOUSE IF EXISTS {}",
         "resource monitor": "DROP RESOURCE MONITOR IF EXISTS {}", "database": "DROP DATABASE IF EXISTS {}"}


def new_key_pair() -> tuple[bytes, str]:
    """A fresh 2048-bit RSA key: the private key as PKCS8 DER (held in memory) and the public key body for Snowflake."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, base64.b64encode(public).decode("ascii")


def _exec(conn: Any, statement: str, code: str) -> list[dict[str, Any]]:
    """Run one statement on an admin connection; return its rows as dicts keyed by lower-case column name. Errors become a code."""
    cursor = conn.cursor()
    try:
        cursor.execute(statement)
        if getattr(cursor, "description", None):
            names = [str(d[0]).lower() for d in cursor.description]
            return [dict(zip(names, row, strict=False)) for row in cursor.fetchall()]
        return []
    except Exception as exc:  # noqa: BLE001 - the type and step only; a driver message can carry SQL
        raise ProvisioningError(f"{code}:{type(exc).__name__}"[:60]) from None
    finally:
        cursor.close()


class SnowflakeProvisioner:
    """`conn` is an admin connection (a role that can create a resource monitor, in practice ACCOUNTADMIN)."""

    def __init__(self, conn: Any, *, account: str, key_pair: Callable[[], tuple[bytes, str]] = new_key_pair) -> None:
        self._conn, self._account, self._key_pair = conn, account, key_pair
        self._created: list[str] = []  # kinds this run created, in order

    def _existing(self) -> dict[str, dict[str, Any]]:
        """Each of our fixed names that already exists: kind -> its row (the exact name only; SHOW ... LIKE treats _ as a wildcard)."""
        found: dict[str, dict[str, Any]] = {}
        for kind, name, show, _ in SNOWFLAKE_OBJECTS:
            for row in _exec(self._conn, show.format(name), f"check_{kind.replace(' ', '_')}"):
                if str(row.get("name", "")).upper() == name:
                    found[kind] = row
        return found

    def _classify(self, found: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
        """Split what exists into (ours, not ours). Ours = carries TAG. A resource monitor has no comment, so it counts as ours only
        when our tagged warehouse exists beside it."""
        ours, other = [], []
        tagged_warehouse = "warehouse" in found and TAG in str(found["warehouse"].get("comment", ""))
        for kind, name, _, has_comment in SNOWFLAKE_OBJECTS:
            if kind not in found:
                continue
            mine = TAG in str(found[kind].get("comment", "")) if has_comment else tagged_warehouse
            (ours if mine else other).append(f"{kind} {name}")
        return ours, other

    def preflight(self) -> None:
        ours, other = self._classify(self._existing())
        if other:
            raise ProvisioningError("name_taken", other)
        if ours:
            raise ProvisioningError("leftovers_found", ours)

    def setup(self) -> Provisioned:
        self.preflight()
        private_der, public_body = self._key_pair()
        steps = 0
        for index, (kind, statement) in enumerate(SNOWFLAKE_SETUP, start=1):
            _exec(self._conn, statement, f"setup_step_{index}")
            if kind:
                self._created.append(kind)
            steps += 1
        _exec(self._conn, f"ALTER USER {SNOWFLAKE_USER} SET RSA_PUBLIC_KEY = '{public_body}'", f"setup_step_{steps + 1}")
        config = SnowflakeConfig(
            account=self._account, user=SNOWFLAKE_USER, warehouse=SNOWFLAKE_WAREHOUSE, database=SNOWFLAKE_DB,
            schema=SNOWFLAKE_SCHEMA, role=SNOWFLAKE_ROLE, private_key_der=private_der,
        )
        return Provisioned(config, steps + 1)

    def teardown(self) -> int:
        """Drop exactly what this run created, every one attempted even if an earlier drop fails."""
        left, done = [], 0
        for kind, name, _, _ in SNOWFLAKE_OBJECTS:
            if kind not in self._created:
                continue
            try:
                _exec(self._conn, _DROP[kind].format(name), "teardown")
                done += 1
            except ProvisioningError:
                left.append(f"{kind} {name}")
        if left:
            raise ProvisioningError(f"teardown_incomplete:{len(left)}", left)
        return done

    def sweep(self) -> SweepResult:
        """Remove tagged leftovers of an interrupted run. Anything that exists but is not tagged is reported and left alone."""
        result = SweepResult()
        ours, other = self._classify(self._existing())
        result.skipped = other
        for kind, name, _, _ in SNOWFLAKE_OBJECTS:
            if f"{kind} {name}" in ours:
                _exec(self._conn, _DROP[kind].format(name), "sweep")
                result.dropped.append(f"{kind} {name}")
        return result

    def close(self) -> None:
        self._conn.close()


# ----------------------------------------------------------------------------------------------------------- Databricks


class DatabricksProvisioner:
    """`client` is a `databricks.sdk.WorkspaceClient`-shaped object signed in as a workspace admin. It never changes a warehouse's
    settings: it only adds `CAN_USE` for its temporary principal, and checks after teardown that the entry is gone."""

    def __init__(
        self,
        client: Any,
        *,
        host: str,
        warehouse_id: str,
        catalog: str = DATABRICKS_CATALOG,
        sql_types: Any | None = None,
        auth: str = "oauth_m2m",
        run_id: str | None = None,
    ) -> None:
        if auth not in ("oauth_m2m", "token"):
            raise ValueError("auth must be oauth_m2m or token")
        self._c, self._host, self._warehouse_id, self._catalog, self._auth = client, host, warehouse_id, catalog, auth
        self._run_id = run_id or uuid.uuid4().hex[:8]
        self._secret_id: str | None = None
        self._types = sql_types
        self._schema_created = False
        self._principal_id: str | None = None
        self._application_id: str | None = None
        self._token_id: str | None = None
        self._created_catalog = False
        self._steps = 0

    @property
    def principal_name(self) -> str:
        return f"{DATABRICKS_PRINCIPAL_PREFIX}{self._run_id}"

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

    def _schema_state(self) -> str:
        """'absent', 'ours' (carries TAG) or 'other', for the sandbox schema in the chosen catalog. Unreadable counts as 'other'."""
        c = f"`{self._catalog}`"
        rows = self._sql(f"SHOW SCHEMAS IN {c} LIKE '{DATABRICKS_SCHEMA}'", "check_schema")
        if not any(DATABRICKS_SCHEMA in [str(cell) for cell in row] for row in rows):  # LIKE treats _ as a wildcard: match exactly
            return "absent"
        detail = self._sql(f"DESCRIBE SCHEMA EXTENDED {c}.`{DATABRICKS_SCHEMA}`", "check_schema_comment")
        return "ours" if any(str(row[0]).strip().lower() == "comment" and TAG in " ".join(map(str, row[1:])) for row in detail if row) else "other"

    def _principals(self) -> list[Any]:
        try:
            return [p for p in self._c.service_principals.list() if str(getattr(p, "display_name", "")).startswith(DATABRICKS_PRINCIPAL_PREFIX)]
        except Exception as exc:  # noqa: BLE001
            raise ProvisioningError(f"check_principals:{type(exc).__name__}"[:60]) from None

    def preflight(self, *, catalog_exists: bool) -> None:
        ours, other = [], []
        if catalog_exists:  # no catalog means no schema to collide with
            state = self._schema_state()
            if state == "other":
                other.append(f"schema {self._catalog}.{DATABRICKS_SCHEMA}")
            elif state == "ours":
                ours.append(f"schema {self._catalog}.{DATABRICKS_SCHEMA}")
        ours += [f"service principal {p.display_name}" for p in self._principals()]
        if other:
            raise ProvisioningError("name_taken", other)
        if ours:
            raise ProvisioningError("leftovers_found", ours)

    def setup(self) -> Provisioned:
        c = f"`{self._catalog}`"
        catalog_exists = True
        if self._catalog == DATABRICKS_CATALOG:  # our own catalog: create it, and drop it later only if this run created it
            catalog_exists = bool(self._sql(f"SHOW CATALOGS LIKE '{DATABRICKS_CATALOG}'", "setup_check_catalog"))
        self.preflight(catalog_exists=catalog_exists)
        if self._catalog == DATABRICKS_CATALOG:
            self._sql(f"CREATE CATALOG IF NOT EXISTS {c}", "setup_catalog")
            self._created_catalog = not catalog_exists
        # An existing catalog the owner named is never created or dropped: only the schema inside it is ours. The schema is created
        # with plain CREATE, so if it appeared since the check the run fails instead of adopting it.
        self._sql(f"CREATE SCHEMA {c}.`{DATABRICKS_SCHEMA}` COMMENT '{TAG}'", "setup_schema")
        self._schema_created = True
        table = f"{c}.`{DATABRICKS_SCHEMA}`.`sales_orders`"
        self._sql(
            f"CREATE TABLE {table} (id STRING NOT NULL, amount DECIMAL(12, 2) NOT NULL, status STRING NOT NULL, "
            "created_at TIMESTAMP NOT NULL)", "setup_table",
        )
        self._sql(
            f"INSERT INTO {table} SELECT concat('o', id), CAST(id AS DECIMAL(12, 2)), IF(id % 5 = 0, 'refunded', 'paid'), "
            f"timestampadd(HOUR, CAST(id * 20 AS INT), TIMESTAMP '2024-01-01 00:00:00+00:00') FROM range(1, {ROWS + 1})", "setup_rows",
        )
        try:
            principal = self._c.service_principals.create(display_name=self.principal_name)
            self._principal_id, application_id = str(principal.id), str(principal.application_id)
            self._application_id = application_id
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
        if self._auth == "oauth_m2m":
            # The recommended method: the principal's client credentials, exchanged by the SDK for short-lived access tokens.
            try:
                created = self._c.service_principal_secrets_proxy.create(
                    service_principal_id=self._principal_id, lifetime=f"{TOKEN_SECONDS}s"
                )
                self._secret_id = str(created.id)
                secret = str(created.secret)
            except Exception as exc:  # noqa: BLE001
                raise ProvisioningError(f"setup_secret:{type(exc).__name__}"[:60]) from None
        else:
            try:
                # A token can only be created for a principal that is allowed to use tokens (found live: PermissionDenied otherwise).
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
        credentials = {"client_id": application_id, "client_secret": secret} if self._auth == "oauth_m2m" else {"access_token": secret}
        config = DatabricksConfig(
            server_hostname=self._host, http_path=f"/sql/1.0/warehouses/{self._warehouse_id}",
            catalog=self._catalog, schema=DATABRICKS_SCHEMA, **credentials,
        )
        return Provisioned(config, self._steps + 3)  # the SQL statements plus the principal, the warehouse permission and the token

    def _warehouse_permission_remains(self) -> bool:
        """After the principal is deleted, is its CAN_USE entry still on the warehouse? Read-only; unknown counts as 'no'."""
        if not self._application_id:
            return False
        try:
            acl = self._c.warehouses.get_permissions(self._warehouse_id).access_control_list or []
        except Exception:  # noqa: BLE001 - cannot read it: do not claim a leftover we cannot see
            return False
        return any(getattr(entry, "service_principal_name", None) == self._application_id for entry in acl)

    def teardown(self) -> int:
        left: list[str] = []
        done = 0
        calls: list[tuple[str, Callable[[], Any]]] = []
        if self._token_id:
            calls.append(("token", lambda: self._c.token_management.delete(self._token_id)))
        if self._secret_id and self._principal_id:  # the secret goes before the principal it belongs to
            calls.append(("secret", lambda: self._c.service_principal_secrets_proxy.delete(self._principal_id, self._secret_id)))
        if self._principal_id:
            calls.append((f"service principal {self.principal_name}", lambda: self._c.service_principals.delete(self._principal_id)))
        if self._schema_created:  # only a schema THIS run created (plain CREATE succeeded); never one that already existed
            calls.append((f"schema {self._catalog}.{DATABRICKS_SCHEMA}", lambda: self._sql(
                f"DROP SCHEMA IF EXISTS `{self._catalog}`.`{DATABRICKS_SCHEMA}` CASCADE", "teardown_schema")))
        if self._created_catalog:  # only a catalog this run created; one that existed before is left alone
            calls.append((f"catalog {DATABRICKS_CATALOG}", lambda: self._sql(f"DROP CATALOG IF EXISTS `{DATABRICKS_CATALOG}` CASCADE", "teardown_catalog")))
        for label, call in calls:
            try:
                call()
                done += 1
            except Exception:  # noqa: BLE001 - keep going; report what is left
                left.append(label)
        if self._principal_id and not any(item.startswith("service principal") for item in left) and self._warehouse_permission_remains():
            left.append("warehouse permission for the deleted principal")
        if left:
            raise ProvisioningError(f"teardown_incomplete:{len(left)}", left)
        return done

    def sweep(self) -> SweepResult:
        """Remove leftovers of an interrupted run: our tagged schema and any principal named with our prefix. An untagged schema is
        reported and left alone. The catalog is never dropped here."""
        result = SweepResult()
        catalog_exists = True
        if self._catalog == DATABRICKS_CATALOG:
            catalog_exists = bool(self._sql(f"SHOW CATALOGS LIKE '{DATABRICKS_CATALOG}'", "sweep_check_catalog"))
        if catalog_exists:
            state = self._schema_state()
            name = f"schema {self._catalog}.{DATABRICKS_SCHEMA}"
            if state == "ours":
                self._sql(f"DROP SCHEMA IF EXISTS `{self._catalog}`.`{DATABRICKS_SCHEMA}` CASCADE", "sweep_schema")
                result.dropped.append(name)
            elif state == "other":
                result.skipped.append(name)
        for principal in self._principals():
            try:
                self._c.service_principals.delete(str(principal.id))
                result.dropped.append(f"service principal {principal.display_name}")
            except Exception as exc:  # noqa: BLE001
                raise ProvisioningError(f"sweep_principal:{type(exc).__name__}"[:60]) from None
        return result

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


def pick_warehouse(client: Any, name: str | None) -> str:
    """The SQL warehouse to run on, chosen BY NAME. There is no default: the first warehouse listed could be a production one."""
    if not name:
        raise ProvisioningError("warehouse_name_required")
    chosen = [w for w in client.warehouses.list() if w.name == name]
    if not chosen:
        raise ProvisioningError("no_matching_warehouse")
    if len(chosen) > 1:
        raise ProvisioningError("warehouse_name_ambiguous")
    return str(chosen[0].id)


# ------------------------------------------------------------------------------------------------------- admin sign-in


def connect_snowflake_admin(account: str, admin_user: str, *, role: str = "ACCOUNTADMIN", connector: Any | None = None) -> Any:
    """Browser SSO sign-in (`externalbrowser`): the person approves it in their browser; no password is handled here."""
    from app.execution.warehouse_sessions import _load_driver

    driver = _load_driver("snowflake.connector", "snowflake extra", connector)
    return driver.connect(account=account, user=admin_user, authenticator="externalbrowser", role=role, login_timeout=120)


def connect_databricks_admin(host: str, *, sdk: Any | None = None) -> Any:
    """OAuth browser sign-in as the person; the SDK opens the sign-in and keeps the token in memory."""
    if sdk is None:
        try:
            import databricks.sdk as sdk
        except ImportError:
            from app.execution.warehouse_sessions import WarehouseDriverMissing

            raise WarehouseDriverMissing("databricks.sdk is not installed; install requirements-warehouses.txt") from None
    return sdk.WorkspaceClient(host=f"https://{host}", auth_type="external-browser")
