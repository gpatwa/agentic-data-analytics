"""Builds the Connect-on-Databricks pieces the app serves, from settings. Fails closed.

What it makes: a connection store, a custody for the per-connection secret (Key Vault when a vault URL is configured, otherwise an
in-memory stand-in that only development may use), the setup service behind `/api/v2/connect/databricks`, and the per-tenant
assets resolver the governed runtime asks (`connected_assets_resolver`), whose contract folder is `<registry root>/<tenant id>`.

Known limit, stated here so it is not mistaken for done: the connection store and the setup audit chain are in memory. A restart
forgets every connection (and leaves its vault secret behind), so production start-up is REFUSED until a persistent store exists.
Nothing here certifies a contract: the resolver reads only contracts a person certified in Git.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.connect.databricks_api import DatabricksSetups, make_session_factory
from app.connect.store import ConnectionRecord, ConnectionStore, TenantSecretStore
from app.connect.tenant_assets import connected_assets_resolver
from app.runtime.pilot_wiring import TenantAssets
from app.security.audit import AuditChain

_TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass
class DatabricksWiring:
    setups: DatabricksSetups
    session_factory: Callable[[ConnectionRecord], Any]
    assets_for_tenant: Callable[[str], TenantAssets | None]


def registry_for_tenant(root: Path) -> Callable[[str], Path | None]:
    """`<root>/<tenant id>`, only for a plain tenant id that stays inside the root; anything else has no registry."""

    def resolve(tenant_id: str) -> Path | None:
        if not _TENANT.match(tenant_id or "") or tenant_id in (".", ".."):
            return None
        folder = (root / tenant_id).resolve()
        if root.resolve() not in folder.parents or not folder.is_dir():
            return None
        return folder

    return resolve


def _secrets(settings: Any, vault_client: Any | None) -> Any:
    if settings.ANALYTICS_CONNECT_KEY_VAULT_URL:
        from app.connect.vault import KeyVaultSecretStore

        if vault_client is None:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            vault_client = SecretClient(vault_url=settings.ANALYTICS_CONNECT_KEY_VAULT_URL, credential=DefaultAzureCredential())
        return KeyVaultSecretStore(vault_client)
    if settings.ENV == "prod":
        raise RuntimeError("ANALYTICS_CONNECT_KEY_VAULT_URL is required in production: the in-memory secret store is for development only")
    return TenantSecretStore()


def build_databricks_wiring(settings: Any, *, vault_client: Any | None = None, connector: Any | None = None, sdk_core: Any | None = None) -> DatabricksWiring | None:
    """None when Connect on Databricks is not enabled. Raises, at start-up, for any configuration that would serve it unsafely."""
    if not settings.ANALYTICS_CONNECT_DATABRICKS_ENABLED:
        return None
    if not settings.ANALYTICS_V2_ENABLED:
        raise RuntimeError("ANALYTICS_CONNECT_DATABRICKS_ENABLED needs ANALYTICS_V2_ENABLED (the governed runtime and its identity check)")
    if settings.ENV == "prod":
        raise RuntimeError("Connect on Databricks is not available in production yet: the connection store is in memory and a restart would forget connections")
    root = (settings.ANALYTICS_CONNECT_REGISTRY_ROOT or "").strip()
    if not root or not Path(root).is_dir():
        raise RuntimeError("ANALYTICS_CONNECT_REGISTRY_ROOT must name an existing folder (one sub-folder per tenant holding its certified contract)")
    store, secrets = ConnectionStore(), _secrets(settings, vault_client)
    setups = DatabricksSetups(store=store, secrets=secrets, chain=AuditChain())
    session_factory = make_session_factory(secrets, connector=connector, sdk_core=sdk_core)
    assets = connected_assets_resolver(store, secrets, registry_for_tenant(Path(root)), **({"connector": connector} if connector is not None else {}))
    return DatabricksWiring(setups, session_factory, assets)


__all__ = ["DatabricksWiring", "build_databricks_wiring", "registry_for_tenant"]
