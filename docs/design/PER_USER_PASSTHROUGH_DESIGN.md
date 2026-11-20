# Per-user identity pass-through: a design note (2026-11-20)

Status: **a proposal, nothing built.** It answers a question a customer with existing row-level security will ask: "does the product see only what each user may see in my warehouse?" It comes from the Mews post's lesson about per-user OAuth, deferred in `SEMANTIC_TRUST_FOUNDATIONS_PLAN.md` section 8A. Vendor behaviour below is from general knowledge and is **not verified on a workspace or account**; each point marked *check* needs the vendor's current reference before any build.

## What exists today

In Connect mode one **read-only principal per connection** runs every statement, limited to the named tables (the "ceiling"). The product enforces everything per user on top of that ceiling: certified metrics only, policy and cohort rules, a cost bound, a human review gate, and an evidence chain. The warehouse itself never learns who asked. So a customer's own row filters, column masks and grants (written for their users) are **not applied**; they are replaced by our rules and by whatever the connection principal may see.

That is a deliberate, safe default: nothing is shown that the principal could not read, and a user can never see more than the ceiling. The gap is the other direction: a customer whose rules are *narrower per user* than the principal's reach must re-express them in our policy, or accept that the product answers from the wider ceiling.

## Options

| | A. Stay on the principal | B. Every statement as the user | C. Hybrid (recommended if built) |
|---|---|---|---|
| Who runs the answer | the connection principal | the user, with their own OAuth token | the user's token for **execution**; the principal for everything that is not an answer |
| Customer's row filters and masks | not applied | applied by the warehouse | applied by the warehouse |
| Token custody and audit burden | none | high: a token per user, refresh, revocation | high, but only for answers |
| Dry-run, probes, drafting, oracle, evaluation | the principal | the user's token (fragile) | the principal (stable, comparable) |
| Failure when a token lapses | n/a | every feature fails | only that user's answers fail, closed |

**C keeps the stable machinery (certification probes, cost estimates, the oracle, evaluation) on one identity so results stay comparable, and uses the user's identity only where the customer's rules matter: running the certified, compiled statement.**

## What must be true before building C

1. **The compiled statement is unchanged.** The user's token only changes *who runs it*. The same certified compiler, allow-list, cost bound and timeout apply first; pass-through is additive and can only narrow what comes back.
2. **Custody.** Authorization code with PKCE and a public client, as the product-run sign-in already does (`app/connect/oauth.py`); a user's token is held in the existing single-use, hard-expiry wrapper style (`app/connect/token.py`) and never in a URL, record, log or evidence row. A refresh token, if any, goes only to the vault (`app/connect/vault.py`), tenant-scoped. *Check:* the vendor's token lifetime, refresh and revocation behaviour.
3. **Evidence names the user, never the token.** The evidence chain records a stable user id and the connection; it never records a token or a statement's result values beyond what it already does.
4. **No cross-user reuse.** Any cache, result store or cost estimate that could carry one user's masked values to another must be **keyed by user** or turned off for pass-through connections. This is the easiest place to leak and the first test to write.
5. **Answers legitimately differ by user.** A mask or filter changes the number, so a value compared with a pinned expectation or an oracle (E5) is only comparable on the same identity. Oracle and evaluation runs stay on the principal and are labelled as such; a user's answer is never "wrong" for differing from them.
6. **Fail closed.** No token, an expired token or a refused statement ends the run as a refusal with a stable code. The product never falls back to the principal for that user's answer: that would silently widen what they see.
7. **Per-connection switch, default off.** A tenant with no pass-through behaves exactly as today.

## Threats to add to the Connect threat model

Token theft or replay (custody); a confused deputy (running the user's statement with the principal's rights, or the reverse); a cache or result leak across users; evidence or logs carrying a token or masked value; a revoked user still answered from a cached token; consent phishing on the sign-in page. Each needs a numbered threat, a test and an owner before the build, in the same format as T1 to T25.

## What this does not solve

It does not make a customer's rules *correct*, only applied. It does not apply to the pilot's PostgreSQL. It does not remove the need for certified metrics: the warehouse's rules decide which rows exist for the user, our contract decides what the number means. And it cannot be tested here without a real identity provider and a warehouse that has row filters or masks, which is a live step.

## Decisions for the owner

1. **Build C, defer, or never** for a first customer. (Recommended: defer until a customer names row-level security as a requirement; keep this note as the design.)
2. **Databricks first** (the engine already in use), Snowflake after. (Recommended: yes.)
3. **A security review** of the threats above by someone other than the author before any build. (Recommended: required.)
4. **A live test needs** a workspace with a row filter or a column mask, two users, and your approval of the exact statements first.

Nothing in this note is approved, built or tested.
