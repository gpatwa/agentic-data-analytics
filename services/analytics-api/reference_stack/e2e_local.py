"""Click through the governed path on your own computer, over real data in DuckDB, with confirm-instead-of-refuse (S6) and the coverage check on.

    python -m reference_stack.e2e_local retail --data DIR --replies REPLIES.json [--synonyms SYN.json] [--web]
    python -m reference_stack.e2e_local olist  --data CSV_DIR --work DIR --replies REPLIES.json [--synonyms SYN.json] [--web]
    ... --selftest      start, ask one question, confirm it, check the number against the independent key, stop (exit 0 if it matches)

What is real: the governed runtime (identity, context, resolution, echo and coverage checks, the deterministic compiler, policy and cost checks, the DuckDB
read-only gateway, answer validation, evidence) and the routes under /api/v2/analytics. What is not: the language model. It REPLAYS recorded replies for the
questions in the evaluation set (written by a stand-in agent that saw only the product's own prompt) and refuses anything else, so only those questions
get an answer. Give it Azure OpenAI credentials in the usual settings if you want a live model instead (not wired here: a later step).

Local only: it listens on 127.0.0.1, uses a throwaway identity that exists only in this process, reads files you point it at, and contacts nothing. The
Retail contract is an evaluation fixture and nothing here certifies anything. Olist is for internal use only (its licence is reported non-commercial).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb

from reference_stack import e2e_duckdb as e

REPO = Path(__file__).resolve().parents[3]
PORT, WEB_PORT = 8090, 5174  # the web dev server proxies /api to 8090 (apps/analytics-web/vite.config.ts)
TOKEN_HOURS = 8


def build_env(dataset_name: str, data: Path, work: Path, replies_file: Path | None, synonyms_file: Path | None, *, live: Any = None) -> tuple[e.Env, e.Dataset]:
    if dataset_name in ("retail", "retail2"):
        from reference_stack import e2e_online_retail as m

        dataset = m.build(data, 2 if dataset_name == "retail2" else 1)
    elif dataset_name in ("synthetic", "synthetic_certified"):
        from reference_stack import e2e_synthetic as m

        dataset = (m.build_certified if dataset_name == "synthetic_certified" else m.build)(data, work / "prepared")
    else:
        from reference_stack import e2e_olist as m

        dataset = m.build(data, work / "prepared")
    if synonyms_file:
        proposed = {k: v for k, v in e.load_json(synonyms_file).items() if k != "notes"}
        dataset, _, _ = e.accept_synonyms(dataset, proposed)
    env = e.Env(dataset, work / "env", e.load_json(replies_file) if replies_file else {}, coverage=True, cost_cap=1e9, s6=True, live=live)
    return env, dataset


def add_routes(env: e.Env) -> None:
    from fastapi import FastAPI

    from reference_stack.local_stack.__main__ import dev_sign_in_router

    app: FastAPI = env.app
    app.include_router(dev_sign_in_router(lambda: env.stack.token("requester", ttl_minutes=TOKEN_HOURS * 60)))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "local DuckDB click-through"}


def selftest(env: e.Env, dataset: e.Dataset) -> int:
    """One answerable question that the wording check refuses on its own: ask it, confirm the reading as a person would, compare with the key."""
    case = next(c for c in dataset.cases if c.expect == "answer" and c.id in env.replies and env.replies[c.id].get("metrics"))
    body = env.ask(case)
    asked = body.get("state") == "waiting_clarification"
    if asked:
        question = body["outcome"]["questions"][0]
        print(f"selftest: asked to confirm: {question['prompt'][:110]}")
        auth = {"Authorization": f"Bearer {env.stack.token('requester')}"}
        body = env.client.post(f"{e.URL}/runs/{body['run_id']}/clarify", json={"purpose": e.PURPOSE, "ambiguity_code": "interpretation", "selected_id": question["choices"][0]["id"]}, headers=auth).json()
    status, detail = e.judge(case, body, dataset.tolerance)
    print(f"selftest: {case.id} {case.question!r} -> {status} ({detail})")
    return 0 if status == "correct" else 1


def banner(dataset: e.Dataset, env: e.Env, *, web: bool, port: int, token_file: Path) -> str:
    answerable = [c for c in dataset.cases if c.expect == "answer" and c.id in env.replies]
    ask = "\n".join(f"      {c.question}" for c in answerable[:8])
    return "\n".join([
        "", f"  API   http://127.0.0.1:{port}   (the real governed routes over DuckDB; the model replays recorded replies)",
        f"  Web   http://127.0.0.1:{WEB_PORT}   " + ("(started for you)" if web else "(start it: cd apps/analytics-web && npm run dev -- --host 127.0.0.1)"),
        f"  The Governed screen asks for an access token: paste the one in {token_file} (a throwaway token for this process only, 8 hours; the file is removed when you stop).", "",
        ("  A live model answers every question you type." if env.live is not None else "  Questions the model has a recorded reply for (anything else is refused):") + ("" if env.live is not None else "\n" + ask), "",
        "  When a wording check would have refused, you are asked: \"I read this as ... Is that what you meant?\". Read it before you say yes.",
        "  Ctrl-C stops everything. Nothing is deployed, uploaded or stored outside this process.", "",
    ])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reference_stack.e2e_local", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dataset", choices=("retail", "retail2", "olist", "synthetic", "synthetic_certified"))
    p.add_argument("--data", required=True, type=Path)
    p.add_argument("--work", type=Path, help="a directory for prepared files (olist) and the throwaway control database; default: a temporary one")
    p.add_argument("--replies", type=Path, help="recorded replies (required unless --live-model)")
    p.add_argument("--live-model", action="store_true", help="use a live model from the environment's settings instead of recorded replies (see reference_stack/live_model.py)")
    p.add_argument("--synonyms", type=Path)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--web", action="store_true", help="also start the web dev server on 127.0.0.1:5174")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if not args.live_model and not args.replies:
        print("refused: give --replies, or --live-model with a provider configured in the environment", file=sys.stderr)
        return 2
    try:
        live = None
        if args.live_model:
            from reference_stack import live_model

            live = live_model.build_client()
            print(f"  Live model: {live_model.describe()}. Each question's prompt (the certified vocabulary and the question, never a row of data) is sent to it.", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            work = args.work or Path(tmp)
            work.mkdir(parents=True, exist_ok=True)
            env, dataset = build_env(args.dataset, args.data, work, args.replies, args.synonyms, live=live)
            add_routes(env)
            if args.selftest:
                return selftest(env, dataset)
            import uvicorn

            web_proc = None
            if args.web:
                web_proc = subprocess.Popen(["npm", "--prefix", str(REPO / "apps" / "analytics-web"), "run", "dev", "--", "--host", "127.0.0.1", "--port", str(WEB_PORT)],  # noqa: S603, S607
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            token_file = REPO / ".local-stack" / "e2e-token.txt"  # .local-stack/ is git-ignored
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(env.stack.token("requester", ttl_minutes=TOKEN_HOURS * 60))
            token_file.chmod(0o600)
            print(banner(dataset, env, web=args.web, port=args.port, token_file=token_file), flush=True)
            try:
                uvicorn.run(env.app, host="127.0.0.1", port=args.port, log_level="warning")
            except KeyboardInterrupt:
                pass
            finally:
                token_file.unlink(missing_ok=True)
                if web_proc is not None:
                    web_proc.terminate()
                    time.sleep(0.2)
            return 0
    except (OSError, ValueError, KeyError, duckdb.Error, RuntimeError) as exc:
        from reference_stack.live_model import LiveModelNotConfigured

        print(f"refused: {exc if isinstance(exc, LiveModelNotConfigured) else type(exc).__name__}", file=sys.stderr)  # a fixed message that names settings, never a value  # the type only: paths and file contents are not echoed
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
