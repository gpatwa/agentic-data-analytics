# Keeping the customer's warehouse warm: a design (2026-11-21)

Status: **built and tested with fakes, wired into the request path behind a per-connection switch that is off by default; nothing was run live.** (Wiring plan, last section: steps 1 to 5 are built; see "As built".) The live evidence (below, and `docs/evaluation/scale-test-tier-m-results.md`) is in; the wiring plan is the last section and needs the owner's decisions. Evidence is the scale test on tier M (`docs/evaluation/scale-test-tier-m-results.md`).

## The problem, as measured

After a table has not been read for a while, its first read comes from cloud storage: **5.4 s and 6.2 s** in two runs (11.2 MB read, 0% from the disk cache), against **0.35 s** once the data is in the warehouse's disk cache. **Correction (2026-11-22):** this is not only a stopped-warehouse effect. The starter warehouse is kept running by other jobs and it still happened, about 13 to 16 minutes after the table's last read. The cause is the data leaving the disk cache, for a reason not yet known. So the first question after a quiet period is slow and every question after it is fast, whether or not the warehouse stopped. Larger tables make the first read longer. Compile time (about 0.2 to 0.5 s) is not the cause.

## Options

| | What | Cost | Weakness |
|---|---|---|---|
| A. Longer auto-stop | raise the warehouse's auto-stop (a setting) | idle uptime up to the new limit, every time | a cloud change on the customer's warehouse; still cold after the limit |
| B. Keep-alive ping | a cheap query every few minutes | a 2X-Small up around the clock is 4 DBU/h, **96 DBU a day** whether or not anyone asks | pays for every idle hour; a trivial query does not load the data the questions read |
| **C. Warm on session open (chosen)** | when a user opens a session and the connection has been quiet, run a few certified read-only statements and discard the results | one to four sub-second statements, only after an idle gap | hides the delay only if the user takes a few seconds before asking; wakes a stopped warehouse (a start the first question would have paid anyway) |
| D. Bigger warehouse | more compute | more cost | does not remove a storage read; untested here (none could be created) |

A trivial `SELECT 1 ... LIMIT 1` (what the connection health check runs) keeps a warehouse running but reads almost nothing, so it does not warm the data a question reads.

## What C is, and what it must not become

`app/connect/warmup.py`, a `Warmer` kept per tenant and connection:

- **Off unless the connection turns it on.** A tenant that does nothing behaves as today.
- **Certified statements only.** The caller compiles them from the certified contract, the way a question is compiled (for example, one no-dimension aggregate per dataset, which reads the columns a question reads). The warmer adds no privilege: the same read-only principal, the same allow-list as an answer. It never sends anything to a model.
- **Results discarded.** Nothing is stored, logged or put in the evidence chain except an audit record the caller chooses to write: a code, a count and a duration, never a statement or a value.
- **Skipped when the warehouse is probably still running.** If the last warm-up or a real answer was less than `idle_after` ago (default 8 minutes, under the roughly 13 minutes in which the data was seen to leave the cache), nothing runs. A busy connection pays nothing.
- **Never breaks the request.** Any error becomes the stable code `failed`, with no driver message; a failed warm-up does not count, so the next session tries again. The deadline (30 s) and the statement cap (4) bound it. The caller should run it in the background and not wait for it.
- **One warm-up at a time per connection**, so two sessions opening together warm once, and one tenant never warms or learns of another's connection.

## Not built, and why

- **The wiring.** I found no "session open" point in the product's request path to hook (the API is question-based). Where it goes is a product decision: the sign-in or workspace load, the first screen of an analysis, or a client call the UI makes when a user starts typing. Each has a different amount of lead time.
- **The per-connection setting** and its owner (an admin turns it on, with the cost stated).
- **Statement choice.** One no-dimension statement per dataset is a guess; the disk cache is per column and file, so a question that reads other columns can still hit storage. This must be measured, not assumed.
- **Snowflake.** Its cache behaviour differs and is not measured at all.

