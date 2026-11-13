"""/health says why it is degraded, with short codes only, and stays "ready" when nothing is missing."""

from unittest.mock import PropertyMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.service import AnalyticsService


def _health(*, database: bool, llm: bool, index: bool) -> dict:
    with (
        patch.object(AnalyticsService, "database_configured", new_callable=PropertyMock, return_value=database),
        patch.object(AnalyticsService, "llm_configured", new_callable=PropertyMock, return_value=llm),
        patch.object(AnalyticsService, "context_index_ready", new_callable=PropertyMock, return_value=index),
    ):
        response = TestClient(app).get("/health")
    assert response.status_code == 200
    return response.json()


def test_ready_has_no_reasons():
    body = _health(database=True, llm=True, index=True)
    assert body["status"] == "ready" and body["degraded_reasons"] == []


def test_each_missing_dependency_is_named():
    body = _health(database=False, llm=False, index=False)
    assert body["status"] == "degraded"
    assert body["degraded_reasons"] == ["database_not_configured", "llm_not_configured", "context_index_not_ready"]


def test_single_missing_dependency_is_the_only_reason():
    body = _health(database=True, llm=False, index=True)
    assert body["status"] == "degraded" and body["degraded_reasons"] == ["llm_not_configured"]


def test_azure_openai_settings_count_as_a_configured_llm():
    from app.config import Settings
    from app.service import AnalyticsService

    base = {"ANALYTICS_DEMO_MODE": False}
    azure = Settings(**base, AZURE_OPENAI_ENDPOINT="https://example.invalid", AZURE_OPENAI_API_KEY="test-key")
    assert AnalyticsService(azure).llm_configured is True
    endpoint_only = Settings(**base, AZURE_OPENAI_ENDPOINT="https://example.invalid")
    assert AnalyticsService(endpoint_only).llm_configured is False
