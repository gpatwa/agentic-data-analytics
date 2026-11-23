"""The enrol file: the admin runs ONE generated file under their own Databricks sign-in and nothing is copied or pasted (docs/design/SELF_SERVE_CONNECT_DESIGN.md, layer 2b, track 1).

What this module holds, all in memory like the setups it belongs to (a restart loses a pending enrolment; the admin starts again):
- `EnrolmentTokens`: a bearer token the generated file carries. Single use, 30 minutes, bound to the tenant, the setup and the file's digest; only its hash is kept; a new token for
  the same setup revokes the old one. It can submit one credential for that one setup and do nothing else.
- `AttemptLimiter`: failed redemptions per caller, so a guessed token is not worth trying.
- `render_enrol_file`: the plain, readable Python the admin runs. It creates the principal, its secret and the read grants with the admin's own sign-in (Compass never holds an admin
  credential), reads the grants back, posts the new principal's id and secret straight to Compass over TLS, and removes everything it created if any step fails. It prints only codes.
  `--digest` prints the file's digest (the part that varies per admin, the address and the token, is left out), which the screen shows, so a reviewer can read the file first.

The secret reaches Compass over TLS exactly as it does today when pasted; what changes is that no person copies it, sees it, or leaves it in a clipboard. Not run against a workspace:
tests execute the generated file with a fake SDK and a fake network."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.connect import databricks_registration as reg
from app.connect.grants import ConnectScope

TOKEN_TTL_SECONDS = 1800.0
TOKEN_PREFIX = "cenrol_"
MAX_FAILURES, FAILURE_WINDOW_SECONDS = 10, 300.0
VARIABLE_MARK = "# @@VARIABLE@@"  # the lines that differ per admin; the digest ignores them
APP_TOKEN = "@APP@"


class EnrolRefused(Exception):
    """A short stable code, never a message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _Entry:
    tenant_id: str
    setup_id: str
    digest: str
    expires_at: float
    used: bool = False


class EnrolmentTokens:
    def __init__(self, clock: Callable[[], float] = time.time, ttl: float = TOKEN_TTL_SECONDS) -> None:
        self._clock, self._ttl = clock, ttl
        self._by_hash: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def mint(self, *, tenant_id: str, setup_id: str, digest: str) -> tuple[str, float]:
        """A new token; any earlier token of the same setup stops working."""
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        entry = _Entry(tenant_id, setup_id, digest, self._clock() + self._ttl)
        with self._lock:
            for key in [k for k, e in self._by_hash.items() if e.setup_id == setup_id or e.expires_at < self._clock()]:
                del self._by_hash[key]
            self._by_hash[self._hash(token)] = entry
        return token, entry.expires_at

    def redeem(self, token: str) -> _Entry:
        """Single use: the first valid call spends the token, whatever happens next."""
        if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) or len(token) > 200:
            raise EnrolRefused("token_invalid")
        with self._lock:
            entry = self._by_hash.get(self._hash(token))
            if entry is None:
                raise EnrolRefused("token_invalid")
            if entry.used:
                raise EnrolRefused("token_used")
            if entry.expires_at < self._clock():
                raise EnrolRefused("token_expired")
            entry.used = True
            return entry


class AttemptLimiter:
    def __init__(self, clock: Callable[[], float] = time.time, maximum: int = MAX_FAILURES, window: float = FAILURE_WINDOW_SECONDS) -> None:
        self._clock, self._max, self._window = clock, maximum, window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        cutoff = self._clock() - self._window
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        self._failures[key] = recent
        return recent

    def check(self, key: str) -> None:
        with self._lock:
            if len(self._recent(key)) >= self._max:
                raise EnrolRefused("too_many_attempts")

    def failed(self, key: str) -> None:
        with self._lock:
            self._recent(key).append(self._clock())


# ---- the generated file ---------------------------------------------------------------------------------------------------------

