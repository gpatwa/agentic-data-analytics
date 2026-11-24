"""The steps of the local deployment check, over HTTP, against the real routes, wiring and verifier.

`build_local_app` composes what `app/main.py` composes at start-up (the governed runtime, Connect on Databricks, the warm-up service) into a fresh app, with: a SQLite control
database, a temporary per-tenant registry copied from the certified synthetic-views contract, the REAL Entra verifier fed a locally generated key instead of Microsoft's (so no network
and no Entra), and a model that refuses to run (so no model call and no key is ever needed). The connector is the real Databricks one unless a fake is injected (tests).
`run_check` then drives it: nothing here reads a credential, prints one or records one. The client id and secret come from the caller as arguments and go only into custody."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from jose import jwt

from app.api_v2 import V2Runtime, build_v2_router
from app.config import Settings
from app.connect.api import MANAGER_GROUP
from app.connect.databricks_api import DatabricksRuntime, build_databricks_router, build_enrol_router
from app.connect.runtime_wiring import build_databricks_wiring, warm_up_for
from app.connect.store import ConnectionRecord
from app.runtime.pilot_wiring import build_pilot_runtime
from app.security.entra import EntraVerifier
from reference_stack import synthetic_views_contract as sv

TENANT = "synthetic"  # the certified contract is bound to this analytics tenant
ENTRA_TENANT = "00000000-0000-0000-0000-00000000c0de"
ISSUER = f"https://login.microsoftonline.com/{ENTRA_TENANT}/v2.0"
AUDIENCE = "api://local-deployment-check"
KID = "local-check-key"
CONNECTION = "local-check-1"
ROLE_MAP = '{"Analytics.Analyst": [["analytics"], ["analyst"]], "Analytics.Admin": [[], ["%s"]]}' % MANAGER_GROUP


class NoModel:
    """Any call fails at once: the check never reaches a model."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("no model in the local deployment check")


@dataclass
class LocalApp:
    app: FastAPI
    wiring: Any
    token: str
    dbx: Any = None  # the Databricks runtime the routes share (the enrol check sets its public address once the port is known)


def make_settings(work: Path) -> Settings:
    shutil.copytree(sv.CERTIFIED.parents[1], work / "tenants" / TENANT)
    return Settings(
        _env_file=None, ENV="dev", ANALYTICS_DB_URL=f"sqlite:///{work / 'data.db'}", ANALYTICS_CONTROL_DB_URL=f"sqlite:///{work / 'control.db'}",
        ANALYTICS_SEMANTIC_REGISTRY_PATH=str(sv.CERTIFIED.parents[1]), ANALYTICS_V2_ENABLED=True, ANALYTICS_V2_CONTRACTS=f"{sv.CONTRACT_ID}@{sv.VERSION}",
        ANALYTICS_CONNECT_DATABRICKS_ENABLED=True, ANALYTICS_CONNECT_REGISTRY_ROOT=str(work / "tenants"), ANALYTICS_TENANT_MAP=f'{{"{ENTRA_TENANT}": "{TENANT}"}}',
        ENTRA_TENANT_ID=ENTRA_TENANT, ENTRA_AUDIENCE=AUDIENCE, ANALYTICS_ROLE_MAP=ROLE_MAP)


def build_local_app(work: Path, *, connector: Any | None = None, enrol_url: str = "", token_ttl: int = 3600) -> LocalApp:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    settings = make_settings(work)
    verifier = EntraVerifier(issuer=ISSUER, audience=AUDIENCE, jwks_url="local://none", tenant_map={ENTRA_TENANT: TENANT},
                             role_map={"Analytics.Analyst": (["analytics"], ["analyst"]), "Analytics.Admin": ([], [MANAGER_GROUP])}, fetch=lambda _url: {KID: public})
    wiring = build_databricks_wiring(settings, **({"connector": connector} if connector is not None else {}))
    built = build_pilot_runtime(settings, verifier=verifier, model_factory=lambda _request_id: NoModel(), assets_for_tenant=wiring.assets_for_tenant)
    v2 = V2Runtime(service=built.service, verifier=built.verifier, feedback=built.feedback, resolver=built.resolver, briefs=built.briefs, warm=warm_up_for(wiring))
    dbx = DatabricksRuntime(setups=wiring.setups, session_factory=wiring.session_factory, verifier=built.verifier, resolver=built.resolver, enrol_url=enrol_url)
    app = FastAPI()
    app.include_router(build_v2_router(v2, lambda: None))
    app.include_router(build_databricks_router(dbx, lambda: None))
    app.include_router(build_enrol_router(dbx))  # the enrol file's own call (single-use token, no identity token)
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "exp": now + token_ttl, "iat": now, "sub": "local-check-admin", "oid": "local-check-admin", "tid": ENTRA_TENANT,
              "roles": ["Analytics.Analyst", "Analytics.Admin"], "preferred_username": "local-check-admin"}
    return LocalApp(app, wiring, jwt.encode(claims, private, algorithm="RS256", headers={"kid": KID}), dbx)


