"""End to end on DuckDB: a business question goes through the real governed path and the number is compared with an independent answer.

Real parts: the governed runtime (identity, context, intent node, resolution, echo and coverage checks, planning, the deterministic compiler, the
policy and cost checks, the DuckDB read-only gateway, answer validation, the evidence chain). Stand-in part: the language model. A model-shaped
reply (a typed intent, or `{"unsupported_request": true}`) is supplied per question, and in the recorded runs it came from a fresh Claude agent that
saw only the exact prompt the product builds (certified vocabulary and the question), never the answers.

The answer keys are computed outside the product: plain Python over the source files, or an independent DuckDB statement over the source, never
through the compiler. A contract that the owner has not certified is run as an EVALUATION FIXTURE (its lifecycle is set to certified in a temporary
copy only); that is not a certification and is said so in the report.

Statuses: `correct` (answered, equals the key), `wrong_answer` (answered, differs from the key), `wrongly_answered` (should have been refused but
was answered), `correct_refusal`, `false_refusal` (should have answered, refused: a gap, not a wrong number), `error`.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api_v2 import V2Runtime, build_v2_router
from app.compiler import DuckDBCompilerAdapter
from app.compiler.service import CertifiedIntentCompiler
from app.context.registry_context import CertifiedContracts, certified_vocabulary, context_from_contract
from app.execution import DuckDBGateway
from app.execution.gateway import ExecutionLimits
from app.runtime import pilot_wiring
from app.runtime.evidence_store import EvidenceStore
from app.runtime.pilot_wiring import GovernedRuntime
from packages.platform_contracts.semantic import SemanticRegistryDocument
from reference_stack.stack import build_stack

TENANT = "tenant-a"
PURPOSE = "analytics"
URL = "/api/v2/analytics"
STATUSES = ("correct", "wrong_answer", "wrongly_answered", "correct_refusal", "false_refusal", "error")


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    expect: str  # answer | refuse
    key: Any = None  # answer: a number, or {label: number} (compared on the labels given)
    note: str = ""  # why a refusal is expected, or what a gap needs


@dataclass(frozen=True)
class Dataset:
    name: str
    document: SemanticRegistryDocument
    sources: dict[str, Path]  # physical table name -> Parquet file
    cases: tuple[Case, ...]
    certified_by_owner: bool
    note: str = ""
    tolerance: Decimal = field(default=Decimal("0.01"))


def with_synonyms(dataset: Dataset, synonyms: dict[str, list[str]]) -> Dataset:
    """The same dataset with synonyms on metrics and dimensions (by id). The product's own validator checks them; a rejected set raises."""
    raw = dataset.document.model_dump(mode="json")
    for kind in ("metrics", "dimensions"):
        for item in raw["contract"][kind]:
            item["synonyms"] = list(synonyms.get(item["id"], []))
    return Dataset(dataset.name, SemanticRegistryDocument.model_validate(raw), dataset.sources, dataset.cases, dataset.certified_by_owner, dataset.note, dataset.tolerance)


def accept_synonyms(dataset: Dataset, proposed: dict[str, list[str]]) -> tuple[Dataset, dict[str, list[str]], list[tuple[str, str, str]]]:
    """Add proposed phrases one at a time and keep each the product's validator accepts (it rejects overlaps, ids and bad shapes). Returns the dataset,
    the phrases kept and the rejected ones with the validator's code: the same per-word screen the drafter applies."""
    kept: dict[str, list[str]] = {}
    rejected: list[tuple[str, str, str]] = []
    current = dataset
    for item_id, phrases in proposed.items():
        for phrase in phrases:
            trial = {**kept, item_id: [*kept.get(item_id, []), phrase]}
            try:
                current = with_synonyms(dataset, trial)
            except ValueError as exc:
                rejected.append((item_id, phrase, str(exc).splitlines()[-2].strip().split(" ")[0] if "\n" in str(exc) else str(exc)[:40]))
                continue
            kept = trial
    return current, kept, rejected


def _fixture_document(document: SemanticRegistryDocument) -> SemanticRegistryDocument:
    raw = document.model_dump(mode="json")
    raw["lifecycle"] = "certified"
    raw["contract"]["tenant_id"] = TENANT
    return SemanticRegistryDocument.model_validate(raw)


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _request_text(prompt: str) -> str:
    """The user's question as the product put it into the prompt (a JSON string on the line that starts with `Request:`)."""
    for line in prompt.splitlines():
        if line.startswith("Request: "):
            try:
                return str(json.loads(line[len("Request: "):]))
            except ValueError:
                return ""
    return ""


