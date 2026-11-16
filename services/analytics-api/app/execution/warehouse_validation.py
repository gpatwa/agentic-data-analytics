"""Live validation of the Snowflake and Databricks sessions against a SANDBOX warehouse (stage S6, docs/WAREHOUSE_S6_LIVE_VALIDATION_PLAN.md).

    cd services/analytics-api
    SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PRIVATE_KEY_PATH=... SNOWFLAKE_WAREHOUSE=... SNOWFLAKE_DATABASE=... \\
    SNOWFLAKE_SCHEMA=... SNOWFLAKE_ROLE=... \\
        python3 -m app.execution.warehouse_validation snowflake --allow-live --expect-target <account> --out report.md

With `--provision` there is nothing to set up by hand: it signs an admin in through the browser, creates the sandbox and a
read-only identity whose credential lives only in memory, runs the checks, and removes everything again in `finally`
(`warehouse_provisioning.py`). `--expect-target` is then the Snowflake account or the Databricks workspace host:

    python3 -m app.execution.warehouse_validation snowflake --allow-live --provision --admin-user <you> --expect-target <account> --out r.md
    python3 -m app.execution.warehouse_validation databricks --allow-live --provision --expect-target <host> --out r.md

This file never runs by itself and has not met a live warehouse. It refuses without `--allow-live` and without
`--expect-target` naming the one account or host it may use; a different one is refused before any connection and
neither value is printed. It reads credentials from the environment only. It runs eight checks in order and stops at the
first one that fails, because a failure is the finding and is reported, not retried. The report is numbers and codes only:
no query text, value, credential or host.

Statements are capped (`--max-statements`, default 30: each session also spends two on its settings). The slow statements
used for the timeout and cancel checks are fixed constants, read-only and never built from input.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.compiler import DatabricksCompilerAdapter, SnowflakeCompilerAdapter
from app.execution.connector_conformance import (  # noqa: F401 - re-exported: the live runner and its tests use these names
    CAPABILITIES,
    COLUMNS,
    INTENT,
    SLOW_SQL,
    TABLE,
    CheckResult,
    ConformanceSpec,
    EngineSpec,
    StopRule,
    ValidationReport,
    _ident,
    _rows,
    _rows_match,
    expected_monthly_revenue,
    render_markdown,
    run_conformance,
    run_validation,
    session_query_runner,
)
from app.execution.warehouse_gateway import DatabricksGateway, SnowflakeGateway
from app.execution.warehouse_provisioning import (
    DATABRICKS_CATALOG,
    DatabricksProvisioner,
    ProvisioningError,
    SnowflakeProvisioner,
    connect_databricks_admin,
    connect_snowflake_admin,
    pick_warehouse,
)
from app.execution.warehouse_sessions import (
    DatabricksConfig,
    DatabricksSession,
    SnowflakeConfig,
    SnowflakeSession,
    WarehouseConfigError,
    WarehouseDriverMissing,
)
from app.metadata.warehouse_providers import DatabricksMetadataProvider, SnowflakeMetadataProvider

_GRANTED = {"USAGE", "SELECT"}
_DATABRICKS_GRANTED = {"SELECT", "USE SCHEMA", "USE CATALOG", "BROWSE"}


def _snowflake_grants(config: SnowflakeConfig) -> Callable[[Any], str | None]:
    database = config.database.upper()

    def check(session: Any) -> str | None:
        rows = _rows(session, f"SHOW GRANTS TO ROLE {_ident(config.role)}", style="pyformat")
        if not rows:
            return "grants_not_visible"
        for row in rows:
            name = str(row.get("name", "")).upper()
            on = str(row.get("granted_on", "")).upper()
            inside = name == database or name.startswith(database + ".")
            if str(row.get("privilege", "")).upper() not in _GRANTED or not (on == "WAREHOUSE" or inside):
                return "extra_privilege"
        return None

    return check


def _databricks_grants(config: DatabricksConfig) -> Callable[[Any], str | None]:
    catalog, schema = _ident(config.catalog), _ident(config.schema)

    def check(session: Any) -> str | None:
        me = next(iter(_rows(session, "SELECT current_user() AS u")[0].values()))
        seen = 0
        for securable in (f"CATALOG `{catalog}`", f"SCHEMA `{catalog}`.`{schema}`", f"TABLE `{catalog}`.`{schema}`.`{TABLE}`"):
            for row in _rows(session, f"SHOW GRANTS ON {securable}"):
                if str(row.get("principal", "")) != str(me):
                    continue
                seen += 1
                if str(row.get("actiontype", "")).upper() not in _DATABRICKS_GRANTED:
                    return "extra_privilege"
        return None if seen else "grants_not_visible"  # direct grants only: group inheritance is not visible from here

    return check


def build_spec(engine: str, env: Mapping[str, str] | None = None) -> ConformanceSpec:
    if engine == "snowflake":
        return spec_from_config(SnowflakeConfig.from_env(env))
    if engine == "databricks":
        return spec_from_config(DatabricksConfig.from_env(env))
    raise ValueError("engine must be snowflake or databricks")


def spec_from_config(config: SnowflakeConfig | DatabricksConfig) -> ConformanceSpec:
    if isinstance(config, SnowflakeConfig):
        sf = config
        return ConformanceSpec(
            "snowflake", sf.account, lambda: SnowflakeSession(sf), SnowflakeGateway, SnowflakeCompilerAdapter, "pyformat",
            lambda run: SnowflakeMetadataProvider(run, database=sf.database, schema=sf.schema), _snowflake_grants(sf),
            tuple(
                s for s in (sf.private_key_passphrase, sf.private_key_path, sf.account,
                            base64.b64encode(sf.private_key_der).decode() if sf.private_key_der else None) if s
            ),
            capabilities=CAPABILITIES["snowflake"],
        )
    if isinstance(config, DatabricksConfig):
        db = config
        return ConformanceSpec(
            "databricks", db.server_hostname, lambda: DatabricksSession(db), DatabricksGateway, DatabricksCompilerAdapter, "named",
            lambda run: DatabricksMetadataProvider(run, catalog=db.catalog, schema=db.schema), _databricks_grants(db),
            (db.access_token, db.server_hostname, db.http_path),
            capabilities=CAPABILITIES["databricks"],
        )
    raise TypeError("unsupported config")





def make_provisioner(args: argparse.Namespace) -> Any:
    """Sign the admin in through the browser and return the provisioner. Replaced in tests."""
    if args.engine == "snowflake":
        if not args.admin_user:
            raise WarehouseConfigError("--admin-user is required for snowflake with --provision")
        return SnowflakeProvisioner(connect_snowflake_admin(args.expect_target, args.admin_user), account=args.expect_target)
    client = connect_databricks_admin(args.expect_target)
    return DatabricksProvisioner(
        client, host=args.expect_target, warehouse_id=pick_warehouse(client, args.databricks_warehouse),
        catalog=args.databricks_catalog or DATABRICKS_CATALOG,
    )


def _provisioned(args: argparse.Namespace) -> int:
    try:
        provisioner = make_provisioner(args)
    except (WarehouseConfigError, WarehouseDriverMissing, ProvisioningError) as exc:
        print(f"Not run. {getattr(exc, 'code', None) or exc}", file=sys.stderr)  # a code or a variable/flag name, never a value
        return 2
    except Exception as exc:  # noqa: BLE001 - the browser sign-in or the connection failed; the type only, never the message
        print(f"Not run. admin sign-in failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    report = ValidationReport(args.engine)
    notes: list[str] = []
    teardown_ok = True
    try:
        try:
            provisioned = provisioner.setup()
            notes.append(f"setup ok, {provisioned.steps} steps")
            report = run_validation(spec_from_config(provisioned.config), max_statements=args.max_statements, max_cost_mib=args.max_cost_mib)
        except ProvisioningError as exc:
            notes.append(f"setup failed: {exc.code}")
            report.provisioning_ok = False
    finally:
        try:
            notes.append(f"teardown ok, {provisioner.teardown()} steps")
        except ProvisioningError as exc:
            teardown_ok = False
            notes.append(f"TEARDOWN INCOMPLETE: {exc.code}")
        finally:
            provisioner.close()
    report.notes = notes
    report.provisioning_ok = report.provisioning_ok and teardown_ok
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(report))
    ok = sum(1 for c in report.checks if c.ok)
    print(f"{args.engine}: {ok} of {len(report.checks) + len(report.not_run)} checks ok, {report.statements} statements. {notes[-1]}. Report: {args.out}")
    if not teardown_ok:
        print("Teardown did not finish: remove the COMPASS_VALIDATION / compass_validation objects and the validation identity by hand.", file=sys.stderr)
    return 0 if report.passed else 1


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("engine", choices=("snowflake", "databricks"))
    parser.add_argument("--allow-live", action="store_true", help="required: this contacts a live sandbox warehouse")
    parser.add_argument("--expect-target", help="required: the one Snowflake account or Databricks host this run may use")
    parser.add_argument("--provision", action="store_true", help="create the sandbox and a read-only identity first, remove them after")
    parser.add_argument("--admin-user", help="Snowflake admin user for the browser sign-in (with --provision)")
    parser.add_argument("--databricks-warehouse", help="name of the SQL warehouse to use (with --provision); never edited")
    parser.add_argument("--databricks-catalog", help="an existing catalog to put the sandbox schema in (with --provision); never created or dropped")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-statements", type=int, default=30)
    parser.add_argument("--max-cost-mib", type=float, default=10.0)
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    if not args.allow_live or not args.expect_target:
        print("Not run. A live run needs --allow-live and --expect-target, and the owner's approval for this engine.", file=sys.stderr)
        return 2
    if not 1 <= args.max_statements <= 100 or not args.max_cost_mib > 0:
        print("Not run. --max-statements must be 1 to 100 and --max-cost-mib positive.", file=sys.stderr)
        return 2
    if args.provision:
        return _provisioned(args)
    try:
        spec = build_spec(args.engine, env)
    except WarehouseConfigError as exc:
        print(f"Not run. {exc}", file=sys.stderr)  # names variables only
        return 2
    if spec.target != args.expect_target:
        print("Not run. The configured target does not match --expect-target.", file=sys.stderr)  # neither value is printed
        return 2

    report = run_validation(spec, max_statements=args.max_statements, max_cost_mib=args.max_cost_mib)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(report))
    ok = sum(1 for c in report.checks if c.ok)
    print(f"{spec.name}: {ok} of {len(report.checks) + len(report.not_run)} checks ok, {report.statements} statements. Report: {args.out}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
