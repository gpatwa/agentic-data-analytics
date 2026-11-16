# Warehouse onboarding experience: design (2026-11-16)

Status: **design for the owner to review. Nothing here is built, approved or tested with users.** On 2026-11-16 the owner narrowed the focus to reference data first (`docs/REFERENCE_DATA_DEMO_PLAN.md`): the "Try with sample data" path, connecting to a pre-seeded reference warehouse, is the part to build first; Connect mode is parked. It is workstream C4 of `docs/WAREHOUSE_CONNECTOR_AND_ONBOARDING_PLAN.md`. A click-through prototype with fake data is in `docs/design/onboarding-prototype.html` (open it in a browser; it connects to nothing).

## Who this is for, and what each person needs

| Person | Does it | Needs from the experience |
|---|---|---|
| **Data or platform admin** | Once per warehouse | Short, safe, reversible. Can say exactly what Compass will do, to their security team, before it does it. |
| **Security reviewer** | Reads one page | The exact objects, permissions, data egress and what Compass never does. A way to run it themselves instead. |
| **Analyst** | Daily, afterwards | Never sees any of this. First governed answer works. |

Success looks like: **a first governed answer on the sample table within five minutes of "Connect"**, an admin who can explain what was created, and nothing left behind if they change their mind. These are targets, not measurements; no admin has used it yet.

## Principles (each comes from something that happened in the live runs)

1. **Never ask for a pasted secret.** Sign in through the vendor's own page; keys and tokens are made and held by the system, short-lived, and never shown.
2. **Show before doing.** A consent page lists every object and permission, generated from the same code that will create them, so it cannot drift from what happens.
3. **Prove, don't claim.** The eight conformance checks are the connection test, shown in plain words. "Read-only" is a result the admin sees, not a promise.
4. **Always reversible.** One visible "Remove access" that tears down exactly what setup created, and a report if anything is left. (An interrupted run once left objects behind.)
5. **Fail fast, say what to do.** No silent retries: a connection that cannot be made says so in under 30 seconds with a next step. (A missing certificate store once looked like a five-minute hang.)
6. **Offer what is likely to work, and fail fast when we cannot know.** The screen is driven by each engine's capability record. Some limits only show when tried: one Snowflake account had no SSO identity provider, and that was learned only when the sign-in was attempted (error `390190`). So the SSO option stays available, and the failure says so within seconds, names the cause and puts the next best method one click away, with nothing left behind.
7. **Honest about stage.** Sandbox runs say "sandbox"; sample data says "sample data". No claim runs ahead of evidence.
8. **Least privilege, visibly.** The identity gets the smallest grants, and the screen lists them.

## Two modes, and why the difference matters

