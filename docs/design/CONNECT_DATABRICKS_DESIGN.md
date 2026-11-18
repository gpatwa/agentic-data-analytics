# Connect mode for Databricks: a design to decide (2026-11-18)

Status: **D0 (this review) was approved by the owner on 2026-11-18 with option A (program file `connect_databricks_d0`); the other three owner decisions below are still open. Nothing here is built or run; no Databricks account or workspace was touched, and nothing here approves a live step.** It starts the Databricks connector for Connect mode (a customer's own warehouse). It builds on `CONNECT_PRODUCT_RUN_DESIGN.md` (Snowflake) and the Databricks pieces that already exist (below). Facts about Databricks come from vendor documentation read on 2026-11-18 and are **not verified on an account**.

## Is there a standard framework?

Yes. Databricks' own standard is **unified client authentication** with the **Databricks SDK** (Python, Java, Go), on **OAuth**:

| Use | Standard | What it is |
|---|---|---|
| Unattended software (Compass reading a customer's data) | **OAuth machine-to-machine (M2M)** with a **service principal** | Client id plus secret; short-lived tokens (one hour) that the SDK requests and refreshes. Personal access tokens are the legacy method. |
| A person signing in (the admin doing setup) | **OAuth user-to-machine (U2M)**, a browser sign-in | Authorization code with PKCE; the person's own rights apply. Optionally the person acts as a service principal (on-behalf-of) so no secret is needed. |
| Running queries | **SQL warehouse** through the **Databricks SQL connector**, authorised by the above | The same two methods work in the connector and the JDBC and ODBC drivers. |
| What data a principal may read | **Unity Catalog grants** (`USE CATALOG`, `USE SCHEMA`, `SELECT`) | Plus `CAN_USE` on the named SQL warehouse. |
| Repeatable admin setup | **Terraform provider for Databricks** or Databricks CLI/SDK scripts | An alternative to hand-run SQL for customers who manage infrastructure as code. |

This is the Databricks counterpart of what Snowflake calls OAuth with a security integration and key-pair service users, and it matches the repository's earlier decision to move Databricks to OAuth M2M (`docs/WAREHOUSE_CONNECTOR_AND_ONBOARDING_PLAN.md`).

## What already exists in this repository

- **The query path.** `app/execution/warehouse_sessions.py` has a Databricks session (SQL connector) that uses OAuth M2M when a client id and secret are present and the legacy token otherwise; the compiler has the Databricks dialect; the conformance suite covers it.
- **Live-verified on the owner's workspace (2026-11-15/16):** the validation tool's automatic path end to end: create a sandbox schema, a service principal with `CAN_USE` on a **named** warehouse, grants, a short-lived secret held only in memory, eight checks, teardown. That is the *validation* sandbox, not Connect mode (the admin signed in through the browser SSO prompt and the sandbox was Compass's own, not a customer's tables).
- **Connect mode today:** the screens say "Databricks is not available for your own data yet", and the host check (`app/connect/targets.py`) already knows the three Databricks host forms.

So the **read path and the sandbox provisioning are largely built; what is missing is the customer-facing setup** that Snowflake has: a ceiling the admin sets, a read-only identity created inside it, and a way for Compass to do that without a person copying code.

## How the Snowflake design maps to Databricks

| Snowflake product-run | Databricks equivalent (to verify) |
|---|---|
| Security integration (OAuth, public client, PKCE, no secret, 10 minute token) | A **custom OAuth app connection** in the account console: redirect URL, public client (no secret), access token 5 to 1,440 minutes (default 60), refresh token (default 7 days, can be shortened). |
| Setup role with `CREATE ROLE`/`CREATE USER` and grant-option grants on a ceiling | A **setup principal** that may create service principals and secrets, and may grant only on the ceiling's catalog or schema (Unity Catalog `MANAGE`, or ownership of a dedicated schema). **The central open question.** |
| Network policy on the integration only | Workspace **IP access lists** (a different mechanism, workspace-wide, so the lockout risk is larger). |
| Resource monitor and X-Small warehouse | **Not created**: the customer names an existing SQL warehouse; we add `CAN_USE` only and never edit it (as the validation tool already does). Its auto-stop and cost are shown, not set. |
| Per-connection role and key-pair service user | A **service principal** with an OAuth secret (shown once, held in memory then in the vault), read-only grants on exactly the chosen tables, `CAN_USE` on the warehouse. |
| Rollback drops only what this run created | The same rule: delete the service principal and revoke grants it created; the secret dies with it. |

## What is not known and the plan must settle

1. **Can a delegated, non-admin principal create service principals and their secrets?** In the documentation this looks like an account or workspace admin, or a "service principal manager", capability. If only a full admin can, Connect mode needs a different split than Snowflake's: for example the admin runs a short script once to create **one** setup service principal with a narrow role, and Compass then uses its M2M credential (a stored secret) instead of an admin's one-time sign-in. That is a security trade-off the owner decides.
2. **Can grants be delegated to exactly a ceiling?** Unity Catalog's `MANAGE` and ownership rules decide whether the setup principal can pass on `SELECT` on chosen tables without being able to grant anything else.
3. **Can the OAuth app connection be a public client with PKCE and a local redirect, and can the sign-in be limited to named admins?** The pages read do not say how a user is restricted, and the token's rights are the person's own (broader than Snowflake's single setup role). The sign-in therefore needs a scope or a service-principal on-behalf-of flow.
4. **Token and secret lifetimes.** Access tokens default to one hour (shorter is allowed to five minutes); service-principal secrets can last up to two years. The design should pick short values and rotate.
5. **Rollback and visibility behaviour** (does a preflight see objects the setup principal does not own; can it drop what it created), the same questions the Snowflake rollback check answered.

## Options for the setup step (an owner decision)

- **A. Script first (recommended first step).** The admin copies one registration script (SQL plus a CLI or Terraform snippet) once: creates the ceiling grants, the setup service principal and the OAuth app. Compass then creates per-connection read-only principals. This mirrors Snowflake's "admin runs a script" tab, needs no U2M sign-in, and is the smallest thing that can be checked live.
- **B. Product-run with an admin sign-in**, as on Snowflake. Better experience (nothing pasted per connection) but depends on answers 1 to 3 and is a larger security surface.
- **C. Terraform or Asset Bundle module** the customer applies. Good for customers already on infrastructure as code; additive, not a replacement.

**Recommendation:** build A first on the existing M2M query path, then run a time-boxed live spike for questions 1 to 3 before deciding on B.

## Proposed gates (each needs the owner's explicit approval; none is approved)

| Gate | Content | Live? |
|---|---|---|
| D0 | Review and decide this document (option, setup-principal model, lifetimes). | no |
| D1 | Build offline (**moved after D2**: the statements depend on what the spike shows, so building first would mean guessing grant and service-principal behaviour): registration script generator and verifier for Databricks, per-connection statements and rollback, the Connect-mode screen for Databricks, with tests on fakes. | no |
| D2 | A read-only live spike on a **sandbox workspace the owner names**: questions 1 to 3, with a created-only teardown and a zero-row sweep. | yes, per step |
| D3 | An end-to-end live check like the Snowflake one, then the rollback check. | yes, per step |

## Owner decisions

1. Which setup option (A recommended; B later if the spike supports it).
2. Which workspace and SQL warehouse may serve as the sandbox (cloud and region; whether it is Azure, AWS or GCP changes the host forms and some admin screens).
3. Whether Compass may hold one narrow setup-principal secret in the vault (needed if answer 1 above is "admin only").
4. Token lifetime defaults.

Not verified: every Databricks behaviour in the tables above; the cost and time of a spike; that PKCE is a setting rather than client behaviour.

## After D0 (2026-11-18): order of work

D0 is approved with option A. The generator's statements depend on three facts that only a workspace can answer (who may create service principals and secrets, how grants can be delegated to a ceiling, and whether a public PKCE client with a local redirect works), so the recommended order is **D2 before D1**: draft a read-only spike plan with exact statements and a created-only teardown, show it for approval, run it, then build against what it showed.

Candidate sandbox, **to confirm**: the workspace and catalog already used for the validation checks of 2026-11-15/16 (recorded in `docs/WAREHOUSE_S6_LIVE_VALIDATION_PLAN.md`). One thing to check first, because it could block the sign-in half of the design: whether that workspace's account has an **account console** where custom OAuth app connections can be created (a free or trial tier may not). If it does not, the spike covers the service-principal and grant questions only and the sign-in question stays open.

## What the candidate workspace looks like (read-only look, 2026-11-18)

With the owner signed in in Chrome, I looked at the validation workspace and changed nothing (no object created, no setting touched):

- **SQL warehouses:** one, "Serverless Starter Warehouse", 2X-Small, serverless, created by the owner. That is the warehouse a spike would name; it is never edited.
- **Account console: not available.** Opening the account console, or its app-connections settings page, only leads to a "Select a workspace" screen; there are no account administration pages. A custom OAuth app connection (the Databricks counterpart of Snowflake's security integration) is created in the account console, so **the sign-in half of option B cannot be tested on this workspace.** I did not read the edition name on the page; this is consistent with a free or starter tier, but that is an inference.
- **What it does allow:** the earlier validation runs created and removed service principals, grants and OAuth M2M secrets here, so a spike on option A (service principals, Unity Catalog grants, `CAN_USE`, M2M) **can** run on it.

Consequence for the plan: option A is the one that can be proven on this workspace; testing option B would need a workspace whose account has an account console (for example a paid or trial account the owner names). That is one more reason to keep A as the first step.