_TEMPLATE = r'''#!/usr/bin/env python3
"""Compass enrol file: connects one Databricks workspace to Compass with a read-only service principal.

Read it before you run it. What it does, in order, under YOUR Databricks sign-in (Compass never holds an admin credential):
  1. checks the workspace is the one named below and finds the SQL warehouse by name (it never changes the warehouse's settings);
  2. creates one service principal and one OAuth secret, held only in this process;
  3. gives the principal CAN_USE on the warehouse and USE CATALOG, USE SCHEMA and SELECT on exactly the tables listed below, nothing else;
  4. reads the grants back and sends the principal's id, secret and the read-back to Compass over TLS (nothing is printed or written to disk);
  5. if any step fails, removes everything this run created.
`python compass-enrol.py --digest` prints this file's digest (the two lines marked below are left out); it must equal the digest Compass showed you.
`python compass-enrol.py --remove <application id>` revokes the grants and deletes the principal later.
Options: --profile <databricks CLI profile>   (otherwise the SDK's default sign-in is used).
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request

PLAN = json.loads(r"""@@PLAN@@""")
ENROL_URL = "@@URL@@"  # @@VARIABLE@@
TOKEN = "@@TOKEN@@"  # @@VARIABLE@@
SAFE_SQL = re.compile(r"^(GRANT|REVOKE) (USE CATALOG|USE SCHEMA|SELECT) |^SHOW GRANTS ", re.IGNORECASE)
FORBIDDEN_SQL = re.compile(r"\b(WITH GRANT OPTION|ALL PRIVILEGES|MANAGE|OWNERSHIP|MODIFY|METASTORE|EXTERNAL LOCATION|STORAGE CREDENTIAL)\b", re.IGNORECASE)


MARK = "@@" + "VARIABLE@@"


def digest_of_this_file():
    lines = open(__file__, encoding="utf-8").read().split("\n")
    return hashlib.sha256("\n".join("<variable>" if MARK in ln else ln for ln in lines).encode()).hexdigest()[:16]


class Stop(Exception):
    pass


def run_sql(client, warehouse_id, statement):
    if not SAFE_SQL.match(statement) or FORBIDDEN_SQL.search(statement) or "`" + PLAN["catalog"] + "`" not in statement:
        raise Stop("statement_not_allowed")
    result = client.statement_execution.execute_statement(statement=statement, warehouse_id=warehouse_id, wait_timeout="50s")
    state = getattr(getattr(result.status, "state", None), "name", str(result.status.state))
    if state != "SUCCEEDED":
        raise Stop("sql_" + state.lower()[:20])
    return [list(row) for row in (getattr(getattr(result, "result", None), "data_array", None) or [])]


def post(body):
    request = urllib.request.Request(ENROL_URL, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        try:
            return {"ok": False, "code": json.loads(error.read().decode()).get("detail", "http_" + str(error.code))}
        except Exception:
            return {"ok": False, "code": "http_" + str(error.code)}


def find_warehouse(client):
    found = [w for w in client.warehouses.list() if getattr(w, "name", None) == PLAN["warehouse"]]
    if len(found) != 1:
        raise Stop("warehouse_not_found" if not found else "warehouse_name_ambiguous")
    return str(found[0].id)


def acl_for(app_id):
    try:
        from databricks.sdk.service import sql
        return sql.WarehouseAccessControlRequest(service_principal_name=app_id, permission_level=sql.WarehousePermissionLevel.CAN_USE)
    except ImportError:
        class Entry:
            service_principal_name, permission_level = app_id, "CAN_USE"
        return Entry()


def open_client(profile):
    import databricks.sdk as sdk
    client = sdk.WorkspaceClient(profile=profile) if profile else sdk.WorkspaceClient()
    if PLAN["host"] not in str(getattr(getattr(client, "config", None), "host", "")):
        raise Stop("workspace_is_not_the_one_named")
    return client


def revoke_all(client, warehouse_id, app_id):
    left = []
    for template in PLAN["revoke"]:
        try:
            run_sql(client, warehouse_id, template.replace("@APP@", app_id))
        except Exception:
            left.append("grants")
    return left


def enrol(profile):
    client = open_client(profile)
    warehouse_id = find_warehouse(client)
    created = {"principal": None, "secret": None, "app": None}
    try:
        principal = client.service_principals.create(display_name=PLAN["principal"])
        created["principal"], created["app"] = str(principal.id), str(principal.application_id)
        secret = client.service_principal_secrets_proxy.create(service_principal_id=created["principal"], lifetime=PLAN["secret_lifetime"])
        created["secret"] = str(secret.id)
        client.warehouses.update_permissions(warehouse_id, access_control_list=[acl_for(created["app"])])
        for template in PLAN["grant"]:
            run_sql(client, warehouse_id, template.replace("@APP@", created["app"]))
        rows = []
        for template in PLAN["show"]:
            for row in run_sql(client, warehouse_id, template.replace("@APP@", created["app"])):
                if str(row[0]).replace("`", "").lower() == created["app"].lower():
                    rows.append([str(row[1]), str(row[2]), str(row[3])])
        acl = client.warehouses.get_permissions(warehouse_id).access_control_list or []
        pairs = [[str(getattr(e, "service_principal_name", "")), str(getattr(getattr(p, "permission_level", ""), "value", getattr(p, "permission_level", "")))]
                 for e in acl for p in (getattr(e, "all_permissions", None) or [])]
        answer = post({"application_id": created["app"], "client_secret": str(secret.secret), "http_path": "/sql/1.0/warehouses/" + warehouse_id,
                       "grants": rows, "warehouse_acl": pairs})
        if not answer.get("ok"):
            raise Stop("compass_refused:" + str(answer.get("code", "unknown"))[:40])
        return created["app"]
    except Exception as error:
        left = []
        if created["app"]:
            left += revoke_all(client, warehouse_id, created["app"])
        for kind, call in (("secret", lambda: client.service_principal_secrets_proxy.delete(created["principal"], created["secret"]) if created["secret"] else None),
                           ("principal", lambda: client.service_principals.delete(created["principal"]) if created["principal"] else None)):
            try:
                call()
            except Exception:
                left.append(kind)
        code = error.args[0] if isinstance(error, Stop) else "unexpected_" + type(error).__name__[:40]
        print("not enrolled: " + str(code) + ("; left behind: " + ", ".join(left) if left else "; nothing left behind"))
        raise SystemExit(1)


def remove(profile, app_id):
    client = open_client(profile)
    warehouse_id = find_warehouse(client)
    left = revoke_all(client, warehouse_id, app_id)
    matches = [p for p in client.service_principals.list() if str(getattr(p, "application_id", "")) == app_id and str(getattr(p, "display_name", "")).startswith("compass-conn-")]
    if len(matches) != 1:
        left.append("principal_not_found")
    else:
        try:
            client.service_principals.delete(matches[0].id)
        except Exception:
            left.append("principal")
    print("removed" if not left else "not everything was removed: " + ", ".join(left))
    raise SystemExit(0 if not left else 1)


def main():
    parser = argparse.ArgumentParser(description="Compass enrol file")
    parser.add_argument("--profile")
    parser.add_argument("--digest", action="store_true")
    parser.add_argument("--remove", metavar="APPLICATION_ID")
    args = parser.parse_args()
    if args.digest:
        print(digest_of_this_file())
        return
    if args.remove:
        remove(args.profile, args.remove)
        return
    try:
        app_id = enrol(args.profile)
    except Stop as stop:
        print("not enrolled: " + str(stop))
        raise SystemExit(1)
    print("enrolled: Compass is connected. Principal application id " + app_id + " (keep it for --remove). Nothing was written to disk.")


if __name__ == "__main__":
    main()
'''


