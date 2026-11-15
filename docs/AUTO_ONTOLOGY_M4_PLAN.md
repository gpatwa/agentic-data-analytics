# Auto ontology, milestone 4: plan (2026-11-14)

Status: stage M4a was approved ("Yes, start M4a") and built on 2026-11-14; see `docs/AUTO_ONTOLOGY_DESIGN.md`, milestone 4. Stage M4b is
not approved and not started. The plan below is otherwise as written.

Milestone 4 in `docs/AUTO_ONTOLOGY_DESIGN.md` is "live run against the pilot warehouse, read-only, producing a draft PR". Milestones 1
to 3 are built and the evaluation met the approved thresholds on one run, so what is left is proving the same pipeline on a real
PostgreSQL and putting a draft in front of the owners. This plan splits that into a part that needs no cloud change and a part
that does, so the owner can approve them separately.

## What I found (read-only checks, this session)

- **The pilot warehouse holds only synthetic data.** `infra/terraform/azure-analytics-pilot/bootstrap.sql` creates `sales_orders`
  (100 generated rows) and `demo_sales_orders` (20,000 generated rows) and a read-only role `analytics_readonly`. No customer data.
  That makes a live run low-risk for data exposure, but it is still a live run under `CLAUDE.md`.
- **The pilot PostgreSQL is not reachable from a laptop today.** It has public access enabled but only the `azure_services`
  firewall rule (`resources.tf`). Reaching it means one of: a temporary firewall rule for the owner's address (a cloud change),
  running the job inside Azure (a deploy), or the owner running it from a host that is already allowed.
- **What has not yet touched a real PostgreSQL:** the introspector (so far SQLite only), the profiler's read-only transaction and
  statement timeout (unit-tested as strings), table and column comment reflection, and the EXPLAIN dry run (a fake probe so far).
  These are the real risks, and none needs the pilot to test.
- **PostgreSQL is available locally** (`postgres`, `pg_ctl`, `psql` and `docker` are installed here).
- **The model path is proven.** Milestone 3 ran `gpt-6-luna` on the pilot account with the key read from the owner's `az` session
  into the process environment only. M4 reuses that exact pattern and sends only schema facts and redacted samples.
- **A drafted contract does not fit `ChangeProposal`.** `packages/platform_contracts/proposals.py` requires triage, feedback and run
  ids from the feedback loop. A drafter run has none, so the draft goes out as plain files, not as a `ChangeProposal`.
- **Where a draft may live matters.** `SemanticRegistry` loads every `*.json` under its root (`registry.py`, `rglob`). A `draft`
  file there is refused by `get_certified()` (tested in M1), but it would still be listed. Drafts go outside the registry root.

## Stage M4a: real PostgreSQL, locally (no cloud change)

Needs from the owner: the go-ahead below, and nothing in Azure. The model call reuses the approved Azure OpenAI path.

1. Start a throwaway local PostgreSQL in the session scratch directory, load the same tables as `bootstrap.sql` (and a two-table
   schema with a foreign key and column comments, to exercise key and comment reflection), and create a read-only role that
   mirrors `analytics_readonly`.
2. Build a real `ExplainProbe`: a read-only transaction, `SET LOCAL statement_timeout`, `EXPLAIN` only (never `ANALYZE`), the compiled
   SQL and parameters from the validator. Tests prove it cannot run a write even if handed one (a write attempt must fail).
3. Run introspection and profiling as the read-only role. Prove, by trying them, that the role cannot write and that the timeout
   fires. Fix whatever real PostgreSQL shows that SQLite hid (type names, comment shapes, quoted identifiers, schema names).
4. Draft with the real model from the real schema. Score it with the milestone 3 harness (leaks, escapes, injections) and look at
   the draft by eye.
5. Emit the draft as files for the owner: the draft contract JSON, the candidate ontology snapshot, and a short review note listing
   every drop and its code, with the schema fingerprint, prompt version and model. Written to `semantic_registry_drafts/` (outside
   the registry root) on a branch, opened as a pull request only on the owner's word. A test proves the registry cannot resolve
   any file in that directory.

Exit evidence: tests against a real PostgreSQL for each of the four risks above, a numbers-only report, one draft the owner can read.
Claims it unlocks: "runs on PostgreSQL" in the roadmap card, not "in production" and not anything about customers' data.

## Stage M4b: the pilot warehouse itself (a cloud-touching step; separate approval)

Only if the owner wants proof against the real pilot server and role. Everything it adds over M4a is: the real server settings,
the real `analytics_readonly` role grants, and the real network path.

Needs, each stated in the task that does it:
- **A route to the server.** Recommended: a temporary firewall rule for one address, removed in the same task, with the rule's
  name, address and removal recorded. Alternative: run it from inside Azure. I would not deploy for this.
- **The read-only role's password**, fetched at run time into the process environment only, never printed or written. If the owner
  prefers, the owner runs the command and I read only the report.
- **A scope.** The two synthetic tables, one pass of introspection, profiling and `EXPLAIN` only, at most 12 model calls.

Stop rules (the run ends and is reported, not retried): any leaked secret or sample value, any validator escape, any write attempt
or permission error on the role, any table outside the named two being read, a statement timeout, or an unexpected error from the
server. After the run: remove the firewall rule, confirm it is gone, and record what was run in the program file in the owner's words.

Exit evidence: the numbers-only report, the removal confirmation, the draft files. Claims it unlocks: "drafted from a live
PostgreSQL pilot warehouse (synthetic data)". Nothing about a real customer, because none was used.

## What stays out of M4

- Certification: a person edits and certifies in Git. The drafts are `draft` and `candidate` and cannot execute.
- Snowflake and Databricks: see `docs/WAREHOUSE_AND_DBT_PLAN.md`, stages S4 to S8, each its own decision.
- Joins and ratios: single-dataset only, as decided. Drafted joins stay non-executable candidates.
- Any deploy, including the landing site.
- Rule-based inference and value-overlap join evidence (design pipeline step 3): not needed for M4; a later packet.

## Risks I can see

- A real PostgreSQL may expose type or comment shapes that SQLite never showed (arrays, enums, domains, partitioned tables, views).
  The introspector skips what it does not recognise and reports it. M4a is where these show up.
- `EXPLAIN` needs the same column and table privileges as the query, so a role with narrower grants than the compiler assumes will
  fail every candidate. That is a useful finding, and it is a drop with a code, not a crash.
- Three runs per case is a small look at a non-deterministic model. A bigger corpus needs a second certified reference, which only
  the owner's team can supply. The milestone 3 evaluation proves the safety properties more than it proves breadth.
- The draft is only as useful as the schema is clean. The pilot's two tables are small, so M4 shows the loop, not the scale.

## Decisions for the owner

1. **Start M4a now (local PostgreSQL, no cloud change, same model path)?** Recommended: yes.
2. **M4b against the pilot: yes, later, or never?** If yes, which route: temporary firewall rule (recommended) or run it from your side.
3. **Where drafts go:** `semantic_registry_drafts/` outside the registry root (recommended), or elsewhere.
4. **Open the draft as a pull request automatically at the end of M4a,** or hand you the files first.

No decision is recorded yet. Approving M4a approves a local run and a draft pull request only; it does not approve M4b, any cloud
change, any deploy or any certification.
