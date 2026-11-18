# Connect mode, product-run setup: design (CM5, 2026-11-17)

Status: **a design for the owner to review. Nothing here is built, approved or tested.** It follows the owner's decisions of 2026-11-17: product-run setup is the intended default for Connect mode, the exported script is the fallback, and identity federation is the runtime direction (`connect_mode_admin_setup_product_run`, `connect_mode_product_run_confirmed` in the program file). It approves no build, no security gate, no live run and no customer connection. Facts about vendors come from their public documentation, read on 2026-11-17, and are marked **verified from docs** (not tested on an account) or **not stated**.

## What "product-run" means, and the question this design answers

The admin signs in through the vendor's own page, sees the exact list of actions, approves it, and Compass runs it. The admin token lives in memory for that run only. The CM3 worker already does everything after the token arrives (single-use approval of an exact digest, preflight, execute, verify grants, roll back only what it created, wipe). **What is missing is how the token gets to the worker without a script and without a pasted secret.** This document designs that and finds what stands in the way.

## What the vendor documentation says (Snowflake first, because it is the first partner's engine)

| # | Fact | Source | Consequence |
|---|---|---|---|
| 1 | A third party signs in through a Snowflake **OAuth security integration** of type custom, created with `CREATE INTEGRATION` (held by `ACCOUNTADMIN` by default, grantable). It yields a client id; a confidential client also has a secret read with `SYSTEM$SHOW_OAUTH_CLIENT_SECRETS`. | create-security-integration-oauth-snowflake, oauth-custom | A one-time admin step in the customer's account is unavoidable. |
| 2 | **`ACCOUNTADMIN`, `SECURITYADMIN`, `ORGADMIN` and `GLOBALORGADMIN` are blocked from OAuth by default.** The two pages disagree on whether that can be changed: one names an account parameter (`OAUTH_ADD_PRIVILEGED_ROLES_TO_BLOCKED_LIST`), the other says the roles "cannot be removed" and to contact Snowflake Support. These roles also cannot be pre-authorised. | same | **Compass cannot sign in as an account admin through OAuth, and should not want to.** The role must be a purpose-built one. |
| 3 | **Only `ACCOUNTADMIN` can create a resource monitor.** Whether creation can be delegated is **not stated**. | resource-monitors, create-resource-monitor | The current setup statements (`CREATE RESOURCE MONITOR`) cannot be run through OAuth. The monitor must be created in the one-time admin step. |
| 4 | The role is requested with the scope `session:role:<NAME>` (case-sensitive, one only); a role the user lacks is rejected only after sign-in. | oauth-custom | The consenting person must hold the delegated role. |
| 5 | Access token: 600 seconds by default (60 to 3600). Refresh tokens are on by default (`OAUTH_ISSUE_REFRESH_TOKENS`), 90 days. | create-security-integration | Turn refresh tokens **off**: Compass never needs to act again without the admin. 600 seconds suits a setup run. |
| 6 | PKCE is optional unless `OAUTH_ENFORCE_PKCE = TRUE`. Authorization endpoint `<account url>/oauth/authorize`; token endpoint `<account url>/oauth/token-request`. | same | Enforce PKCE. |
| 7 | Redirect URI must use TLS unless explicitly relaxed, and carry no query parameters. Alternates are allowed. | same | One fixed, exact, HTTPS callback per environment. |
| 8 | Client types are `CONFIDENTIAL` and `PUBLIC`. Whether a public client has or needs a secret is **not stated**. The token endpoint also accepts `private_key_jwt` (an RSA key pair instead of a secret); the page does not link that to client types or say how the public key is registered. | same | **How Compass authenticates itself without a pasted secret is unresolved.** See "Decisions", question 2. |
| 9 | The integration can carry a `NETWORK_POLICY` that covers the code exchange and token use. `OAUTH_USE_SECONDARY_ROLES` defaults to `NONE`. | same | Restrict the integration to Compass's addresses; keep secondary roles off. |

Databricks: a custom app connection is registered in the account console and yields a client id; a confidential app also has a secret, **shown once**. Public clients, PKCE, redirect rules and who may register are **not stated** on the pages read. Access tokens last an hour by default (configurable). Whether a workspace admin's token can create service principals is **not stated**. Databricks product-run is therefore not designed here beyond noting that it has the same shape and the same secret problem.

## What this does to the design

Product-run cannot be "the admin signs in as an admin and Compass does what the script does". Because admin roles are blocked from OAuth and monitors need `ACCOUNTADMIN`, product-run needs **a one-time registration step, run by an account admin, that is larger than today's script**, and a **delegated setup role** that product-run signs in as. Honest summary: product-run removes the script from every setup *after* the first, at the cost of a standing integration and a standing delegated role in the customer's account. For a first partner with one warehouse it adds work and surface without removing their one script. It pays off for repeated setups, several warehouses, or a partner who will not run SQL at all.

### Phase 0: registration (one time, an admin runs a Compass-generated script)

