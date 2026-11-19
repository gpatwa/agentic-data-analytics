"""C2 and C4 of the certification plan (docs/design/CONNECT_CERTIFICATION_PLAN.md): draft on the sandbox, then ask a question.

A dev tool, never imported by the app. Both stages first stand the sandbox up the way the product does (the owner's signed-in
admin runs the generated registration script, the product's custody step stores the principal's secret, the connection test reads
the tables), then:

- `draft` (C2): read the schema and a redacted profile **through the connection's own session**, draft with the model, write the
  draft for the owner to review. Then tear everything down. The draft is **not certified**; the owner certifies it in Git (C3).
- `ask` (C4): assemble the tenant's assets from the **certified** contract the owner placed in `--registry`, compile one metric for
  Databricks, run it through the connection's gateway, compare the answer with the sandbox's known data, and exercise the failure
  cases (a query outside the allowlist, a suspended connection, a rotated secret, removal). Then tear everything down.

Both use the same fixed `--suffix`, so the sandbox catalog name, and therefore the contract's physical names, are the same in both
runs. Secrets live in memory only. Not run against a workspace until the owner approves the live gates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.connect import databricks_registration as reg
from app.connect.databricks_api import DatabricksSetups, make_session_factory
from app.connect.e2e import databricks_e2e as base
from app.connect.e2e import databricks_spike as sp
from app.connect.e2e.leak import LeakScanner
from app.connect.gateway import ConnectionUnavailable
from app.connect.records import SetupRecorder
from app.connect.store import ConnectionStore, TenantSecretStore
from app.connect.tenant_assets import connected_assets_resolver
from app.execution import ExecutionLimits, QueryRejected
from app.ontology_draft.databricks_source import (
    databricks_explain_probe,
    infer_keys,
    introspect_session,
    profile_session,
    qualified_prefix,
)
from app.ontology_draft.draft_run import (
    REQUIRED as _MODEL_ENV_UNUSED,  # noqa: F401 - documents where the env names come from
)
from app.ontology_draft.draft_run import stop_reasons
from app.ontology_draft.drafter import PROMPT_VERSION
from app.ontology_draft.emit import write_draft
from app.ontology_draft.evaluation import EvalCase, run_case_full
from app.ontology_draft.proposal import DraftError
from app.ontology_draft.validate import DraftContext
from app.security.audit import AuditChain
from app.semantic_registry import SemanticRegistry
from packages.platform_contracts.analytics_intent import AnalyticalIntent, IntentMetric, SemanticContractReference

TENANT = "sandbox"
CONTRACT_ID = "sandbox-databricks"
MODEL_ENV = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")
EXPECTED = {("sum", "amount"): 3255, ("count", "id"): sp.ROWS, ("count_distinct", "id"): sp.ROWS, ("max", "amount"): 210, ("min", "amount"): 7}


@dataclass
class Certify:
    admin: Any
    warehouse_id: str
    warehouse_name: str
    host: str
    names: sp.Names
    session_factory_for: Any  # secrets -> factory(record) -> session (the product's own `make_session_factory` live)
    model: Any = None
    out_dir: Path = Path("semantic_registry_drafts")
    registry: Path | None = None
    dns: Any = None  # a resolver for the host check; None is the system resolver
    rotate: bool = True  # C4: the owner makes a second secret and the product's rotation step swaps it in
    scanner: LeakScanner = field(default_factory=LeakScanner)
    outcomes: list[dict[str, str]] = field(default_factory=list)
    stopped: str = ""

    def __post_init__(self) -> None:
        self.e2e = base.E2E(self.admin, lambda a, s: [], self.warehouse_id, self.warehouse_name, self.names, scanner=self.scanner)
        self.store, self.secrets, self.chain = ConnectionStore(), TenantSecretStore(), AuditChain()
        self.setups = DatabricksSetups(self.store, self.secrets, self.chain)
        self.factory = self.session_factory_for(self.secrets)

    def note(self, step: str, result: str, detail: str = "") -> None:
        self.outcomes.append({"step": step, "result": result, "detail": detail})

    # ---- the sandbox, as the product makes it --------------------------------------------------------------------------------

    def stand_up(self) -> Any:
        self.e2e.preflight()
        self.e2e.fixture()
        self.e2e.run_script()
        pending = self.setups.begin(tenant_id=TENANT, admin_id="owner", host=self.host, catalog=self.names.catalog,
                                    tables=[(sp.SCHEMA, t) for t in sp.INSIDE], warehouse_name=self.warehouse_name,
                                    **({"resolver": self.dns} if self.dns else {}))
        done = self.setups.complete(pending, application_id=self.e2e._app, client_secret=self.e2e._secret,
                                    http_path=f"/sql/1.0/warehouses/{self.warehouse_id}", session_factory=self.factory)
        self.note("connection_active", "allowed" if done.ok else "problem:connection_test_failed", f"tables={len(done.checks)}")
        if not done.ok:
            raise sp.SpikeStop("connection_test_failed")
        return self.store.list(TENANT)[0]

    # ---- C2 ---------------------------------------------------------------------------------------------------------------------

    def draft(self, record: Any) -> None:
        if self.model is None:
            raise sp.SpikeStop("model_not_configured")
        session = self.factory(record)
        try:
            intro = introspect_session(session, catalog=self.names.catalog, tables=[(sp.SCHEMA, t) for t in sp.INSIDE[:1]])
            profile = profile_session(session, intro.facts, catalog=self.names.catalog, schema=sp.SCHEMA)
        finally:
            session.close()
        facts = infer_keys(intro.facts, profile)
        self.note("introspect_profile", "allowed", f"tables={len(facts.tables)} keys_inferred={sum(1 for t in facts.tables if t.primary_key)}")
        ctx = DraftContext(tenant_id=TENANT, domain="sandbox", contract_id=CONTRACT_ID, owner_id="team.semantic-owners", owner_name="Semantic owners",
                           model_id=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "model"), prompt_version=PROMPT_VERSION, dialect="databricks",
                           physical_prefix=qualified_prefix(self.names.catalog, sp.SCHEMA))
        probe = databricks_explain_probe(lambda: self.factory(record))
        report, result = run_case_full(EvalCase(CONTRACT_ID, facts, profile), self.model, ctx, explain=probe)
        reasons = stop_reasons(report, remote=True)
        if reasons:
            raise sp.SpikeStop("stop_rule:" + ",".join(reasons))
        if result is None or result.document is None:
            self.note("draft", f"problem:no_draft:{report.error or 'nothing_kept'}", f"drops={dict(report.drops)}")
            raise sp.SpikeStop("no_draft")
        where = write_draft(result, self.out_dir, report=report, skipped_columns=intro.skipped,
                            source_note=f"Databricks sandbox catalog `{self.names.catalog}`, schema `{sp.SCHEMA}` (synthetic data, via the connection's own principal)")
        self.scanner.scan("draft_files", "\n".join(p.read_text() for p in where.glob("*") if p.is_file()))
        self.note("draft", "allowed", f"metrics={report.kept_metrics} dimensions={report.kept_dimensions} drops={dict(report.drops)} leaks={report.leaks} "
                                      f"escapes={len(report.escapes)} injections={report.injections} dir={where.name}")
        self.draft_dir = where

    # ---- C4 ---------------------------------------------------------------------------------------------------------------------

    def ask(self, record: Any) -> None:
        if self.registry is None:
            raise sp.SpikeStop("registry_not_given")
        resolve = connected_assets_resolver(self.store, self.secrets, lambda t: self.registry)
        assets = resolve(TENANT)
        self.note("assets_assembled", "allowed", "dialect=databricks")
        document = next(e.document for e in SemanticRegistry(self.registry).list_entries() if e.document is not None and e.document.lifecycle == "certified")
        contract = document.contract
        metric = contract.metrics[0]
        fields = {f.id: f for f in contract.fields}
        measure = fields[metric.measure_field_id].physical_name if metric.measure_field_id else ""
        intent = AnalyticalIntent(query_id="c4", tenant_id=TENANT, dataset_id=metric.dataset_id,
                                  semantic_contract=SemanticContractReference(contract_id=contract.id, contract_version=contract.version),
                                  metrics=[IntentMetric(metric_id=metric.id)], limit=10)
        plan = assets.compiler.compile(intent)
        limits = ExecutionLimits(timeout_seconds=120, max_rows=100, max_cost_units=1_000.0)
        result = assets.gateway.execute(plan, limits=limits)
        value = result.rows[0][-1] if result.rows else None
        expected = EXPECTED.get((metric.aggregation, measure))
        if expected is None:
            self.note("answer", "recorded", f"metric={metric.id} value={value} not_independently_checked=true")
        else:
            self.note("answer", "allowed" if value is not None and float(value) == float(expected) else "problem:wrong_answer",
                      f"metric={metric.id} value={value} expected={expected}")
        outside = type("P", (), {"dialect": "databricks", "sql": f"SELECT * FROM {self.names.catalog}.{sp.SCHEMA}.{sp.OUTSIDE}", "parameters": {}})()
        try:
            assets.gateway.execute(outside, limits=limits)
            self.note("outside_allowlist", "problem:allowed")
        except QueryRejected:
            self.note("outside_allowlist", "denied")
        self.setups.health_check(TENANT, record.connection_id, actor_id="owner", session_factory=self.factory)
        self.store.set_status(TENANT, record.connection_id, "suspended")
        try:
            resolve(TENANT)
            self.note("suspended_connection", "problem:still_served")
        except ConnectionUnavailable as exc:
            self.note("suspended_connection", "denied", exc.code)
        if self.rotate:
            fresh = self.fresh_secret()
            self.scanner.watch("rotated_secret", fresh)
            rotated = self.setups.rotate(TENANT, record.connection_id, actor_id="owner", client_secret=fresh, session_factory=self.factory)
            self.note("rotation", "allowed" if rotated.ok else "problem:rotation_failed", f"status={rotated.connection_status}")
            again = resolve(TENANT).gateway.execute(plan, limits=limits)
            self.note("answer_after_rotation", "allowed" if again.rows and again.rows[0][-1] == value else "problem:different", "")
        else:
            self.store.set_status(TENANT, record.connection_id, "active")

    def remove_connection(self, record: Any) -> None:
        recorder_removed = self.secrets.exists(TENANT, record.secret_name)
        self.setups._remove_credential(record, SetupRecorder(self.chain, tenant_id=TENANT, connection_id=record.connection_id, admin_id="owner"))
        self.store.set_status(TENANT, record.connection_id, "removed")
        self.note("connection_removed", "allowed" if recorder_removed and not self.secrets.exists(TENANT, record.secret_name) else "problem:secret_remains")

    def fresh_secret(self) -> str:
        """The owner, as admin, makes a second secret for the same principal (what an admin does to rotate); recorded for teardown."""
        made = self.admin.service_principal_secrets_proxy.create(service_principal_id=self.e2e._pid, lifetime=reg.SECRET_LIFETIME)
        self.e2e.created.append(("secret", f"{self.e2e._pid}:{made.id}"))
        return str(made.secret)

    # ---- the run -----------------------------------------------------------------------------------------------------------------

    def run(self, stage: str) -> dict[str, object]:
        record = None
        try:
            record = self.stand_up()
            getattr(self, stage)(record)  # "draft" (C2), "ask" (C4), "govern" (C5, `databricks_govern.Govern`)
        except sp.SpikeStop as stop:
            self.stopped = stop.code
        except DraftError as exc:
            self.stopped = f"draft_error:{exc.code}"
        except ConnectionUnavailable as exc:
            self.stopped = f"unavailable:{exc.code}"
        except Exception as exc:  # noqa: BLE001 - type only
            self.stopped = f"unexpected:{type(exc).__name__}"[:60]
        finally:
            if record is not None:
                try:
                    self.remove_connection(record)
                except Exception:  # noqa: BLE001
                    self.note("connection_removed", "problem:failed")
            self.e2e.remove()
            self.e2e.do_sweep()
        result: dict[str, object] = {"stage": stage, "outcomes": self.outcomes, "stopped": self.stopped, "teardown": self.e2e.teardown, "sweep": self.e2e.sweep,
                                     "audit_chain_verifies": self.chain.verify()}
        self.scanner.scan("report", json.dumps(result))
        result["leaks"] = self.scanner.summary()
        return result


def render_markdown(suffix: str = "a1b2c3d4", warehouse: str = "Serverless Starter Warehouse") -> str:
    """What the reviewer reads. A test keeps `docs/design/CONNECT_CERTIFICATION_STATEMENTS.md` equal to this."""
    n = sp.Names.for_run(suffix)
    c, t = f"`{n.catalog}`", sp.INSIDE[0]
    blocks = [
        "# Certification stages C2 and C4: the exact statements and calls (generated)\n\n"
        "Generated by `services/analytics-api/app/connect/e2e/databricks_certify.py`; a test keeps this file equal to the generator. "
        f"`{suffix}` stands for the run suffix, **the same in both stages**, so the sandbox catalog name and the contract's physical names match. "
        f"The warehouse is `{warehouse}`, chosen by name and never edited. **Nothing here has been run.** Plan: `docs/design/CONNECT_CERTIFICATION_PLAN.md`. "
        "Both stages begin with the registration the previous check ran (`docs/design/CONNECT_DATABRICKS_REGISTRATION_STATEMENTS.md`: the sandbox catalog and three "
        "synthetic tables as the signed-in owner, the generated script, the product's custody step and its table-read connection test) and both end with that "
        "check's removal and a zero-leftover sweep. The principal's secret and the model key stay in memory.\n",
        "### C2, stage `draft`: read through the connection's own principal (read only)\n\n```sql\n"
        f"DESCRIBE TABLE EXTENDED {c}.`{sp.SCHEMA}`.`{t}`;\n"
        f"SELECT count(*), count(w.`<col>`), count(DISTINCT w.`<col>`), min(w.`<col>`), max(w.`<col>`) FROM (SELECT * FROM {c}.`{sp.SCHEMA}`.`{t}` LIMIT <row limit>) w;"
        "  -- one query per table, ranges only for numeric and date columns\n"
        f"SELECT `<col>`, count(*) AS n FROM (SELECT `<col>` FROM {c}.`{sp.SCHEMA}`.`{t}` LIMIT <row limit>) w WHERE `<col>` IS NOT NULL GROUP BY `<col>` ORDER BY n DESC, `<col>` LIMIT 51;"
        "  -- only for low-cardinality, non-sensitive text columns; at most 5 samples, each in at least the cohort floor of rows, redacted\n"
        "EXPLAIN <each compiled candidate metric and dimension>;  -- never run, one per candidate\n```\n",
        "### C2, the model call\n\n```text\nOne structured-output call to the pilot's Azure OpenAI deployment (as in the earlier runs), with only the redacted view: table and column names, "
        "types, screened comments, aggregate counts and ranges, at most five cohort-safe samples. The model never writes SQL and never sees a credential. "
        "The draft is written to a drafts folder that is NOT the registry, as `contract.draft.json`, `ontology.candidate.json` and `REVIEW.md`.\n```\n",
        "### C3: yours\n\n```text\nYou review the draft and certify it in Git by placing a contract with lifecycle `certified` and a tenant of `sandbox` in the registry folder. "
        "Confirm the grain: the key was inferred from a fully-seen table, not declared. I do not certify, and the run refuses anything that is not certified.\n```\n",
        "### C4, stage `ask`: assemble, compile, run, then the failure cases\n\n```text\n"
        "1. connected_assets_resolver(store, secrets, registry): the certified contract must be inside the connection's allowlist, for tenant `sandbox`\n"
        "2. compile one metric of the certified contract for Databricks (no model, no LLM intent step)\n"
        f"3. gateway.execute(plan) on the connection: the compiled statement reads only {c}.`{sp.SCHEMA}`.`{t}`; the answer is compared with the sandbox's known data\n"
        f"4. gateway.execute(SELECT * FROM {n.catalog}.{sp.SCHEMA}.{sp.OUTSIDE}) must be REFUSED before it reaches Databricks\n"
        "5. health check (reads each named table), then the connection is suspended: the resolver must refuse it\n"
        "6. as the owner, make a second secret for the same principal; the product's rotation swaps it in after the table reads pass; the same question answers again\n"
        "7. remove the connection: the stored credential is deleted\n```\n",
        "### Not covered by C4\n\n```text\nThe model's intent extraction and the full governed graph (control database, review, evidence) are not exercised: the intent is built by hand "
        "from the certified contract. C4 shows the connected-warehouse half of a governed question, not the whole pipeline.\n```\n",
    ]
    return "\n".join(blocks)


def main(argv: list[str] | None = None, *, sdk: Any | None = None, model: Any | None = None) -> int:
    p = argparse.ArgumentParser(description="Databricks certification stages C2 (draft) and C4 (ask), owner-approved, throwaway.")
    p.add_argument("--stage", choices=("draft", "ask", "govern"), required=True)
    p.add_argument("--allow-live", action="store_true")
    p.add_argument("--expect-host", required=True)
    p.add_argument("--suffix", required=True)
    p.add_argument("--warehouse", required=True)
    p.add_argument("--registry", default=None, help="C4: the folder holding the certified contract the owner placed there")
    p.add_argument("--out-dir", default=None, help="C2: where the draft is written (not the registry)")
    p.add_argument("--report", default=None)
    args = p.parse_args(argv)
    if not args.allow_live:
        print("refused: pass --allow-live (only after the owner approved the gate)", file=sys.stderr)
        return 2
    if not sp._HOST.match(args.expect_host) or not sp._SUFFIX.match(args.suffix) or not args.warehouse.strip():
        print("refused: check --expect-host, --suffix and --warehouse", file=sys.stderr)
        return 2
    if args.stage in ("ask", "govern") and not args.registry:
        print("refused: --registry is required for the ask stage", file=sys.stderr)
        return 2
    if args.stage in ("draft", "govern") and model is None:
        missing = [n for n in MODEL_ENV if not os.environ.get(n)]
        if missing:
            print("Not run. Set these environment variables (names only shown): " + ", ".join(missing), file=sys.stderr)
            return 2
        from app.llm_structured import OpenAIStructuredClient

        model = OpenAIStructuredClient.azure(endpoint=os.environ["AZURE_OPENAI_ENDPOINT"], api_key=os.environ["AZURE_OPENAI_API_KEY"],
                                             api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01"), deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
                                             timeout_seconds=120.0, reasoning_effort=os.environ.get("AZURE_OPENAI_REASONING_EFFORT") or None)
    admin = sp.connect_admin(args.expect_host, sdk=sdk)
    wid = sp.pick_warehouse(admin, args.warehouse)
    names = sp.Names.for_run(args.suffix)
    common = dict(model=model, out_dir=Path(args.out_dir) if args.out_dir else Path("semantic_registry_drafts"), registry=Path(args.registry) if args.registry else None)
    if args.stage == "govern":
        from app.connect.e2e.databricks_govern import Govern

        run: Certify = Govern(admin, wid, args.warehouse, args.expect_host, names, lambda secrets: make_session_factory(secrets), model_factory=lambda _rid: model,
                              **common)
    else:
        run = Certify(admin, wid, args.warehouse, args.expect_host, names, lambda secrets: make_session_factory(secrets), **common)
    if model is not None:
        run.scanner.watch("model_api_key", os.environ.get("AZURE_OPENAI_API_KEY", ""))
    result = run.run(args.stage)
    if args.report:
        Path(args.report).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ("stage", "stopped", "teardown", "sweep")} | {"leaks_clean": result["leaks"]["clean"]}))  # type: ignore[index]
    ok = not result["stopped"] and not result["teardown"]["left"] and result["leaks"]["clean"]  # type: ignore[index]
    return 0 if ok else 1


__all__ = ["Certify", "main"]

if __name__ == "__main__":
    sys.exit(main())
