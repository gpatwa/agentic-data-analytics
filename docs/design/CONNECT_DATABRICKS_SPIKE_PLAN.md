# Databricks Connect-mode spike (option A): a plan to review (2026-11-18)

Status: **a plan for the owner to decide. Nothing here has been built or run, no Databricks object was created, and approving this plan approves no live step.** It is gate D2 of `CONNECT_DATABRICKS_DESIGN.md` (D0, the design, was approved with option A on 2026-11-18). Each live gate below needs its own explicit approval in the task that runs it, with the exact statements shown first (CLAUDE.md). Databricks facts come from vendor documentation read on 2026-11-18 and are **not verified on an account**; the spike exists to verify them.

## What the spike must answer (workspace-only questions)

The design's option A is: the admin runs one script once; Compass then creates read-only service principals per connection inside a ceiling. That works only if a **delegated setup service principal** `S` (not a workspace admin) can do the per-connection work. The documentation suggests it may not, so each step is a question, not an assumption.

| # | Question | What the documentation suggests (unverified) | Why it matters |
|---|---|---|---|
| Q1 | Can `S`, a service principal that is **not** a workspace admin, create a service principal `T`? | Adding service principals is a workspace-admin action. Likely **no**. | If no, Compass cannot create per-connection principals without an admin-level credential. |
| Q2 | Can `S` create an OAuth secret for a principal it did not create, and can the *service principal manager* role on `T` be granted to `S` alone? | Secrets need account admin or the manager role on that principal; workspace admins get the manager role on principals they create. | Decides whether "admin pre-creates `T`, Compass mints its secret" is possible. |
| Q3 | Can `S` grant `SELECT` on exactly the ceiling tables to `T`, and nothing else? Does `MANAGE` on a **table** suffice, and does it let `S` also grant `USE CATALOG` and `USE SCHEMA`? | `MANAGE` on an object lets one manage its privileges; on a schema it also covers children (wider than a table ceiling); data access needs `USE CATALOG` and `USE SCHEMA` on the path. | The ceiling must be exactly the tables the admin named, as on Snowflake. |
| Q4 | Can `S`, holding only `CAN_USE` on the named SQL warehouse, give `T` `CAN_USE`? | Changing warehouse permissions probably needs `CAN_MANAGE`, which can edit the warehouse. Likely **no**. | If no, per-connection principals need another route to the warehouse (an admin-granted group, or a shared principal). |
| Q5 | Can `S` delete what it created and revoke what it granted, so a rollback leaves nothing? | Plausible; unverified. | The rollback rule: drop only what this run created. |
| Q6 | Does `T`, authenticated with OAuth M2M and a short-lived secret, read the ceiling tables and **fail** on a table outside it, a write, and a schema change? | The earlier validation runs showed this for a different principal on this workspace. | The read-only guarantee for the per-connection identity. |
| Q7 | Does `S`'s preflight see a pre-existing principal with the planned name (a decoy), and does `S` leave it alone? | Same class of question as the Snowflake decoy. | A failed setup must never delete a customer's object. |

**Not testable on this workspace:** the sign-in half of option B (a custom OAuth app connection needs an account console, which this workspace does not have).

## How each answer changes the design

- **Q1 yes** (unexpected): option A works as designed, with `S` as the delegated setup principal.
- **Q1 no, Q2 yes:** the admin pre-creates the per-connection principals (or a small pool) with a script, and Compass mints secrets and grants within the ceiling. Less automatic, still no per-connection paste of data grants.
- **Q1 no, Q2 no:** Compass cannot create identities with a narrow credential. Options: the admin runs a short per-connection script (like the Snowflake script path), or Compass holds an admin-level credential (a security decision the owner would have to take; not recommended).
- **Q3 shows `MANAGE` on a schema is needed:** the ceiling can only be a schema; the review text must say so.
- **Q4 no:** per-connection principals cannot be given the warehouse by Compass; the admin grants it to a group once, and the principals join that group (a further question).
- **Q5 no:** the rollback reports leftovers with the exact commands for the admin, as designed for Snowflake.

## Safety design