Generated by the same kind of code as `app/connect/grants.py`, shown on the same review page with a digest. It creates, all with per-connection-suffixed names and a tag comment, so teardown drops only what it made:

1. **The OAuth security integration**: custom client, redirect URI the fixed Compass callback, `OAUTH_ENFORCE_PKCE = TRUE`, `OAUTH_ISSUE_REFRESH_TOKENS = FALSE`, access token validity 600, the default blocked roles left in place, `NETWORK_POLICY` set to Compass's egress addresses, secondary roles `NONE`. The client type and how Compass authenticates are decision 2 below.
2. **A delegated setup role** with only what product-run needs: the account privileges to create a role, a user and a warehouse, and **no `MANAGE GRANTS`** and no admin role.
3. **A scope ceiling**: `GRANT SELECT ... WITH GRANT OPTION` on exactly the databases, schemas or tables the admin allows Compass to *ever* expose, to the setup role. Product-run can then only grant a subset of the ceiling to the read-only role it creates; it cannot grant anything outside it. The admin chooses the ceiling on the review page, in plain words.
4. **The resource monitor** and, to avoid needing `ACCOUNTADMIN` later, **the shared warehouse** the read-only roles will use (so per-setup runs only grant `USAGE` on it, with grant option on the setup role).
5. **The network policy** object for the read-only users, if user-level policies cannot be attached by the setup role (not stated).

Every privilege in items 2 to 5 is a **design intent, not a verified list**: which exact privileges let a non-`ACCOUNTADMIN` role create users and roles, grant within a ceiling, and attach a network policy to a user it created must be checked on a sandbox before any code is written (acceptance 1).

### Phase 1: sign-in (hosted OAuth, authorization code with PKCE)

1. The Compass user starts a setup (as in CM4). Compass creates a `PendingSetup`, a random `state` bound to tenant, setup, the signed-in Compass user and a 10-minute expiry, and a PKCE verifier kept server-side only.
2. The browser is sent to `<account url>/oauth/authorize` with the client id, the fixed redirect URI, `state`, the PKCE challenge (S256) and `scope=session:role:<SETUP ROLE>`. The admin signs in on **Snowflake's own page**; Compass never sees the password or the IdP.
3. Snowflake redirects to the Compass callback with a code. The callback checks `state` (single use, unexpired, same tenant and user), exchanges the code **server-side** at `/oauth/token-request` with the PKCE verifier, and gets an access token. **The token is never sent to the browser.**
4. The token goes straight into an `AdminToken` (CM3: capped lifetime, unprintable, wiped). The callback then redirects the browser to the review page. No token, code or verifier is placed in a URL, a log or a record.
5. Before anything runs, the worker checks the token belongs to the account the setup targets (the host Compass validated, and `CURRENT_ACCOUNT()` from the connection), so a sign-in on the wrong account fails closed.

### Phase 2 and 3: approve, run, verify

Unchanged from CM3 and CM4: the admin approves the digest of the exact statements on the review page, the worker runs them once as the delegated role, verifies the grants, and wipes the token; the connection test then runs as the new read-only identity. The statement list for product-run is the *per-setup* list (create role, user, grants within the ceiling, key); it is smaller than today's script because the monitor and warehouse already exist.

## New threats this introduces (to be added to the threat model table when PR #60 lands)

| ID | Threat | Control in this design | Rating |
|---|---|---|---|
| T24 | Callback CSRF or code injection: an attacker feeds a victim's browser or the callback a code or state of their own | `state` bound to tenant, setup, user and expiry, single use; PKCE S256 with a server-side verifier; the callback refuses anything unbound | H |
| T25 | Redirect URI hijack or open redirect | One exact HTTPS redirect registered in the integration, no query parameters, no alternates; the post-callback redirect goes only to a fixed path on our own host | H |
| T26 | The standing delegated role and integration are abused (**note from the spike: the setup role holds `SELECT` on the ceiling tables, so it can read them while signed in as it; the review text says so, and the ceiling should be chosen with that in mind**) (by Compass, an attacker with our secrets, or a rogue admin) | No `MANAGE GRANTS`, no admin role, blocked roles left in place; grant-option **ceiling** chosen by the admin; refresh tokens off; tokens only after a human signs in and consents; integration restricted to Compass addresses; the whole set removable with the teardown script | H |
| T27 | Wrong-account or confused-deputy sign-in (the admin signs in to a different Snowflake account than the setup targets) | Account checked against the validated host and `CURRENT_ACCOUNT()` before any statement runs | M |
| T28 | The registration script is broader than the admin understood | The review page lists the ceiling, the privileges and the integration settings in plain words, with the digest; same signing and verification as T15 | M |
| T29 | Compass's own client credential (a secret or private key) is stolen | Decision 2: prefer a public client or key-based client authentication so no secret is handed over; if a secret is unavoidable it is a per-customer secret in the tenant store and a known, labelled weakness | H until decided |

## Decisions for the owner

