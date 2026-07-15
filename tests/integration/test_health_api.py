"""Integration tests for the FastAPI application factory and health route."""

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from repopilot.api.app import create_app
from repopilot.infrastructure.config import AppSettings
from repopilot.schemas.health import HealthResponse


def test_health_returns_public_schema_with_fake_model(tmp_path) -> None:
    settings = AppSettings(
        app_name="RepoPilot Integration",
        app_env="test",
        model_provider="openai",
        model_name="offline-model",
        model_api_key="must-never-appear",
        model_base_url="https://example.test/v1?token=must-never-appear",
        data_directory=tmp_path / "runtime",
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
        "persistence_backend": "sqlite",
        "persistence_ready": True,
        "context_policy": "deterministic_compaction",
        "trace_backend": "sqlite",
    }
    assert "must-never-appear" not in response.text


def test_health_reports_missing_real_provider_configuration_without_network(tmp_path) -> None:
    settings = AppSettings(model_api_key=None, data_directory=tmp_path / "runtime", _env_file=None)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["model_configured"] is False


def test_health_does_not_construct_or_call_provider(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = AppSettings(
        model_api_key="configured-but-unused",
        data_directory=tmp_path / "runtime",
        _env_file=None,
    )

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


def test_two_apps_own_independent_sqlite_lifecycles(tmp_path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    app_a = create_app(
        AppSettings(
            workspace_path=workspace_a,
            data_directory=tmp_path / "data-a",
            _env_file=None,
        ),
        model_override=FakeListChatModel(responses=["unused"]),
    )
    app_b = create_app(
        AppSettings(
            workspace_path=workspace_b,
            data_directory=tmp_path / "data-b",
            _env_file=None,
        ),
        model_override=FakeListChatModel(responses=["unused"]),
    )

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        assert client_a.get("/health").status_code == 200
        assert client_b.get("/health").status_code == 200
        assert app_a.state.persistence is not app_b.state.persistence
        assert (tmp_path / "data-a" / "runtime.sqlite3").is_file()
        assert (tmp_path / "data-b" / "runtime.sqlite3").is_file()
