# agentic-data-analytics

Governed natural-language analytics: an LLM interprets a question into a typed, validated intent, and a deterministic compiler, policy checks and human review do the rest. Callable by agents through typed tools and MCP. Includes the public landing site.

## What is agentic here

**Agentic, governed.** A typed, durable graph runner (not LangGraph) with explicit node and edge allowlists, human review gates, and an agent tool surface plus an MCP adapter. The model does one bounded job: it turns a question into a typed `AnalyticalIntent` (validated, `extra="forbid"`) and drafts grounded explanations. Queries are compiled deterministically; the model never writes SQL.

**Not claimed:** real-model accuracy (every evaluation used a scripted model and emulated PostgreSQL) and certified semantics (that gate is the owner's).

## Layout

`services/analytics-api`, `apps/analytics-web`, `apps/landing`, `packages/platform_contracts`, `infra/terraform/azure-analytics-pilot`, `docs/execution/enterprise-analytics`.

## Test

```bash
cd services/analytics-api && PYTHONPATH=.:../.. python3 -m pytest tests ../../packages -q
```

## Status

Pilot stage. Nothing here is production, and no compliance certification is claimed. Cloud changes (Terraform apply,
deploys) are made only on the owner's explicit request. See `CLAUDE.md` for the working rules and
`docs/handoffs/CLAUDE_CODE_HANDOFF.md` for the current state.

## Related repositories

This repository was extracted from a monorepo with its history. The products are independent and import nothing from each other:

- `agentic-search-discovery`
- `agentic-resolution-intelligence`

## Licence

MIT (see `LICENSE`).
