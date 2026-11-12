# Claude Code Handoff: agentic-data-analytics

Updated: 2026-11-11 (extracted from the monorepo `gpatwa/enterprise-agentic-rag` on 2026-11-10).

## Where this came from

Extracted with `git filter-repo` (script: `scripts/separation/extract.sh` in the original repository; plan:
`docs/REPO_SEPARATION_EXTRACTION_PLAN.md` there). History and paths are preserved. Product history before the extraction
includes work on the sibling products' shared tooling.

## State

- Deploys use GitHub OIDC with a narrow deploy identity and no stored secrets (PR #3). The first OIDC deploy of the
  Analytics pilot ran on 2026-11-11 (workflow run 43093695032, success).
- Reported after that deploy (not re-verified by the commit that wrote this note): API and purge job on image
  `compass-analytics-api:fe517f9`; `/health` returns 200 with `"status":"degraded"` (llm, context index and dashboard not
  configured, no baseline yet); the static web app returns 200.
- Deploy-log secret scan (2026-11-11, runs 43093695032, 42981529462, 42981500897): no tokens, keys, connection strings,
  SAS signatures or private keys found. Container app secrets appear only as Key Vault references (`secretRef`, empty
  `value`). The logs do show identifiers (subscription, tenant and client IDs, resource IDs); these are not credentials.
- Open decisions that are the owner's: any licence other than MIT, and the items listed in `CLAUDE.md` as human gates.

## Next actions

1. Read `CLAUDE.md`, then verify real state (`git status -sb`, `gh pr list`) and report.
2. Not yet done: run the Infra (Terraform) workflow in plan mode to confirm no drift after the deploy (needs the owner's
   go-ahead; it reads cloud state).
3. The owner decides what comes next.
