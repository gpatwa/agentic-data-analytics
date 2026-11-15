"""A real `ExplainProbe` for the drafter's dry run (auto-ontology milestone 4a).

It reuses the governed `PostgresGateway`, so a drafted metric is checked the same way a production query is: AST validation and a
table allowlist, then `EXPLAIN (FORMAT JSON)` (never `ANALYZE`) inside a read-only transaction with a statement timeout, always
rolled back. The caller passes an engine for the read-only role. A failure surfaces as the gateway's `ExecutionError` or
`QueryRejected`, which the drafter reports by class name only.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.compiler.postgres import CompiledQuery
from app.execution.postgres_gateway import PostgresGateway
from app.ontology_draft.validate import ExplainProbe


def explain_probe(engine: Any, *, allowed_tables: Iterable[str]) -> ExplainProbe:
    """Return a probe that plans `(sql, parameters)` on `engine` and raises if the plan is refused or cannot be made."""
    gateway = PostgresGateway(engine, allowed_tables=allowed_tables)

    def probe(sql: str, parameters: dict) -> None:
        gateway.estimate(CompiledQuery(sql=sql, parameters=dict(parameters)))

    return probe


__all__ = ["explain_probe"]