1. **Is this the right trade for the first design partner?** The facts above mean product-run needs a larger one-time script than the script path and a standing delegated role. Options: build it now, or serve the first partner with the script path and build product-run when a partner needs repeated setups or refuses SQL. Recommendation: the second, with this design kept as the plan. (You chose to keep product-run on 2026-11-17 before these facts were known; this is a new fact set, not a request to reopen the decision.)
2. **How does Compass authenticate to the integration without a pasted secret?** A public client, or `private_key_jwt` with Compass's own key pair registered in the integration, or a per-customer secret read from `SYSTEM$SHOW_OAUTH_CLIENT_SECRETS` and handed over. The pages do not settle the first two; this is the first thing to check on a sandbox. Recommendation: do not ship a secret handoff.
3. **The callback host.** One fixed HTTPS hostname owned by Compass for the redirect URI, per environment. Which?
4. **Whether to ask Snowflake whether Compass can become a pre-registered partner application**, which would remove the registration step. A business and engineering question, not a coding one.

## Build stages (each local and fake-based unless it says otherwise)

| Stage | Work | Needs |
|---|---|---|
| **CM5a** | The registration script generator (integration, delegated role, ceiling, monitor, warehouse, network policy), its grants verifier and teardown, with tests and an admin-readable review page. **Built 2026-11-17, local only** (`app/connect/registration.py`, 84 tests, reviewable example `docs/design/CONNECT_REGISTRATION_SCRIPT.md`): the script, a verifier that compares `SHOW GRANTS TO ROLE` and `DESCRIBE SECURITY INTEGRATION` with exactly what it creates, the teardown, the plain-language summary, and per-connection statements that refuse anything outside the ceiling. The review page UI is CM5d. The network policy, `NETWORK_POLICY` on the integration and the `https` redirect remain **not verified live**. | nothing live |
| **CM5b** | The OAuth client: authorize-URL builder, `state` and PKCE store, callback handler, code exchange against a **fake token endpoint**, tests for every row of T24 and T25. **Built 2026-11-17, local only** (`app/connect/oauth.py`, `oauth_api.py`): a public PKCE client with no secret; one single-use unguessable state per sign-in with the verifier kept server-side and wiped after its one use; a browser-binding cookie (HttpOnly, SameSite=Lax, Secure by default) checked before the state is consumed, so a forged callback cannot burn a real sign-in and an attacker's state in a victim's browser is refused; one fixed https redirect; a callback that only redirects to a fixed path or a fixed error path carrying one short allow-listed code (`no-store`, `no-referrer`); a per-person cap on sign-ins in progress; a single-use, tenant-scoped hand-off of the `AdminToken`. The callback needs no API key (a browser redirect cannot send one). Limits: in-memory state; the deployment must not log the callback's query string | nothing live |
| **CM5c** | Wire the token to the CM3 worker through the Snowflake connector's OAuth authenticator; the wrong-account check; the per-setup statements within the ceiling. **Built 2026-11-17, local only** (`app/connect/product_run.py`, `product_run_api.py`, mounted inert in `main.py`): register once, plan inside the ceiling (anything outside is refused before a key, approval or record exists), approve the exact digest, sign in, run. The order is enforced: `run` refuses an unapproved setup **before** touching the token. After sign-in and **before anything is created** the worker checks that the token's `CURRENT_ACCOUNT()` is the registered account (T27), that the role is the registered setup role, and that the setup role's grants still equal exactly what the registration created (a widened or narrowed ceiling stops the run); the preflight checks only the role and user (the warehouse and monitor are pre-created); the worker's rollback and wipe are unchanged. 45 tests on a fake Snowflake that keeps state; eleven deliberate breaks each caught | nothing live |
| **CM5d** | The screens: register once, sign in, approve, done | nothing live |
| **CM5e** | **Live sandbox verification** of the privilege list, the integration settings, the sign-in and the rollback on the owner's Snowflake account | **the owner's approval for each live step** |

CM5e comes first in value even though it is last in order: until the privilege list and the client-authentication question are verified, CM5a to CM5c risk being built on a wrong assumption. The recommended order is therefore a small CM5e-style **verification spike** (a sandbox integration and role, no product code) before CM5a.

## Acceptance (what would have to be true before this is considered built, not approved)

1. The delegated role's exact privileges, the ceiling grants, the resource-monitor and warehouse arrangement, and the network-policy attachment are shown to work on a sandbox account.
2. A client-authentication method that needs no pasted secret is shown to work, or the secret handoff is a recorded, accepted weakness.
3. Fake-endpoint tests cover the forged state, replayed code, wrong tenant, expired state, mismatched redirect, missing PKCE, token in any log or record, and wrong-account cases.
4. The token appears nowhere after a run (the CM3 leak tests extend to the callback path).
5. The registration and teardown scripts verify their own results the way `verify_grants` does.

## Not verified

Everything in "What the vendor documentation says" is from documentation, not from an account. Two Snowflake pages disagreed (whether privileged roles can be unblocked, and the refresh-token minimum) and the excerpts for two pages were cut partway. The delegated-privilege list is my design intent. Databricks product-run is not designed. No account was touched.
