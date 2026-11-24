"""A local stack for trying the Databricks Connect screens against a REAL workspace, with nothing deployed: `python -m reference_stack.local_stack`.

It runs the real app (real routes, Connect wiring, the real Databricks connector) on 127.0.0.1 with a test identity (a locally generated key; no Entra), an in-memory secret store, a
temporary SQLite control database and no model. With `--web` it also starts the web app's dev server. It makes NO call to any workspace by itself: warehouse calls happen only when YOU
run the generated enrol or rotate file in your terminal under your own Databricks profile, or switch warm-up on in the screen.

What it does not cover (these need a deployed instance): real Entra sign-in, the Key Vault secret store, a public https address and TLS, other people's browsers and networks.
The test token it writes is for this process's throwaway key only; it opens nothing else."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
TOKEN_HOURS = 8  # a working day: the token is for a throwaway key on this computer only
PORT, WEB_PORT = 8090, 5174  # 8090 is where the web dev server proxies /api (apps/analytics-web/vite.config.ts)


def build(work: Path, *, connector: Any | None = None, port: int = PORT) -> tuple[Any, str]:
    from reference_stack.deployment_check.check import build_local_app

    local = build_local_app(work, connector=connector, enrol_url=f"http://127.0.0.1:{port}/api/v2/connect/databricks/enrol", token_ttl=TOKEN_HOURS * 3600)
    return local, local.token


def banner(token_file: Path, *, web: bool, port: int) -> str:
    return "\n".join([
        "",
        f"  API   http://127.0.0.1:{port}   (real routes, real Databricks connector, test identity)",
        f"  Web   http://127.0.0.1:{WEB_PORT}   " + ("(started for you)" if web else "(start it: cd apps/analytics-web && npm run dev -- --host 127.0.0.1)"),
        f"  Token {token_file}   (paste it into the screen's Access token box; it lasts 8 hours)",
        "",
        "  In the screen: Connect a warehouse > Databricks (internal testing) > Open. Use the host of your workspace, the catalog, tables as SCHEMA.TABLE, and the warehouse's name.",
        "  Then: One file (recommended) > Get the file > download it, and in your own terminal run it as the screen says (python3 compass-enrol.py --profile <your profile>).",
        "  The screen shows waiting, then connected. Remove it afterwards from the screen, then run the same file with --remove <application id> as the screen shows.",
        "  Ctrl-C stops everything. Nothing is deployed and nothing is stored outside this process.",
        "",
    ])


def main(argv: list[str] | None = None, *, connector: Any | None = None, serve: Callable[..., Any] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reference_stack.local_stack", description=__doc__)
    p.add_argument("--web", action="store_true", help="also start the web dev server on 127.0.0.1:5174")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--token-file", default=str(REPO / ".local-stack" / "token.txt"), help="where the test token is written (git-ignored)")
    p.add_argument("--selftest", action="store_true", help="start, check the Databricks routes answer with the token, stop; exit 0 if so")
    args = p.parse_args(argv)
    if connector is None:
        try:
            import databricks.sdk.core  # noqa: F401
            import databricks.sql  # noqa: F401
        except ImportError:
            print("refused: the Databricks connector is not installed (pip install -r requirements-warehouses.txt)", file=sys.stderr)
            return 2
    token_file = Path(args.token_file)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        local, token = build(Path(tmp), connector=connector, port=args.port)
        token_file.write_text(token)
        token_file.chmod(0o600)
        if args.selftest:
            from reference_stack.enrol_check.__main__ import real_serve

            client, _base, stop = (serve or real_serve)(local.app)
            try:
                r = client.get("/api/v2/connect/databricks/capabilities", headers={"Authorization": f"Bearer {token}"})
                ok = r.status_code == 200 and r.json().get("can_manage") is True
                print(f"selftest: capabilities {r.status_code}, can_manage={r.json().get('can_manage') if r.status_code == 200 else None}")
            finally:
                stop()
            return 0 if ok else 1
        import uvicorn

        web_proc = None
        if args.web:
            web_proc = subprocess.Popen(["npm", "--prefix", str(REPO / "apps" / "analytics-web"), "run", "dev", "--", "--host", "127.0.0.1", "--port", str(WEB_PORT)],  # noqa: S603, S607
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(banner(token_file, web=args.web, port=args.port), flush=True)
        try:
            uvicorn.run(local.app, host="127.0.0.1", port=args.port, log_level="warning")
        except KeyboardInterrupt:
            pass
        finally:
            if web_proc is not None:
                web_proc.terminate()
                time.sleep(0.2)
            token_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