## A live experiment to validate it (the first attempt failed; the redesigned one needs your approval)

The first version waited for the warehouse to stop; the starter warehouse never stops (other jobs), so it ended with `warehouse_did_not_go_idle` and no result. The redesign sleeps a fixed gap (20 minutes) since the last read, runs only the warmed round, and takes the control from the two earlier observations (5.4 s and 6.2 s), labelled as such: `--idle-minutes 20 --warmed-only --control-first-ms 5400`, 36 statements. The original text follows.


On the starter warehouse only, no creation: leave it idle past its 10-minute auto-stop, run the warm-up statements (the Warmer, with the statements from the scale test's workload), then the 32 statements. Pass if the first question's execution is about 0.35 s instead of 5 to 6 s. About 40 `SELECT`s, the 2X-Small, a 10-minute cap. The idle wait is the only slow part. It would also show whether one statement per dataset is enough.

Nothing here is approved, wired or measured live.


## What the live runs showed (2026-11-22, 10 times tier M, starter warehouse, one pair of runs)

After a 20-minute gap with no reads, the first workload statement took **8.5 s** and read 30.5 MB from remote storage (control). After two warm-up statements (17.3 s, paid by the warm-up, not by a question) the same statement took **2.9 s** and read 0.76 MB; the other 31 statements were unchanged (2.1 s median either way). So warming works and moves the cost off the user. It is one pair on a shared warehouse with replicated data.

## Wiring plan (a proposal; nothing here is built or approved)

**Where it hooks in.** The API has no "session open" call, only `POST /api/v2/analytics/analyze`. Three options:

| | Hook | Lead time | Cost | Weakness |
|---|---|---|---|---|
| 1 | A new `POST /api/v2/analytics/warm`, fire-and-forget, that the UI (or an agent) calls when a user opens the workspace or starts typing | seconds to minutes, best | one warm-up per idle gap | needs a client change; each client must call it |
| 2 | Inside `/analyze`: start the warm-up in a background thread when the connection has been quiet, in parallel with interpretation (the model call takes seconds before any warehouse read) | 1 to 3 s | same | the first question still waits for the cold read (the warm-up and the answer read the same data); it helps only by overlapping them |
| 3 | A timer that warms every ~8 minutes | none needed | the warehouse is up all day: **96 DBU a day on a 2X-Small** | that is option B in the table above, rejected |

**Recommendation: 1, with 2 as a fallback for clients that cannot call it.** Option 1 is the only one that gives the warm-up time to finish (17 s) before the first question. Option 2 alone would not fix the 8.5 s.

**The pieces, smallest first.**

1. A `warm_up` flag on the connection record, **off by default**, stored with the connection (a migration beside 0013, kept equal to the store's table definition by the existing test). Setting it is an admin action on the connect API, with the cost stated in the call's description (one short burst of reads per idle gap, and a stopped warehouse is started).
2. `app/connect/warmup_service.py`: given a tenant, take the connection (live, active, flag on), pick the statements, run them through the tenant's own gateway and compiler (the same ones a question uses, via `GovernedRuntime._tenant`), and hand each to `Warmer.warm` in a background thread. Results are discarded; the audit record is the `WarmResult` code, a count and a duration.
3. The endpoint: authenticated like `/analyze`, tenant taken from the identity (never from the body), returns `202` and the code at once (`warming`, `skipped_recent`, `skipped_disabled`), never waits, never returns a value. It is rate-limited per tenant (the `Warmer`'s `idle_after` already makes the second call within 8 minutes a no-op, so it cannot be used to keep a warehouse alive).
4. Option 2's hook as a call to the same service at the top of `/analyze`, behind the same flag.
5. `note_activity` called after every real answer, so a busy connection is never warmed.

**Statement choice.** The measured warm-up used `order_count` and `gross_order_value` with no dimension, and the first question afterwards still read 0.76 MB from storage, so the cache is per column and these two did not cover every column. Default: for each certified dataset in the tenant's contract, the no-dimension aggregate of its first two metrics, at most 4 statements. A later refinement could choose from the columns real questions touched; that needs data we do not have.

**Limits to say out loud.** Per replica, not shared (each replica warms once per idle gap; the extra is a few reads). Snowflake is not measured. A warm-up wakes a stopped warehouse, so the customer pays for that start: the flag description says so. Repeated data caches better than real data, so the 8.5 s may be different on a customer's own data.

**What I need from you before building.** (a) Hook: 1, 1 plus 2, or something else. (b) Admin-set flag per connection, or a tenant-wide default. (c) Whether the first release is Databricks only. Each build step above is small enough for one PR with fakes-only tests; nothing live is needed to build it, and a live check afterwards would need a fresh approval.


## As built (fakes only; no live check yet)

- `ConnectionRecord.warm_up` (default off), migration `0015_connect_warm_up`, and `PUT /api/v2/connect/databricks/connections/{id}/warm-up` (connection managers of the same tenant only; each change is a `connect.warm_up_changed` audit event). The connection list shows it.
- `app/connect/warmup_service.py`: `WarmUpService.request(tenant)` answers `warming`, `skipped_recent`, `skipped_disabled`, `skipped_busy` or `failed` at once and does the warehouse work on a background thread (at most four queued or running at once). It warms only a tenant's single active Databricks connection with the switch on. Statements: for each certified dataset of the tenant's contract, the no-dimension aggregate of its first two metrics, at most four, compiled by the tenant's own certified compiler and run through its own gateway with a one-row limit; results discarded. Each run writes a `connect.warm_up_ran` audit event with a code, a count and a duration only.
- `POST /api/v2/analytics/warm` (202 when warming, else 200 with the code; the tenant comes from the identity). `POST /analyze` calls `WarmUpService.on_question` first: it warms if the connection is idle and otherwise marks it busy so no warm-up is wasted; it never raises and never waits.
- `Warmer.recent()` (read only) so the call can answer without claiming anything.
- A contract that needs policy values to compile fails to warm (code `failed`); it does not guess values.
- Not done: a live check (needs a fresh approval); Snowflake; the browser CORS list allows only GET, POST and OPTIONS, so a browser calling the PUT switch directly would be refused (the same is true of the existing DELETE); the UI call to `/warm`.

## Proposed live check of the wired warm-up (prepared 2026-11-22; NOT approved, nothing run)

`python -m reference_stack.scale_test --experiment warmup_service` (code: `reference_stack/scale_test/service_check.py`, tested with fakes) runs the product's own `WarmUpService` and `Warmer` against the existing starter warehouse
and the x10 schema, with these steps and pass conditions:

1. Switch off: `request` answers `skipped_disabled` and sends nothing.
2. Switch on, then a 20-minute gap with no call: `request` answers `warming` at once; the background warm-up finishes with code `warmed`, running the statements `warm_plans` chooses (three with this contract: one on `orders_current`, two on `orders_usd_gross`).
3. A second `request` answers `skipped_recent`, and `on_question` sends nothing and records activity.
4. The 32 workload statements run, all answers checked; the first read is compared with the earlier control (8,502 ms) and experiment (2,939 ms), which came from one pair of runs and are not re-measured here.

What stands in for the real path: an in-memory connection record with no credential, and the owner's CLI profile instead of the OAuth connector, so the OAuth path itself is not exercised. At most 38 SELECT statements, 35-minute cap, nothing created, resized, stopped or deleted.

### The UI call (built 2026-11-22)

`apps/analytics-web`: `warm()` in `src/governed/workflow.ts` posts to `/api/v2/analytics/warm` with the signed-in token. `GovernedWorkspace` calls it once when a person signs in and again when the tab returns to the front. It is fire-and-forget: it never throws, shows nothing and sends no body, and does nothing in the public demo or when signed out. The server's idle gate makes repeats cheap. Typecheck and the 71 web tests pass; not exercised in a browser against a live API.
