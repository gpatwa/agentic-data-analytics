# Auto ontology, milestone 4: plan (2026-11-14)

Status: stage M4a was approved ("Yes, start M4a") and built on 2026-11-14; see `docs/AUTO_ONTOLOGY_DESIGN.md`, milestone 4. Stage M4b is
not approved and not started. On 2026-11-15 the owner asked for the route to be planned ("Plan the route"); that is the section
"M4b route options" below. It is a plan only: no Azure resource was read or changed to write it, only the Terraform files in this
repository. The rest of the plan is as written.

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

## M4b route options (2026-11-15)

**Facts the options rest on** (all from `infra/terraform/azure-analytics-pilot/`, nothing queried live):

- The server is a PostgreSQL Flexible Server (`B_Standard_B1ms`, version 16) with `public_network_access_enabled = true`. Its only
  firewall rule is `allow-azure-services` (`0.0.0.0` to `0.0.0.0`), so a laptop is refused today. Terraform manages that one rule.
  A rule added by hand is not in Terraform state, so `terraform plan` will not report it. It must be removed by the run itself, and
  a leftover rule would stay invisible, which is why removal is verified, not assumed.
- The warehouse is the database `analytics_warehouse`. The role `analytics_readonly` has `CONNECT`, `USAGE` on `public`, `SELECT` on
  every table in `public`, and `default_transaction_read_only = on`. It is not limited to the two tables, so "no table outside the
  two" is enforced by the runner's `--tables` list and a stop rule, not by the database.
- The role's full connection string, with `sslmode=require`, is already stored as the Key Vault secret `analytics-db-url`. That is
  exactly the shape `ONTOLOGY_PG_READONLY_URL` wants, so no password needs to be copied, typed or generated.
- Side finding for the owner, not part of M4: the `0.0.0.0` rule admits any Azure-hosted address, from any tenant, to the server's
  port. Password and TLS still apply, and the code comment says "tighten for production". It is a reason to prefer a rule scoped to
  one address, and a production item for the pilot-hardening list.

| Route | What it changes in Azure | Fit | My view |
|---|---|---|---|
| **A. Temporary firewall rule for one address, from this machine** | One rule added, then removed, in the same command | Reaches the real server, role and TLS exactly as the pilot API does. Needs the owner's `az` identity to be allowed to edit that server's firewall (unchecked). | **Recommended** |
| B. The owner runs the command, I read only the report | The same one rule, made by the owner | Same network need as A, because the owner's laptop is also not allowed today. It changes who holds the password, not the route. | Fine as a variant of A if the owner prefers to keep the secret off this session |
| C. Run inside Azure (a one-off container job, or a shell in the pilot API's container) | A new resource, or a live production-adjacent container; also needs the new code in an image, so a deploy | More Azure change than a firewall rule, and it is a deploy | No |
| D. Do not do M4b | Nothing | M4a already ran the same code on a real PostgreSQL 18.3 with the same bootstrap tables. M4b would add only the real server settings, the real grants and the real network path | Reasonable if "runs on PostgreSQL" is the only claim wanted |

### Recommended run sheet (route A)

Nothing below runs until the owner approves in the task that does it. The approval must name: the route, the two tables, the model
call cap, and that one temporary firewall rule may be created and removed.

1. **Before any change (read-only, one pass):** confirm the Azure account and subscription, confirm the server exists and is running
   (if it is stopped, stop and ask: starting it is a change), list its current firewall rules and record them, and confirm the
   signed-in identity can edit firewall rules. If it cannot, stop and report; do not widen access.
2. **Find the address to allow.** Either the owner gives it, or it is read from an IP-echo service; the second sends this machine's
   address to a third party, so the owner chooses. The rule is a single address, never a range.
3. **One shell command, so removal cannot be skipped:** create the rule `m4b-temp-<date>`; fetch `analytics-db-url` from Key Vault
   into the process environment with its output discarded; wait for the rule to take effect; run `draft_run` with
   `--allow-remote`, `--tables demo_sales_orders` (and `sales_orders` only if the owner names it), and a fresh `--contract-id`; then
   remove the rule and unset the variable in a `trap` that fires on any exit, including a failure or an interrupt.
4. **Verify removal:** list the firewall rules again and compare with the step 1 record. The run is not finished until the two
   lists match. If removal fails, say so at once and give the owner the exact delete command; the rule stays visible until then.
5. **Report:** the numbers-only report, the rule name, the address and the timestamps of creation and removal, and the draft files
   on a branch. A pull request is opened only on the owner's word, and certification stays with the owners as before.

Expected cost: a few model calls (the cap stays 12; one pass is expected to use about 1 to 2) and nothing else. No Azure resource is
created that outlives the run, and nothing is deployed.

### One small code change first (a pull request, no cloud contact)

The runner already refuses a non-local host without `--allow-remote`. Add `--expect-host`, so a mistyped or swapped URL is refused
before any connection if its host is not the one the owner named, and add a stop-rule check in the runner that fails the run if the
report shows any drop whose code starts with `explain_failed:` (the exception class follows the colon), or if profiling raises `profile_failed` (for M4b a role problem is a finding, not something to retry).
Both get tests that need no network. Until that merges, M4b should not start. (Built on 2026-11-15 after the owner chose route A: a remote run now also requires `--expect-host`, and a leak, escape, injection, failed probe or table outside `--tables` stops it with nothing written. Tests need no network.)

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

Decided on 2026-11-14: M4a now (yes), drafts in `semantic_registry_drafts/` outside the registry root, and the draft was opened
as a pull request that is not auto-merged. Open now:

1. **M4b: do it, or stop at M4a?** Recommended: do it only if the claim "drafted from the live pilot warehouse" is wanted; otherwise
   M4a already supports "runs on PostgreSQL".
2. **Route:** A (recommended, from this machine), A with the owner running the command (B), or none.
3. **Address:** the owner supplies it, or the IP-echo service is allowed to find it.
4. **Tables:** `demo_sales_orders` only (recommended, it has the certified reference to score against), or both synthetic tables.
5. **Go-ahead for the small code change** (`--expect-host` and the stop-rule check) as its own pull request.

Approving any of this does not approve a deploy, a change to Terraform, any certification, or a second run. M4b, if approved, is
one run in one task.
