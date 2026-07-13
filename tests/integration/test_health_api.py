"""Integration tests for the FastAPI application factory and health route."""

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from repopilot.api.app import create_app
from repopilot.infrastructure.config import AppSettings
from repopilot.schemas.health import HealthResponse


def test_health_returns_public_schema_with_fake_model() -> None:
    settings = AppSettings(
        app_name="RepoPilot Integration",
        app_env="test",
        model_provider="openai",
        model_name="offline-model",
        model_api_key="must-never-appear",
        model_base_url="https://example.test/v1?token=must-never-appear",
        _env_file=None,
    )
    fake_model = FakeListChatModel(responses=["unused"])

    with TestClient(create_app(settings, model_override=fake_model)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert HealthResponse.model_validate(payload).model_dump() == payload
    assert payload == {
        "status": "ok",
        "app_name": "RepoPilot Integration",
        "app_env": "test",
        "model_provider": "openai",
        "model_name": "offline-model",
        "model_configured": True,
    }
    assert "must-never-appear" not in response.text


def test_health_reports_missing_real_provider_configuration_without_network() -> None:
    settings = AppSettings(model_api_key=None, _env_file=None)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["model_configured"] is False


def test_health_does_not_construct_or_call_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings(model_api_key="configured-but-unused", _env_file=None)

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("Health API attempted a real model operation")

    monkeypatch.setattr(
        "repopilot.infrastructure.model_factory.ChatOpenAI",
        fail_if_called,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["model_configured"] is True
