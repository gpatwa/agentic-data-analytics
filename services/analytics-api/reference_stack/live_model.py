"""A live language model for the local DuckDB runs, built from the same settings the deployed app reads, and nothing else.

Configuration comes from the environment (and the usual `.env`), never from a flag, so no key appears in a command line, a log or a report:

    Azure OpenAI:      AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT (and AZURE_OPENAI_API_VERSION, AZURE_OPENAI_REASONING_EFFORT)
    OpenAI-compatible: ANALYTICS_LLM_API_KEY or OPENAI_API_KEY, ANALYTICS_LLM_MODEL, ANALYTICS_LLM_BASE_URL

What leaves this computer: the product's own prompt for each question, which holds the certified vocabulary (metric, dimension and field ids and display names,
synonyms) and the question text, plus the tenant id. **No row of data and no value from the data is ever in it**: the model interprets a question, it never
reads the table. Read the contract's names and the questions you type before pointing this at a provider you do not control.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.llm_structured import OpenAIStructuredClient


class LiveModelNotConfigured(RuntimeError):
    """Raised with a fixed message that names the settings to provide, never a value."""


def build_client(settings: Settings | None = None) -> OpenAIStructuredClient:
    s = settings or Settings()
    if s.AZURE_OPENAI_ENDPOINT:
        if not s.AZURE_OPENAI_API_KEY:
            raise LiveModelNotConfigured("AZURE_OPENAI_ENDPOINT is set but AZURE_OPENAI_API_KEY is not")
        return OpenAIStructuredClient.azure(endpoint=s.AZURE_OPENAI_ENDPOINT, api_key=s.AZURE_OPENAI_API_KEY, api_version=s.AZURE_OPENAI_API_VERSION,
                                            deployment=s.AZURE_OPENAI_DEPLOYMENT, reasoning_effort=s.AZURE_OPENAI_REASONING_EFFORT)
    key = s.ANALYTICS_LLM_API_KEY or s.OPENAI_API_KEY
    if not key:
        raise LiveModelNotConfigured("set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY, or ANALYTICS_LLM_API_KEY (or OPENAI_API_KEY), to use a live model")
    return OpenAIStructuredClient.openai_compatible(api_key=key, model=s.ANALYTICS_LLM_MODEL, base_url=s.ANALYTICS_LLM_BASE_URL)


def describe(settings: Settings | None = None) -> str:
    """Which provider and model, with no key."""
    s = settings or Settings()
    if s.AZURE_OPENAI_ENDPOINT:
        return f"Azure OpenAI, deployment {s.AZURE_OPENAI_DEPLOYMENT}"
    return f"an OpenAI-compatible endpoint, model {s.ANALYTICS_LLM_MODEL}"


def record_replies(client: Any, prompts: dict[str, dict[str, str]], *, max_tokens: int = 1_024) -> dict[str, Any]:
    """Ask the model each captured product prompt and keep its reply as a recorded reply (a typed intent, or the unsupported marker)."""
    from packages.platform_contracts.analytics_intent import AnalyticalIntent

    schema = AnalyticalIntent.model_json_schema()
    replies: dict[str, Any] = {}
    for case_id, item in prompts.items():
        try:
            reply = client.complete_json(prompt=item["prompt"], schema=schema, max_tokens=max_tokens)
        except (TimeoutError, ConnectionError):
            reply = {"unsupported_request": True, "_note": "the model did not answer"}
        replies[case_id] = reply if isinstance(reply, dict) else {"unsupported_request": True, "_note": "the reply was not an object"}
    return replies
