# Keeping the customer's warehouse warm: a design (2026-11-21)

Status: **the component is built and tested with fakes; it is not wired into any request path and nothing was run live.** Evidence is the scale test on tier M (`docs/evaluation/scale-test-tier-m-results.md`).

## The problem, as measured

After a warehouse has been idle, the first read of a table comes from cloud storage: **5.4 s and 6.2 s** in two runs (11.2 MB read, 0% from the disk cache), against **0.35 s** once the data is in the warehouse's disk cache. The cache lives only while the warehouse runs, and the starter warehouse auto-stops after 10 minutes. So the first question after a quiet period is slow, and every question after it is fast. Larger tables make the first read longer. Compile time (about 0.2 to 0.5 s) is not the cause.

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
- **Skipped when the warehouse is probably still running.** If the last warm-up or a real answer was less than `idle_after` ago (default 8 minutes, under the starter's 10), nothing runs. A busy connection pays nothing.
- **Never breaks the request.** Any error becomes the stable code `failed`, with no driver message; a failed warm-up does not count, so the next session tries again. The deadline (30 s) and the statement cap (4) bound it. The caller should run it in the background and not wait for it.
- **One warm-up at a time per connection**, so two sessions opening together warm once, and one tenant never warms or learns of another's connection.

## Not built, and why

- **The wiring.** I found no "session open" point in the product's request path to hook (the API is question-based). Where it goes is a product decision: the sign-in or workspace load, the first screen of an analysis, or a client call the UI makes when a user starts typing. Each has a different amount of lead time.
- **The per-connection setting** and its owner (an admin turns it on, with the cost stated).
- **Statement choice.** One no-dimension statement per dataset is a guess; the disk cache is per column and file, so a question that reads other columns can still hit storage. This must be measured, not assumed.
- **Snowflake.** Its cache behaviour differs and is not measured at all.

## A live experiment to validate it (needs your approval, not run)

On the starter warehouse only, no creation: leave it idle past its 10-minute auto-stop, run the warm-up statements (the Warmer, with the statements from the scale test's workload), then the 32 statements. Pass if the first question's execution is about 0.35 s instead of 5 to 6 s. About 40 `SELECT`s, the 2X-Small, a 10-minute cap. The idle wait is the only slow part. It would also show whether one statement per dataset is enough.

Nothing here is approved, wired or measured live.
