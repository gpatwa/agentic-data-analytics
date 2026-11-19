"""A production secret store adapter shaped like Azure Key Vault's `SecretClient` (the deployment is on Azure).

Same interface as `TenantSecretStore` (put, read, exists, version, delete), so it drops in wherever that is used. Tenant scoping is
in the secret's name: a hash of the tenant and of the name, so a read under another tenant is a different name and finds nothing,
and no customer text reaches a vault name. A value is never printed (`repr` shows no name or value) and no vault error text is
passed on: a failure is `KeyError` (not found) or one fixed word.

**Not verified against a real vault.** The client is injected; the tests use a fake with the SDK's method names
(`set_secret`, `get_secret`, `begin_delete_secret`). Vault access policy, soft-delete and purge settings, and throttling are the
deployment's to configure and test.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.connect.store import DeletionReceipt


class VaultUnavailable(RuntimeError):
    """The vault could not be reached or refused. Never carries the vault's message."""


def _h(text: str, n: int) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


class KeyVaultSecretStore:
    def __init__(self, client: Any, *, prefix: str = "compass") -> None:
        if not prefix.replace("-", "").isalnum():
            raise ValueError("the prefix may contain only letters, digits and dashes")
        self._c, self._prefix = client, prefix

    def __repr__(self) -> str:
        return "KeyVaultSecretStore()"

    def _name(self, tenant_id: str, name: str) -> str:
        if not tenant_id or not name:
            raise ValueError("tenant_id and name are required")
        return f"{self._prefix}-{_h(tenant_id, 20)}-{_h(name, 32)}"

    @staticmethod
    def _missing(exc: Exception) -> bool:
        return "notfound" in type(exc).__name__.lower()

    def put(self, tenant_id: str, name: str, value: str) -> str:
        if not value:
            raise ValueError("value is required")
        try:
            return str(self._c.set_secret(self._name(tenant_id, name), value).properties.version)
        except Exception:  # noqa: BLE001 - never echo the vault's message
            raise VaultUnavailable("vault_unavailable") from None

    def read(self, tenant_id: str, secret_name: str) -> str:
        try:
            return str(self._c.get_secret(self._name(tenant_id, secret_name)).value)
        except Exception as exc:  # noqa: BLE001
            if self._missing(exc):
                raise KeyError("secret was not found") from None
            raise VaultUnavailable("vault_unavailable") from None

    def exists(self, tenant_id: str, name: str) -> bool:
        try:
            self._c.get_secret(self._name(tenant_id, name))
            return True
        except Exception as exc:  # noqa: BLE001
            if self._missing(exc):
                return False
            raise VaultUnavailable("vault_unavailable") from None

    def version(self, tenant_id: str, name: str) -> str:
        try:
            return str(self._c.get_secret(self._name(tenant_id, name)).properties.version)
        except Exception as exc:  # noqa: BLE001
            if self._missing(exc):
                raise KeyError("secret was not found") from None
            raise VaultUnavailable("vault_unavailable") from None

    def delete(self, tenant_id: str, name: str) -> DeletionReceipt:
        try:
            self._c.begin_delete_secret(self._name(tenant_id, name)).wait()
        except Exception as exc:  # noqa: BLE001
            if self._missing(exc):
                raise KeyError("secret was not found") from None
            raise VaultUnavailable("vault_unavailable") from None
        return DeletionReceipt(tenant_id, name, datetime.now(timezone.utc))