class _Model:
    """The stand-in model: captures the prompt, or answers from recorded replies."""

    def __init__(self, env: "Env") -> None:
        self.env = env

    def __call__(self, request_id: str) -> "_Model":
        self.rid = request_id
        return self

    def complete_json(self, *, prompt: str, schema: dict, max_tokens: int) -> Any:  # noqa: ARG002
        env = self.env
        env.prompts[env.current] = prompt
        if env.live is not None:
            return env.live.complete_json(prompt=prompt, schema=schema, max_tokens=max_tokens)
        reply = env.replies.get(env.current) if env.current else env.by_question.get(_norm(_request_text(prompt)))
        if reply is None:
            return {"unsupported_request": True}
        if isinstance(reply, dict) and "metrics" in reply:
            return {**reply, "query_id": self.rid, "tenant_id": TENANT}
        return reply


class Env:
    def __init__(self, dataset: Dataset, workdir: Path, replies: dict[str, Any] | None = None, *, coverage: bool = True, cost_cap: float | None = None, confirm: str | None = None, s6: bool = False, live: Any = None) -> None:
        self.dataset, self.replies, self.prompts, self.current, self.n = dataset, replies or {}, {}, "", 0
        self.live = live  # a structured-output client for a live model; when set it answers every prompt instead of the recorded replies
        self.confirm = confirm  # None: S6 off. "careful": confirms a reading only when the question should be answered. "habitual": always says yes.
        document = dataset.document if dataset.document.lifecycle == "certified" and dataset.document.contract.tenant_id == TENANT else _fixture_document(dataset.document)
        registry = workdir / "registry"
        (registry / "contracts").mkdir(parents=True)
        (registry / "contracts" / f"{document.contract.id}.json").write_text(document.model_dump_json())
        allowed = Path(os.path.commonpath([str(Path(p).resolve().parent) for p in dataset.sources.values()]))
        stack = build_stack("duckdb", workdir / "control")
        # The pilot's default bounds (a run budget of 10,000 cost units, an execution limit of 1,000,000) are sized for small tables. `cost_cap` raises both,
        # for a real table of a million rows; without it the product's own defaults apply and the report shows what they refuse.
        self._limits = (lambda: ExecutionLimits(max_cost_units=cost_cap)) if cost_cap is not None else ExecutionLimits
        with mock.patch.object(pilot_wiring, "ExecutionLimits", self._limits):
            runtime = GovernedRuntime(
                control=stack.control, evidence=EvidenceStore(stack.control.engine), gateway=DuckDBGateway(dataset.sources, allowed_root=allowed),
                compiler=CertifiedIntentCompiler(registry, DuckDBCompilerAdapter()), contracts=CertifiedContracts([document]),
                contexts={TENANT: context_from_contract(document)}, vocabularies={TENANT: certified_vocabulary(document)},
                model_factory=_Model(self), coverage_tenants=frozenset({TENANT}) if coverage else frozenset(), review_threshold=1e12,
                confirm_tenants=frozenset({TENANT}) if (confirm or s6) else frozenset(),
                **({} if cost_cap is None else {"max_cost_units": cost_cap}),
            )
            service = runtime.service()
            feedback = runtime.feedback_service()
        app = FastAPI()

        async def no_key() -> None:
            return None

        app.include_router(build_v2_router(V2Runtime(service=service, verifier=stack.verifier(), feedback=feedback), no_key))
        self.app, self.client, self.stack = app, TestClient(app), stack
        self.by_question = {_norm(c.question): self.replies[c.id] for c in dataset.cases if c.id in self.replies}  # for a live server, where no case id is known

    def ask(self, case: Case) -> dict[str, Any]:
        self.current, self.n = case.id, self.n + 1
        headers = {"Authorization": f"Bearer {self.stack.token('requester')}", "Idempotency-Key": f"e2e-duckdb-{self.n:06d}-{case.id}"}
        with mock.patch.object(pilot_wiring, "ExecutionLimits", self._limits):
            body = self.client.post(f"{URL}/analyze", json={"request_text": case.question, "purpose": PURPOSE}, headers=headers).json()
            if body.get("state") == "waiting_clarification" and self.confirm:
                (question,) = body["outcome"]["questions"]
                say_yes = self.confirm == "habitual" or case.expect == "answer"
                choice = question["choices"][0]["id"] if say_yes else "none"
                auth = {"Authorization": f"Bearer {self.stack.token('requester')}"}
                body = self.client.post(f"{URL}/runs/{body['run_id']}/clarify", json={"purpose": PURPOSE, "ambiguity_code": "interpretation", "selected_id": choice}, headers=auth).json()
                self.asked = getattr(self, "asked", 0) + 1
            return body


