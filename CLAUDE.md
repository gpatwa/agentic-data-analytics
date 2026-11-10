# Claude Code Repository Guide: agentic-data-analytics

## Start Here

Before changing files, inspect `git status -sb`, the current branch and recent commits. This repository may contain the
owner's work; never discard or overwrite unrelated changes. Read `docs/handoffs/CLAUDE_CODE_HANDOFF.md` for the current state.

## Boundaries

- This repository is one product: Governed natural-language analytics: an LLM interprets a question into a typed, validated intent, and a deterministic compiler, policy checks and human review do the rest. Callable by agents through typed tools and MCP. Includes the public landing site.
- Do not import code from the sibling products. They live in separate repositories:
- `agentic-search-discovery`
- `agentic-resolution-intelligence`
- Semantic definitions are Git-backed. Never claim a contract is certified because code or a fixture refers to it; preserve the human semantic-certification gate.
- Human gates (semantic certification, security, policy, thresholds, final go/no-go) need the owner's explicit approval. Record approvals in `docs/execution/enterprise-analytics/agentic-data-stack-program.yaml`; never infer one from implementation or tests.
- The release manifest hashes files by path relative to this repository's root; keep the layout. Signatures bind `manifest_digest`, so sign off on the final manifest.

## Safety and workflow

- Do not deploy to Azure, mutate cloud resources, connect to a live warehouse or run live validation unless the owner
  explicitly asks in the current task. Local fake-provider verification is the default.
- Human gates (security, policy, thresholds, licensing, final go/no-go) need the owner's explicit approval. Never infer approval.
- Never set GitHub secrets or print credentials; never commit `backend.hcl`, `terraform.tfvars`, `secrets.auto.tfvars` or plan files.
- Merge your own pull requests once every check is green (`gh pr merge <n> --auto --merge`).
- Do not claim a test or evaluation passed unless you ran it.

## Useful commands

```bash
cd services/analytics-api && PYTHONPATH=.:../.. python3 -m pytest tests ../../packages -q
ruff check <changed-python-files>
git diff --check
```