| | **Sandbox mode** (built and verified for the owner's accounts) | **Connect mode** (customer data; not built) |
|---|---|---|
| Purpose | Prove the connector on a synthetic table | Let Compass answer from the customer's real tables |
| What Compass creates | A new database or schema, a 100-row synthetic table, a read-only identity, a small warehouse and spending cap | **Only an identity and grants**: a read-only role and a service identity, `SELECT` on the schemas the admin picks. No new database, no copied data. Optionally a spending cap. |
| Risk class | Owner's own accounts | **New: touches a customer's warehouse. Needs its own security approval before any build.** |

The prototype shows both, because the consent page is the heart of the design and must make the difference unmistakable.

## The flow

Seven screens. Each has one primary action.

**0. Start.** Two paths: *Try with sample data* (no warehouse, an instant governed answer on a synthetic table, to see the product first) and *Connect my warehouse*. The first path is the safest first minute and the best demo.

**1. Choose your warehouse.** PostgreSQL, DuckDB, Snowflake, Databricks. Engines not built are not shown as options; a "Request another" link goes to a form. Each card shows the stage honestly ("sandbox validated", never "supported" until true).

**2. Connect.** Pick how to sign in. Options come from the engine's capabilities and the account:
- *Sign in with your company login* (SSO / OAuth browser sign-in). Shown only if the account supports it; if detection fails the error catalogue explains why.
- *Use a key you control* (key-pair admin sign-in).
- *My admin will run it* (**export the SQL**): downloads the exact statements and a checklist; Compass waits for a short connectivity test afterwards. Essential for organisations that will not let a third party create users.
- *Type a password* (only if the owner approves it as a security decision; typed into a prompt, never stored).
Never offered: paste a token, paste a password into a form, upload a long-lived key to us.

**3. Review what will be created.** The consent page. Left: the exact list of objects and permissions for the chosen engine and mode, each with a one-line reason. Right: *Compass will never* (write to your data, copy your tables, store your admin login, run SQL written by a model) and *Spending cap* (the size of the warehouse or monitor). Below: *Export as SQL*, and one primary button, **Create and test**. No checkbox wall; the page is the consent, and it is saved as a record with the time and the signed-in person.

**4. Setting up and testing.** A live list of the eight checks in plain words ("Your access is read-only", "We can read your table layout", "A governed query ran", "Results match what we expected", "A too-expensive query is refused", "A runaway query is stopped", "A running query can be cancelled", "Nothing sensitive was logged"). Each shows a spinner, a tick or a plain failure. The slowest steps say roughly how long they take. A cancel button stops and tears down.

**5. Connected.** A summary: what was created, the access list, the checks passed with timings, and **Remove access** (one click, with a plain confirmation of exactly what disappears). A link to the audit trail. If anything failed, this screen becomes the recovery screen (see the catalogue).

**6. Choose what Compass may use.** A schema and table allowlist (default none), then a **redaction preview**: for a chosen table, what the model would see, with sensitive columns excluded and sample values shown only as the approved rules allow (emails and numbers as placeholders such as `<email>`; a column with too few repeated values is not sampled). One button: *Draft definitions*. Drafts are `draft` and `candidate` only; a person certifies them (the auto-ontology human gate). Nothing runs until certified.

**7. Ask your first question.** On the sample table or a certified definition. The answer shows the definitions used, the filters, the policy decision and the evidence, because that is the product's promise.

## Errors and recovery

Every failure has the same shape: **what happened** (plain words), **why it usually happens**, **what to do now**, **who can fix it** (you, your admin, Compass), and a **Copy details** button that copies the code and nothing else (never a query, a value or a credential). The codes below are the real ones produced by the code today.

| Code or symptom | The person sees | What to do | Who |
|---|---|---|---|
| Snowflake `390190` (no SAML identity provider) | "Your Snowflake account does not use company sign-in, so this method cannot work." | Choose *Use a key you control* or *My admin will run it* | Admin |
| Admin sign-in failed (`DatabaseError`, browser closed or timed out) | "We could not sign you in." | Try again; check the browser did not block the pop-up | You |
| `setup_catalog` / `setup_schema` (cannot create) | "Your login cannot create a catalog here." | Pick an existing catalog and schema, or ask an admin with rights | Admin |
| `setup_principal`, `setup_secret`, `setup_token_permission` (permission denied) | "Your login is not allowed to create a service identity." | Ask a workspace admin, or use *My admin will run it* | Admin |
| `setup_warehouse_access` | "We could not give the new identity access to the warehouse." | Ask the warehouse owner to allow it | Admin |
| `no_matching_warehouse` | "We could not find a SQL warehouse to use." | Create or start one, then retry | Admin |
| `setup_step_N:Type` (Snowflake setup step) | "Setup stopped at step N of 15. Nothing after it ran." Teardown runs automatically. | Retry, or export the SQL | Admin |
| TLS: certificate verify failed (connection retried) | "We could not make a secure connection to your workspace." (shown within 30 s) | Check network proxy or certificate store; contact support with the details | Admin or Compass |
| `grants_not_visible` | "We could not confirm the access is read-only." | Re-run after the grants finish; if it persists, export the SQL | Admin |
| `extra_privilege` | "The new identity has more access than read-only. We stopped." | Remove the extra grant (we show which kind); retry | Admin |
| `columns_differ` | "The table does not look like the one we expected." | Choose the right table | You |
| `cost_over_cap`, `estimate_zero` | "We could not get a safe cost estimate, so nothing ran." | Contact support with the details | Compass |
| `result_mismatch` | "Results did not match the expected values." | Contact support; do not proceed | Compass |
| `no_timeout`, `finished` (cancel) | "A runaway query could not be stopped on this warehouse." | Do not proceed; contact support | Compass |
| `check_hung`, `unexpected_timeout`, `statement_cap` | "A step took too long and was stopped." | Retry once; then contact support | Compass |
| `teardown_incomplete:N` | "Setup was removed except N objects." **Lists them by name**, with the exact commands to remove them. | Run the commands, or click *Retry cleanup* | Admin |
| A name already exists (planned check) | "An object with our name already exists and is not ours. We did not touch it." | Choose another name or remove it | Admin |

## Security and privacy design

- **No stored admin credential.** The admin signs in through the vendor's page; the session holds a short-lived token in memory for the length of setup.
- **The runtime identity is least-privilege and rotatable**: a read-only role or service principal; Snowflake key pair, Databricks OAuth client credentials (short-lived tokens). Secrets live in the secret store, never the control database, logs, evidence or snapshots.
- **What the model sees** is only schema facts and redacted samples under the approved rule; shown to the admin before drafting.
- **A consent record**: who, when, which engine and mode, the exact object list (a hash of it), stored in the evidence trail without any credential.
- **Remove access** deletes what setup created and the stored secret; the audit trail keeps the fact, not the content.
- **Export-SQL parity**: the SQL a person runs by hand is generated by the same code as automatic setup, so the two paths cannot differ.

## Measuring it (no query text, no values, no hosts)

Funnel: start, engine chosen, method chosen, consent viewed, created, checks passed, tables chosen, first answer. Per step: time spent and failure code. Headline measures: **time from Connect to first answer**, **completion rate**, **failures by code**, **Export-SQL share** (a signal that automatic setup is not allowed), and **Remove-access rate** (a signal of mistrust).

## Testing the design

Before any build: show the prototype to **two or three real data admins** (we have none yet; the owner supplies them or the names). Tasks: connect a warehouse; explain to a colleague what was created; find how to undo it; recover from a deliberately forced error (no SSO; no permission). Look for: where they hesitate on the consent page, whether *Export SQL* is found, whether the error text lets them act without asking us. Pass: all three finish the connect task unaided in under ten minutes and can say what was created.

## Accessibility and writing

Plain words, short sentences, no jargon on first view (a "Details" line holds the technical name); keyboard operable; status never by colour alone; screen-reader labels for the check list; works on a phone for the read-only screens (consent, result), though setup is expected on a desktop.

## Decisions for the owner

1. **Mode:** design Connect mode now, or stay on Sandbox and *Try with sample data* until a design partner exists? Recommended: keep Connect mode designed but unbuilt.
2. **Sign-in methods to show:** SSO and key and export-SQL (recommended). Include a typed password (security decision)?
3. **Naming:** what the objects are called on a customer's side (a visible, distinctive prefix such as `COMPASS_...`).
4. **Spending cap defaults:** one Snowflake credit and the smallest warehouse (as validated), or let the admin choose.
5. **Consent record:** keep it in the evidence trail (recommended).
6. **Admins for the usability test:** who can we show it to?
7. **Where it lives:** inside the analytics web app, or a separate setup app with its own sign-in and permission (recommended, to keep the admin tool away from the query path).

## Out of scope

Billing and plans, user management, SSO for the analysts, the draft-and-certify screens themselves (the auto-ontology work), and any build.

## Not verified

The copy, the flow and the time target are reasoned, not tested with users. The error text maps real codes, but the wording has not been tried on an admin. The redaction preview is a design of what the approved rule produces, not a built screen. Snowflake's and Databricks's own sign-in pages are outside our control and may change.