def capture_prompts(dataset: Dataset, workdir: Path | None = None, *, coverage: bool = True) -> dict[str, dict[str, str]]:
    """The exact prompt the product sends its model for every question (so a stand-in model sees what a real one would)."""
    own = workdir is None
    work = Path(tempfile.mkdtemp()) if own else workdir
    try:
        env = Env(dataset, work, coverage=coverage)
        for case in dataset.cases:
            env.ask(case)
        return {c.id: {"question": c.question, "prompt": env.prompts.get(c.id, "")} for c in dataset.cases}
    finally:
        if own:
            shutil.rmtree(work, ignore_errors=True)


def _num(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _close(a: Decimal, b: Decimal, tol: Decimal) -> bool:
    return abs(a - b) <= tol


def _label(value: Any) -> str:
    text = str(value)
    return text[:10] if len(text) >= 10 and text[:4].isdigit() and text[4] == "-" else text  # a timestamp label is compared on its date


def judge(case: Case, body: dict[str, Any], tol: Decimal) -> tuple[str, str]:
    out = body.get("outcome") or {}
    if not isinstance(out, dict) or "outcome" not in out:
        return "error", f"no outcome ({body.get('state')})"
    kind = out["outcome"]
    if case.expect == "refuse":
        return ("wrongly_answered", f"answered: {out.get('answer')}") if kind == "answer" else ("correct_refusal", out.get("reason_code") or kind)
    if kind != "answer":
        return "false_refusal", f"{kind}: {out.get('reason_code') or ''}".strip()
    rows = (out.get("result") or {}).get("rows") or []
    if isinstance(case.key, dict) and len(case.key) == 1 and len(rows) == 1 and len(rows[0]) == 1:
        # "How many orders in New York?" answered as one filtered number, not a table: compare that number with the one key value
        value, want = _num(rows[0][0]), Decimal(str(next(iter(case.key.values()))))
        return ("correct", str(value)) if value is not None and _close(value, want, tol) else ("wrong_answer", f"got {value}, key {want}")
    if isinstance(case.key, dict):
        got = {_label(r[0]): _num(r[-1]) for r in rows}
        bad = [k for k, v in case.key.items() if got.get(_label(k)) is None or not _close(got[_label(k)], Decimal(str(v)), tol)]
        return ("correct", f"{len(case.key)} values match") if not bad else ("wrong_answer", f"differs on {bad[:3]}: got {[str(got.get(_label(b))) for b in bad[:3]]}")
    value = _num(rows[0][0]) if rows and rows[0] else None
    if value is not None and _close(value, Decimal(str(case.key)), tol):
        return "correct", str(value)
    return "wrong_answer", f"got {value}, key {case.key}"


def score(dataset: Dataset, replies: dict[str, Any], workdir: Path | None = None, *, coverage: bool = True, cost_cap: float | None = None, confirm: str | None = None) -> list[dict[str, Any]]:
    own = workdir is None
    work = Path(tempfile.mkdtemp()) if own else workdir
    try:
        env = Env(dataset, work, replies, coverage=coverage, cost_cap=cost_cap, confirm=confirm)
        results = []
        for case in dataset.cases:
            try:
                status, detail = judge(case, env.ask(case), dataset.tolerance)
            except Exception as exc:  # noqa: BLE001 - one broken case must not hide the rest
                status, detail = "error", type(exc).__name__
            results.append({"id": case.id, "question": case.question, "expect": case.expect, "status": status, "detail": detail, "note": case.note})
        return results
    finally:
        if own:
            shutil.rmtree(work, ignore_errors=True)


def render_report(dataset: Dataset, results: list[dict[str, Any]], *, model_note: str, setup: str = "") -> str:
    counts = Counter(r["status"] for r in results)
    answerable = [r for r in results if r["expect"] == "answer"]
    lines = [f"# End to end on DuckDB: {dataset.name}", "",
             f"Contract: {'certified by the owner' if dataset.certified_by_owner else '**an evaluation fixture, not certified by the owner** (lifecycle set to certified in a temporary copy only)'}. {dataset.note}",
             f"Model: {model_note}", f"Setup: {setup}" if setup else "", "Answer keys: computed outside the product (plain Python over the source files, or an independent statement), never through the compiler.", "",
             "| Status | Count |", "|---|---|"] + [f"| {s} | {counts.get(s, 0)} |" for s in STATUSES] + [
             "", f"Answerable questions answered correctly: **{sum(r['status'] == 'correct' for r in answerable)} of {len(answerable)}**. "
             f"Wrong numbers: **{counts.get('wrong_answer', 0)}**. Questions that should have been refused but were answered: **{counts.get('wrongly_answered', 0)}**.", "",
             "| ID | Question | Expected | Result | Detail |", "|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['question']} | {r['expect']} | {r['status']} | {str(r['detail'])[:90].replace('|', '/')} |" for r in results]
    return "\n".join(lines) + "\n"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


SETUPS = (
    ("A", "As authored (cost bounds raised for a table this size, coverage check on)", dict(coverage=True, cost_cap=1e9), False, None),
    ("B", "A plus synonyms proposed blind by a fresh agent (kept only if the validator accepts each)", dict(coverage=True, cost_cap=1e9), True, None),
    ("C", "B with the product's own default cost bounds (a run budget of 10,000 units)", dict(coverage=True), True, None),
    ("D", "B plus confirm-instead-of-refuse (S6) answered by a careful user (confirms only a right reading)", dict(coverage=True, cost_cap=1e9, confirm="careful"), True, "careful"),
    ("E", "B plus S6 answered by a habitual user (always says yes)", dict(coverage=True, cost_cap=1e9, confirm="habitual"), True, "habitual"),
)


def run_matrix(dataset: Dataset, replies: dict[str, Any], proposed_synonyms: dict[str, list[str]]) -> dict[str, Any]:
    with_syn, kept, rejected = accept_synonyms(dataset, proposed_synonyms)
    runs = {}
    for key, label, kw, use_synonyms, _ in SETUPS:
        runs[key] = {"label": label, "results": score(with_syn if use_synonyms else dataset, replies, **kw)}
    return {"runs": runs, "synonyms_kept": kept, "synonyms_rejected": rejected}


def render_matrix(dataset: Dataset, matrix: dict[str, Any], *, model_note: str, internal_only: bool = False) -> str:
    answerable = sum(c.expect == "answer" for c in dataset.cases)
    lines = [f"# End to end on DuckDB: {dataset.name}", ""]
    if internal_only:
        lines += ["**Internal use only:** the data's licence is non-commercial; do not publish these numbers or show them in a demo before the licence is checked.", ""]
    lines += [f"Contract: {'certified by the owner (tenant id rewritten in memory for the harness)' if dataset.certified_by_owner else '**an evaluation fixture, not certified by the owner** (lifecycle set to certified in a temporary copy only)'}. {dataset.note}",
              f"Model: {model_note}", "Answer keys: computed outside the product (plain Python over the source files, or an independent statement), never through the compiler.",
              f"{len(dataset.cases)} questions: {answerable} the contract can answer and {len(dataset.cases) - answerable} it should refuse (a refusal that names a missing definition is a gap, not a failure).", "",
              "## Results by setup", "", "| Setup | Correct (of answerable) | Wrong numbers | Wrongly answered | Correct refusals | False refusals |", "|---|---|---|---|---|---|"]
    for key, run in matrix["runs"].items():
        c = Counter(r["status"] for r in run["results"])
        lines.append(f"| **{key}** {run['label']} | {c.get('correct', 0)} of {answerable} | {c.get('wrong_answer', 0)} | {c.get('wrongly_answered', 0)} | {c.get('correct_refusal', 0)} | {c.get('false_refusal', 0)} |")
    kept, rejected = matrix["synonyms_kept"], matrix["synonyms_rejected"]
    lines += ["", f"Synonyms in B to E: {sum(len(v) for v in kept.values())} kept, {len(rejected)} rejected by the validator." + ("".join(f" Rejected: {i} / {w} ({c})." for i, w, c in rejected) if rejected else ""),
              "", "## Questions that were not answered correctly or refused correctly, by setup", "", "| Setup | ID | Question | Expected | Result | Detail |", "|---|---|---|---|---|---|"]
    for key, run in matrix["runs"].items():
        for r in run["results"]:
            if r["status"] not in ("correct", "correct_refusal"):
                lines.append(f"| {key} | {r['id']} | {r['question']} | {r['expect']} | {r['status']} | {str(r['detail'])[:80].replace('|', '/')} |")
    lines += ["", "## Every question under setup D", "", "| ID | Question | Expected | Result | Detail |", "|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['question']} | {r['expect']} | {r['status']} | {str(r['detail'])[:70].replace('|', '/')} |" for r in matrix["runs"]["D"]["results"]]
    lines += ["", "What this does not show: a real language model's choices (a Claude agent stood in), real users (the careful and habitual users are scripted), other contracts, or any warehouse other than DuckDB."]
    return "\n".join(lines) + "\n"
