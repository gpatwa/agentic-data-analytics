"""A connected tenant's own governed-run assets (docs/design/CONNECT_CERTIFICATION_PLAN.md, C1).

`connected_assets_resolver` is handed to `GovernedRuntime.assets_for_tenant`. For a tenant with no live connection it returns `None`
(the pilot's assets, as before). For a tenant with exactly one active Databricks connection it assembles, per run:
the connection's gateway (`app.connect.runtime_gateway`), a Databricks compiler, and the **certified** contracts for that tenant's
tables from the tenant's registry folder, with the context built from them. Anything missing or wrong **raises**, so the run fails
closed:

- no certified contract for the tenant (a draft, a deprecated or an invalid file never counts);
- a contract that belongs to another tenant;
- a contract that uses any table outside the connection's allowlist, so a certified contract can never reach beyond what the admin
  named (checked again here on every assembly, not only when the contract was certified);
- more than one certified contract (the runtime serves one per tenant, as in the pilot).

Nothing here certifies anything. A contract is certified only by a person, in Git; this reads the result.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.compiler.adapter import DatabricksCompilerAdapter
from app.compiler.service import CertifiedIntentCompiler
from app.connect.gateway import ConnectionUnavailable
from app.connect.runtime_gateway import LIVE, connected_gateway_resolver
from app.connect.store import ConnectionRecord, ConnectionStore
from app.context.registry_context import CertifiedContracts, certified_vocabulary, context_from_contract
from app.runtime.pilot_wiring import TenantAssets
from app.semantic_registry import SemanticRegistry
from packages.platform_contracts.semantic import SemanticRegistryDocument


def contract_problem(document: SemanticRegistryDocument, record: ConnectionRecord) -> str | None:
    """A stable code if the contract may not run on this connection, else None."""
    if document.lifecycle != "certified":
        return "contract_not_certified"
    if document.contract.tenant_id != record.tenant_id:
        return "contract_tenant_mismatch"
    allowed = {t.lower() for t in record.allowed_tables}
    if not document.contract.datasets or any(d.physical_name.lower() not in allowed for d in document.contract.datasets):
        return "contract_outside_ceiling"
    return None


def connected_assets_resolver(
    store: ConnectionStore, secrets: Any, registry_for_tenant: Callable[[str], Path | str | None], *, ttl_seconds: float = 300.0, **options: Any
) -> Callable[[str], TenantAssets | None]:
    gateways = connected_gateway_resolver(store, secrets, ttl_seconds=ttl_seconds, **options)

    def resolve(tenant_id: str) -> TenantAssets | None:
        gateway = gateways(tenant_id)  # None, or raises: suspended, ambiguous, credential missing
        if gateway is None:
            return None
        record = next(r for r in store.list(tenant_id) if r.status in LIVE)
        if record.engine != "databricks":
            raise ConnectionUnavailable("engine_not_supported")
        root = registry_for_tenant(tenant_id)
        if root is None:
            raise ConnectionUnavailable("no_certified_contract")
        registry = SemanticRegistry(root)
        certified = [e.document for e in registry.list_entries() if e.document is not None and e.document.lifecycle == "certified"
                     and e.document.contract.tenant_id == tenant_id]
        if not certified:
            raise ConnectionUnavailable("no_certified_contract")
        if len(certified) > 1:
            raise ConnectionUnavailable("contract_ambiguous")
        problem = contract_problem(certified[0], record)
        if problem:
            raise ConnectionUnavailable(problem)
        context, ontology = context_from_contract(certified[0])
        return TenantAssets(gateway=gateway, compiler=CertifiedIntentCompiler(root, DatabricksCompilerAdapter()),
                            contracts=CertifiedContracts(certified), context=context, ontology=ontology,
                            vocabulary=certified_vocabulary(certified[0]))

    return resolve
