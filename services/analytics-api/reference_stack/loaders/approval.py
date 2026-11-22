"""The run only starts when the program file records an approval whose scope names exactly this target. It reads the approval; it never writes one."""

from __future__ import annotations

from pathlib import Path

import yaml

PROGRAM = Path(__file__).resolve().parents[4] / "docs/execution/enterprise-analytics/agentic-data-stack-program.yaml"


class ApprovalError(Exception):
    pass


def _find(node: object, key: str) -> dict | None:
    if isinstance(node, dict):
        if key in node and isinstance(node[key], dict):
            return node[key]
        for v in node.values():
            hit = _find(v, key)
            if hit:
                return hit
    elif isinstance(node, list):
        for v in node:
            hit = _find(v, key)
            if hit:
                return hit
    return None


def check_approval(approval_id: str, host: str, catalog: str, schema: str, warehouse: str, cap_minutes: float, program: Path = PROGRAM, *, also: tuple[str, ...] = (), max_cap_minutes: float = 15.0) -> None:
    entry = _find(yaml.safe_load(program.read_text()), approval_id)
    if not entry or entry.get("status") != "approved" or entry.get("approved_by") != "user":
        raise ApprovalError("approval_not_recorded")
    scope = str(entry.get("scope", ""))
    for needle in (host, f"{catalog}.{schema}", warehouse, *also):
        if needle not in scope:
            raise ApprovalError("target_not_in_approval")
    if cap_minutes > max_cap_minutes:
        raise ApprovalError("cap_above_approved")
