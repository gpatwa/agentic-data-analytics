"""A Databricks session that drives the owner's own `databricks` command line.

The program never sees a credential: the command line reads its own profile, so nothing here reads, stores or prints a token. Every call is a
`subprocess` with an argument list (no shell). A wall-clock deadline stands in for the warehouse-time cap, since warehouse time cannot exceed it.
Error messages carry the failing state and the engine's own error code, never the statement text."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

Runner = Callable[[list[str]], tuple[int, str]]

POLL_SECONDS = 2.0


class CapExceeded(RuntimeError):
    pass


class EngineError(RuntimeError):
    pass


def _run(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=180, check=False)  # noqa: S603
    return proc.returncode, proc.stdout


class DatabricksCliSession:
    def __init__(
        self,
        profile: str,
        warehouse_id: str,
        cap_seconds: float,
        *,
        runner: Runner = _run,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_statements: int | None = None,
    ) -> None:
        self.profile = profile
        self.warehouse_id = warehouse_id
        self._runner = runner
        self._clock = clock
        self._sleep = sleep
        self._deadline = clock() + cap_seconds
        self.statements = 0
        self._max_statements = max_statements

    def _check_cap(self) -> None:
        if self._clock() > self._deadline:
            raise CapExceeded("warehouse_time_cap_reached")

    def _api(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        self._check_cap()
        argv = ["databricks", "api", method, path, "-p", self.profile]
        tmp: str | None = None
        if body is not None:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                json.dump(body, fh)
                tmp = fh.name
            argv += ["--json", f"@{tmp}"]
        try:
            code, out = self._runner(argv)
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)
        if code != 0:
            raise EngineError("cli_call_failed")
        try:
            return json.loads(out)
        except ValueError as exc:
            raise EngineError("cli_output_not_json") from exc

    def _execute(self, sql: str) -> dict[str, Any]:
        if self._max_statements is not None and self.statements >= self._max_statements:
            raise CapExceeded("statement_cap_reached")
        self.statements += 1
        resp = self._api(
            "post",
            "/api/2.0/sql/statements",
            {"warehouse_id": self.warehouse_id, "statement": sql, "wait_timeout": "30s", "on_wait_timeout": "CONTINUE"},
        )
        while resp.get("status", {}).get("state") in {"PENDING", "RUNNING"}:
            self._sleep(POLL_SECONDS)
            resp = self._api("get", f"/api/2.0/sql/statements/{resp['statement_id']}")
        state = resp.get("status", {}).get("state")
        if state != "SUCCEEDED":
            code = (resp.get("status", {}).get("error") or {}).get("error_code", "unknown")
            raise EngineError(f"statement_{state}:{code}")
        return resp

    def run(self, sql: str) -> None:
        self._execute(sql)

    def query(self, sql: str) -> list[tuple[Any, ...]]:
        resp = self._execute(sql)
        rows = (resp.get("result") or {}).get("data_array") or []
        return [tuple(r) for r in rows]

    def upload(self, local: str, remote: str) -> None:
        """Copy one file into the staging volume. The caller has already checked that `remote` is inside the run's own schema."""
        self._check_cap()
        code, _ = self._runner(["databricks", "fs", "cp", local, f"dbfs:{remote}", "--overwrite", "-p", self.profile])
        if code != 0:
            raise EngineError("upload_failed")


def find_warehouse_id(profile: str, name: str, runner: Runner = _run) -> str:
    code, out = runner(["databricks", "warehouses", "list", "-p", profile, "-o", "json"])
    if code != 0:
        raise EngineError("warehouse_list_failed")
    matches = [w["id"] for w in json.loads(out) if w.get("name") == name]
    if len(matches) != 1:
        raise EngineError(f"warehouse_matches:{len(matches)}")
    return matches[0]