def register(local: LocalApp, *, host: str, http_path: str, catalog: str, schema: str, client_id: str, client_secret: str, warehouse: str) -> None:
    """The connection record and its secret, put straight into the product's own store and custody (the registration flow itself was checked on 2026-11-18). The secret goes
    only into custody; nothing returns or records it."""
    local.wiring.setups.secrets.put(TENANT, "databricks/local-check", client_secret)
    local.wiring.setups.store.add(ConnectionRecord(
        tenant_id=TENANT, connection_id=CONNECTION, engine="databricks", host=host, account=host, user=client_id, role="", warehouse=warehouse, database=catalog,
        default_schema=schema, allowed_tables=(f"{catalog}.{schema}.orders_current", f"{catalog}.{schema}.orders_usd_gross"), auth_method="oauth_m2m",
        secret_name="databricks/local-check", http_path=http_path, status="active"))


def _events(local: LocalApp, kind: str) -> list[Any]:
    return [e for e in local.wiring.setups.chain.events if e.event_type == kind]


def run_check(client: Any, local: LocalApp, *, wait_seconds: float = 90.0, sleep: Callable[[float], None] = time.sleep, connection: str = CONNECTION) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {local.token}"}
    base, dbx = "/api/v2/analytics", f"/api/v2/connect/databricks/connections/{connection}"
    steps: dict[str, Any] = {}

    def ran() -> list[Any]:
        return _events(local, "connect.warm_up_ran")

    r = client.post(f"{base}/warm", headers=headers)
    steps["switch_off"] = {"status": r.status_code, "code": r.json().get("code"), "warm_ups_recorded": len(ran())}
    r = client.put(f"{dbx}/warm-up", headers=headers, json={"enabled": True})
    steps["switch_on"] = {"status": r.status_code, "warm_up": r.json().get("warm_up"), "changed_events": len(_events(local, "connect.warm_up_changed"))}
    t0 = time.monotonic()
    r = client.post(f"{base}/warm", headers=headers)
    steps["warm"] = {"status": r.status_code, "code": r.json().get("code"), "answered_in_ms": round((time.monotonic() - t0) * 1000, 1)}
    waited = 0.0
    while not ran() and waited < wait_seconds:
        sleep(1.0)
        waited += 1.0
    done = ran()
    payload = getattr(done[0], "payload", {}) if done else {}
    steps["warm_up_recorded"] = {"events": len(done), "code": payload.get("code"), "statements_run": payload.get("statements_run"), "seconds": payload.get("seconds"),
                                 "keys": sorted(payload)}
    r = client.post(f"{base}/warm", headers=headers)
    steps["second_warm"] = {"status": r.status_code, "code": r.json().get("code")}
    before = len(ran())
    r = client.post(f"{base}/analyze", headers={**headers, "Idempotency-Key": "local-check-1"}, json={"request_text": "How many orders were there?", "purpose": "analytics"})
    sleep(1.0)
    steps["analyze_after_warm"] = {"status": r.status_code, "extra_warm_ups": len(ran()) - before}  # the answer is not the point (no model); a second warm-up must not follow
    ok = (steps["switch_off"]["code"] == "skipped_disabled" and steps["switch_off"]["warm_ups_recorded"] == 0 and steps["switch_on"]["warm_up"] is True
          and steps["switch_on"]["changed_events"] == 1 and steps["warm"]["code"] == "warming" and steps["warm"]["status"] == 202
          and steps["warm_up_recorded"]["code"] == "warmed" and (steps["warm_up_recorded"]["statements_run"] or 0) >= 1
          and steps["warm_up_recorded"]["keys"] == ["code", "connection_id", "seconds", "statements_run"]
          and steps["second_warm"]["code"] == "skipped_recent" and steps["analyze_after_warm"]["extra_warm_ups"] == 0)
    return {"steps": steps, "wired_path_ok": ok, "verdict": "wired path behaved as designed over HTTP" if ok else "wired path did NOT behave as designed: read the steps"}
