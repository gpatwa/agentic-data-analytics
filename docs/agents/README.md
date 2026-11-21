# Agent definitions

`.claude/` is gitignored in this repository, so shared agent definitions live here. To use one, copy it to `.claude/agents/` (project) or `~/.claude/agents/` (yours):

```bash
mkdir -p .claude/agents && cp docs/agents/approval-sme.md .claude/agents/
```

- `approval-sme.md`: a read-only advisor that prepares a pending owner approval (what is asked, what is already approved and excluded, exact actions and blast radius, ranked risks, cost cap, rollback, a recommendation and a ready-to-paste approval sentence). It approves nothing, edits nothing and runs nothing live; approvals are recorded only from the owner's own words in `docs/execution/enterprise-analytics/agentic-data-stack-program.yaml`.
