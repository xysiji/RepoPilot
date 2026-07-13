"""Offline integration tests for P1 service composition and POST /agent/run."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from repopilot.api.app import create_app
from repopilot.infrastructure.config import AppSettings
from tests.scripted_model import ScriptedToolCallingModel


def _call(name: str, args: dict[str, object], call_id: str) -> dict[str, object]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_agent_api_runs_list_read_answer_offline_without_leaking_tool_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("private README source", encoding="utf-8")
    secret = "integration-secret-key"
    settings = AppSettings(
        workspace_path=tmp_path,
        model_api_key=secret,
        model_base_url="https://example.test/v1?token=integration-url-secret",
        _env_file=None,
    )
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_call("list_files", {"directory": "."}, "list-1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_call("read_file", {"path": "README.md"}, "read-1")],
            ),
            AIMessage(content="README describes the offline P1 fixture."),
        ]
    )

    def fail_if_real_provider_is_constructed(*args: object, **kwargs: object) -> None:
        raise AssertionError("real provider construction attempted")

    monkeypatch.setattr(
        "repopilot.infrastructure.model_factory.ChatOpenAI",
        fail_if_real_provider_is_constructed,
    )
    with TestClient(create_app(settings, model_override=model)) as client:
        response = client.post(
            "/agent/run",
            json={"goal": "Summarize README.md", "max_steps": 4},
        )
        health = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["final_answer"] == "README describes the offline P1 fixture."
    assert [item["tool_name"] for item in payload["tool_executions"]] == [
        "list_files",
        "read_file",
    ]
    assert "private README source" not in response.text
    assert secret not in response.text
    assert "integration-url-secret" not in response.text
    assert health.status_code == 200


def test_agent_api_returns_stable_error_when_model_is_not_configured(tmp_path: Path) -> None:
    settings = AppSettings(workspace_path=tmp_path, model_api_key=None, _env_file=None)

    with TestClient(create_app(settings)) as client:
        response = client.post("/agent/run", json={"goal": "Inspect files"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "model_not_configured",
            "message": "A configured model or injected test model is required",
        }
    }


def test_agent_api_rejects_blank_goal_before_running_model(tmp_path: Path) -> None:
    settings = AppSettings(workspace_path=tmp_path, model_api_key=None, _env_file=None)
    model = ScriptedToolCallingModel(responses=[AIMessage(content="must not run")])

    with TestClient(create_app(settings, model_override=model)) as client:
        response = client.post("/agent/run", json={"goal": "   "})

    assert response.status_code == 422
    assert model.received_messages == []


def test_two_agent_api_runs_do_not_share_message_history(tmp_path: Path) -> None:
    settings = AppSettings(workspace_path=tmp_path, model_api_key=None, _env_file=None)
    model = ScriptedToolCallingModel(
        responses=[AIMessage(content="first answer"), AIMessage(content="second answer")]
    )

    with TestClient(create_app(settings, model_override=model)) as client:
        first = client.post("/agent/run", json={"goal": "first goal"})
        second = client.post("/agent/run", json={"goal": "second goal"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(model.received_messages) == 2
    assert all(len(messages) == 1 for messages in model.received_messages)
    assert isinstance(model.received_messages[0][0], HumanMessage)
    assert model.received_messages[0][0].content == "first goal"
    assert model.received_messages[1][0].content == "second goal"