- **Synthetic data only**, in a new sandbox catalog `compass_connect_spike_<suffix>` with a fresh 8-hex suffix, schema `data`, three tables of 30 rows (two inside the ceiling, one outside it), all tagged with a comment. If catalog creation is not allowed on this workspace, the fallback is a schema with the same naming inside an existing catalog the owner names; nothing existing is altered (**owner decision, below**).
- **Nothing existing is edited.** The SQL warehouse is the existing "Serverless Starter Warehouse", chosen **by name**, never created, resized or reconfigured; only `CAN_USE` entries for our own principals are added and removed. Its auto-stop and cost are read, not set.
- **A read-only preflight stops** on anything under our prefixes (catalogs, principals named `compass-spike-*`, warehouse permission entries) or an existing sandbox name. Creation uses plain `CREATE`, never `IF NOT EXISTS` or `OR REPLACE`.
- **No credential is written anywhere.** `S`'s secret and `T`'s secret live in the spike process's memory only, with short lifetimes, and die with it. The admin sign-in is the owner's own browser sign-in (the owner clicks; I never type a password). The leak scanner from the Snowflake harness watches both secrets and every access token.
- **Created-only teardown by recorded ids**: secrets, then principals, then the sandbox catalog or schema. A `--sweep` removes tagged leftovers by prefix.
- **Stop rules.** Stop and report, without improvising, on: an existing object that is not ours; any statement that would touch the owner's own user, groups, workspace settings or any existing catalog; a secret or token in any log; a permission result that surprises (for example `S` able to grant outside the ceiling); an error naming billing or quota.
- **Cost:** the warehouse is serverless and starts on demand; the expected use is a few minutes of its smallest size on whatever free or starter allowance the workspace has. Not measured.
- **The admin steps use the owner's signed-in rights**, so the spike never needs account-level access.

## Built first, offline (no approval needed)

1. A throwaway driver under `app/connect/e2e/` (like the Snowflake harness, never imported by the app, off unless `--allow-live` and an expected-workspace flag): runs the steps below through the Databricks SDK, records only outcomes (`ok`, a permission-denied code, a count), and includes the leak scanner and a lockout-style stop rule that refuses any call touching users, groups or settings.
2. Generated statements and API calls for the fresh suffix, published for review in `CONNECT_DATABRICKS_SPIKE_STATEMENTS.md` and kept equal to the generator by a test, as for Snowflake.
3. Fake-workspace tests for each outcome of Q1 to Q7 (allowed and denied), so the driver's reporting is proven before it touches the workspace.

## The gates (each needs your explicit approval)

| Gate | What happens | Live? |
|---|---|---|
| **D2-0 Review** | You read this plan and the generated statements and approve or change them. | no |
| **D2-1 Fixture and setup principal** | Read-only preflight; the sandbox catalog or schema and tables; **as you**, create `S` (a plain service principal, **not** an admin) and a short-lived secret for it; give `S` `CAN_USE` on the named warehouse and the ceiling grants it would hold in the design (`MANAGE` on the two ceiling tables, `USE CATALOG`/`USE SCHEMA` as the design needs). About 15 steps. | yes |
| **D2-2 The questions** | **As `S`** (M2M, short-lived tokens): Q1, Q2, Q3, Q4, Q5, Q6 and Q7 in that order, creating only what the questions need. Every denial is expected to be recorded as a result, not as a failure. About 25 calls. | yes |
| **D2-3 Tear down and sweep** | Created-only removal by recorded ids, then the zero-leftover checks: principals with our prefix, catalogs and schemas with our prefix, warehouse permission entries, secrets. Always runs after D2-1, including after a stop. | yes |

D2-1 and D2-2 may be approved together. You are needed for one browser sign-in (about three clicks); I drive the rest.

## Owner tasks and decisions for D2-0

1. Approve or change the gates and the generated statements (shown in full before D2-1).
2. **Catalog:** may the spike create a sandbox catalog, or must it use a schema inside an existing catalog you name (the earlier validation used `dev`)?
3. **Warehouse:** confirm "Serverless Starter Warehouse" by name.
4. Accept that a **denied** step is a useful result, and that Q1 and Q4 are expected to come back "no".
5. Accept that `S` is created and removed by you as a signed-in admin, and that its secret and `T`'s live in memory only.

Not verified: every Databricks behaviour in the table above, whether this workspace allows a sandbox catalog, the cost, and the time (I would expect about an hour with you present; an estimate).
