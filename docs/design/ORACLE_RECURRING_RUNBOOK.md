# Recurring oracle check: runbook (L3)

**No schedule exists.** Nothing in this repository creates, registers or runs a scheduled job, a cloud resource or a standing connection. The owner approved
the command and this runbook only; a standing job is a separate decision that needs its own approval. This page says what a scheduler would call so that
decision can be made.

## What it is

An oracle is an independent statement, written by a contract's owner, that returns the number a question should produce now (`app/contract_review/oracle.py`).
The recurring check compares the product's real answer with that statement, through the same read-only gateway as every answer, for each eval case that has an
oracle. It catches the case where a customer's data, or a definition, has drifted away from the answer the product gives.

## What a scheduler would call

```bash
cd services/analytics-api && PYTHONPATH=.:../.. python3 -m app.contract_review.oracle_run \
    --contract <certified contract.json> --eval <eval file with oracles.json> \
    --factory <deployment_module>:build --max-statements 20 --max-seconds 300 --out result.json
```

* `--factory module:build` is supplied by the deployment, not this repository. `build(document)` returns `(gateway, answer)`: a gateway for the connection's
  read-only principal (same allow-list and cost bound as answering) and the callable that produces the product's real answer for a case. Without a factory
  the command refuses (exit 2): it opens no connection and calls no model of its own.
* The eval file's `contract_id` must equal the contract's id, or the run is refused.

## Exit codes

| Code | Meaning | Scheduler action |
|---|---|---|
| 0 | Every case matched, the run was complete | none |
| 1 | Any mismatch, `oracle_failed`, `answer_failed`, `not_run` (cap reached), or no case had an oracle | alert |
| 2 | Refused: bad input, contract/eval mismatch, no factory, or the factory raised | alert (the run did not check anything) |

A scheduler must alert on any non-zero code. Exit 2 is not "nothing to report": it means nothing was checked.

## The result file

JSON with `contract_id`, `contract_version`, `certified: false`, per-case `{case_id, status, detail}`, a `tally`, `statements_used`, the caps, `elapsed_seconds`,
`complete` and `ok`. Statuses: `match`, `mismatch`, `oracle_failed`, `answer_failed`, `not_run`. It holds **no row value, no number and no driver message**; a
failure carries the exception type only (a driver message can quote data). Case ids are chosen by the eval file's author, so do not put data in them.

## The alert path: the owner must name it before any schedule exists

| Item | Value |
|---|---|
| Owner (a person or team who reads the alert) | **not named** |
| Channel (where a non-zero exit is delivered) | **not named** |
| What the owner does on `mismatch` | **not decided** |

Until these three are named, a schedule would run unattended and its failures would go nowhere. That is the reason none is created.

## Cost cap per run

* At most `--max-statements` oracle statements (default 20) and `--max-seconds` wall-clock (default 300) per run. Each oracle is one statement through the
  gateway, which also applies its own row, byte, cost-unit and timeout limits (the per-statement timeout is cut to the time left in the run).
* The cap is checked before each case. A case the cap stops is reported `not_run` and the run exits 1: a capped run never reads as a pass.
* A statement counting wrapper refuses any statement past the cap as a backstop.
* The cap counts **oracle** statements only. The product's answer path (the `answer` callable) runs its own statements under its own limits; the owner should
  budget for one answer per case on top.
* Warehouse compute behind each statement is the customer's cost; this repository cannot price it.

## What a result means

* `match`: the compiler, the definition as written and the data agreed on that number today. It is **never** "certified", and the result file says
  `certified: false`. It does not show the business meaning is right.
* `mismatch`: the product's answer and the independent statement differ beyond the case's tolerance. It is reported, never hidden, and is never averaged away.
  It does not say which side is wrong: the definition, the oracle, the data or the compiler may be. A person investigates; do not re-certify around it.
* `oracle_failed`: the independent statement was refused or failed (not read-only, outside the allow-list, not exactly one number, timeout, driver error).
  Never a pass.
* `answer_failed`: the product's own answer failed or was not a number; the oracle was not run for that case. This points at the product path, not the data.
* `not_run`: the cap stopped the run first.

## What is not covered

* Whether a definition is right for the business (that is certification, a human gate).
* A change in model behaviour (the answer callable decides whether a model is involved; a model-free replay is `app.contract_review`).
* Cases without an oracle (they are skipped, and a run with none exits 1).
* Anything outside the oracles the owner wrote: the check is as strong as those statements and no stronger.
* Multi-row or non-numeric answers (an oracle must return exactly one number).
* Freshness, pipeline health or access drift beyond what an oracle happens to touch.

## Before a schedule is ever created (needs the owner)

1. Name the owner and the alert channel above, and what happens on a mismatch.
2. Approve the cost cap and the deployment's factory (which principal, which allow-list).
3. Approve the standing resource and where it runs. Not part of this change.