def _plan(ceiling: ConnectScope, *, suffix: str, warehouse_name: str, host: str) -> dict:
    script = reg.generate(ceiling, suffix=suffix, warehouse_name=warehouse_name, application_id=APP_TOKEN)
    show = [f"SHOW GRANTS `{APP_TOKEN}` ON CATALOG {reg.bt(ceiling.database)}",
            *(f"SHOW GRANTS `{APP_TOKEN}` ON SCHEMA {reg.bt(ceiling.database)}.{reg.bt(s)}" for s in ceiling.schemas),
            *(f"SHOW GRANTS `{APP_TOKEN}` ON TABLE {reg.bt(ceiling.database)}.{reg.bt(s)}.{reg.bt(t)}" for s, t in sorted(ceiling.tables))]
    return {"host": host, "catalog": ceiling.database, "warehouse": warehouse_name.strip(), "principal": script.principal, "secret_lifetime": reg.SECRET_LIFETIME,
            "tables": [f"{s}.{t}" for s, t in sorted(ceiling.tables)], "script_digest": script.digest,
            "grant": [s.arg for s in script.steps if s.op == "sql"], "revoke": [s.arg for s in script.teardown if s.op == "sql"], "show": show}


def normalized_digest(source: str) -> str:
    """The digest the file prints for itself: the lines marked as varying per admin (the address and the token) are left out."""
    lines = source.split("\n")
    return hashlib.sha256("\n".join("<variable>" if "@@VARIABLE@@" in ln else ln for ln in lines).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class EnrolFile:
    filename: str
    content: str
    digest: str


def render_enrol_file(ceiling: ConnectScope, *, suffix: str, warehouse_name: str, host: str, enrol_url: str, token: str) -> EnrolFile:
    if not re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?$", enrol_url or "") and not re.match(r"^http://(localhost|127\.0\.0\.1)(:\d+)?(/[A-Za-z0-9._~/-]*)?$", enrol_url or ""):
        raise EnrolRefused("enrol_url_invalid")
    if not re.match(r"^cenrol_[A-Za-z0-9_-]{20,}$", token or ""):
        raise EnrolRefused("token_invalid")
    plan = json.dumps(_plan(ceiling, suffix=suffix, warehouse_name=warehouse_name, host=host), indent=1, sort_keys=True)
    if '"""' in plan:
        raise EnrolRefused("plan_not_representable")
    body = _TEMPLATE.replace("@@PLAN@@", plan)
    canonical = body.replace("@@URL@@", "<url>").replace("@@TOKEN@@", "<token>")
    digest = normalized_digest(canonical)
    return EnrolFile("compass-enrol.py", body.replace("@@URL@@", enrol_url).replace("@@TOKEN@@", token), digest)


# ---- the endpoint's logic ------------------------------------------------------------------------------------------------------------


def check_readback(pending, application_id: str, grants: object, acl: object) -> reg.Report:
    """The product's verifier on the read-back the admin's file sent. Compass does not rely on it alone: the connection test that follows reads every named table as the principal."""
    if not isinstance(grants, list) or not isinstance(acl, list) or len(grants) > 500 or len(acl) > 500:
        raise EnrolRefused("invalid_request")
    try:
        rows = [{"actionType": str(r[0]), "objectType": str(r[1]), "objectKey": str(r[2])} for r in grants]
        pairs = [(str(r[0]), str(r[1])) for r in acl]
    except (IndexError, TypeError, KeyError):
        raise EnrolRefused("invalid_request") from None
    return reg.verify_registration(rows, pairs, pending.scope, application_id)


__all__ = [
    "APP_TOKEN", "AttemptLimiter", "EnrolFile", "EnrolRefused", "EnrolmentTokens", "TOKEN_TTL_SECONDS", "check_readback", "normalized_digest", "render_enrol_file",
]
