"""A readable, canonical diff of two versions of a certified contract: what a certifier needs to see before signing a new version.

Definitions (ids, aggregations, grains, filters, wording, synonyms) are shown as they are: they are authored contract text, not
data. Order never matters; only content does.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from packages.platform_contracts.semantic import SemanticContract

KINDS = ("datasets", "fields", "entities", "dimensions", "metrics", "joins", "filters", "policies")
_NOISE = {"version"}  # the version label itself changes on every release


def contract_digest(contract: SemanticContract) -> str:
    return hashlib.sha256(json.dumps(contract.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Change:
    kind: str
    item_id: str
    change: str  # added | removed | changed
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContractDiff:
    old_version: str
    new_version: str
    old_digest: str
    new_digest: str
    changes: tuple[Change, ...]

    @property
    def empty(self) -> bool:
        return not self.changes

    def of(self, kind: str, change: str | None = None) -> list[Change]:
        return [c for c in self.changes if c.kind == kind and (change is None or c.change == change)]


def _field_changes(old: dict[str, Any], new: dict[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        if isinstance(a, list) and isinstance(b, list) and all(isinstance(x, str) for x in a + b):
            gone, came = [x for x in a if x not in b], [x for x in b if x not in a]
            if gone or came:
                out.append(f"{key}: " + ", ".join([f"+{x}" for x in came] + [f"-{x}" for x in gone]))
            continue
        out.append(f"{key}: {json.dumps(a, sort_keys=True)} -> {json.dumps(b, sort_keys=True)}")
    return tuple(out)


def diff_contracts(old: SemanticContract, new: SemanticContract) -> ContractDiff:
    if old.id != new.id or old.tenant_id != new.tenant_id:
        raise ValueError("contracts_are_not_versions_of_one_contract")
    a, b = old.model_dump(mode="json"), new.model_dump(mode="json")
    changes: list[Change] = []
    for kind in KINDS:
        old_items = {i["id"]: i for i in a.get(kind, [])}
        new_items = {i["id"]: i for i in b.get(kind, [])}
        for item_id in sorted(old_items.keys() - new_items.keys()):
            changes.append(Change(kind, item_id, "removed"))
        for item_id in sorted(new_items.keys() - old_items.keys()):
            changes.append(Change(kind, item_id, "added"))
        for item_id in sorted(old_items.keys() & new_items.keys()):
            details = _field_changes(old_items[item_id], new_items[item_id])
            if details:
                changes.append(Change(kind, item_id, "changed", details))
    for key in ("domain", "owners", "metadata"):
        details = _field_changes({key: a.get(key)}, {key: b.get(key)})
        if details:
            changes.append(Change("contract", key, "changed", details))
    return ContractDiff(old.version, new.version, contract_digest(old), contract_digest(new), tuple(changes))


def render_diff(diff: ContractDiff) -> str:
    lines = [f"## Contract changes: `{diff.old_version}` to `{diff.new_version}`", "", f"- old digest `{diff.old_digest[:12]}`, new digest `{diff.new_digest[:12]}`"]
    if diff.empty:
        return "\n".join(lines + ["", "No change in definitions."]) + "\n"
    lines.append("")
    for c in diff.changes:
        lines.append(f"- {c.change} {c.kind[:-1] if c.kind.endswith('s') else c.kind} `{c.item_id}`" + (": " + "; ".join(c.details) if c.details else ""))
    return "\n".join(lines) + "\n"


__all__ = ["Change", "ContractDiff", "contract_digest", "diff_contracts", "render_diff"]
