"""Offline integration tests for the P2 LangGraph service and POST /agent/run."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from repopilot.api.app import create_app
from repopilot.infrastructure.config import AppSettings
from tests.scripted_model import ScriptedToolCallingModel


def _call(name: str, args: dict[str, object], call_id: str) -> dict[str, object]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_agent_api_runs_graph_offline_without_leaking_tool_content(
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
                tool_calls=[_call("read_file", {"path": "README.md"}, "read-1")],
            ),
            AIMessage(content="README describes the offline P2 graph fixture."),
        ]
    )

    def fail_if_real_provider_is_constructed(*args: object, **kwargs: object) -> None:
        raise AssertionError("real provider construction attempted")

    monkeypatch.setattr(
        "repopilot.infrastructure.model_factory.ChatOpenAI",
        fail_if_real_provider_is_constructed,
    )
    with TestClient(create_app(settings, model_override=model)) as client:
        response = client.post("/agent/run", json={"goal": "Summarize README.md", "max_steps": 3})
        health = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["final_answer"] == "The run completed without applying a patch."
    assert payload["final_report"]["model_final_text"] == (
        "README describes the offline P2 graph fixture."
    )
    assert [item["tool_name"] for item in payload["tool_executions"]] == ["read_file"]
    assert "private README source" not in response.text
    assert secret not in response.text
    assert "integration-url-secret" not in response.text
    assert health.status_code == 200


def test_agent_api_returns_stable_error_when_model_is_not_configured(tmp_path: Path) -> None:
    settings = AppSettings(workspace_path=tmp_path, model_api_key=None, _env_file=None)

    with TestClient(create_app(settings)) as client:
        response = client.post("/agent/run", json={"goal": "Inspect files"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "model_not_configured"


def test_agent_api_rejects_blank_goal_before_running_model(tmp_path: Path) -> None:
    settings = AppSettings(workspace_path=tmp_path, model_api_key=None, _env_file=None)
    model = ScriptedToolCallingModel(responses=[AIMessage(content="must not run")])

    with TestClient(create_app(settings, model_override=model)) as client:
        response = client.post("/agent/run", json={"goal": "   "})

    assert response.status_code == 422
    assert model.received_messages == []


def test_two_agent_api_runs_do_not_share_graph_state(tmp_path: Path) -> None:
    settings = AppSettings(workspace_path=tmp_path, model_api_key=None, _env_file=None)
    model = ScriptedToolCallingModel(
        responses=[AIMessage(content="first answer"), AIMessage(content="second answer")]
    )

    with TestClient(create_app(settings, model_override=model)) as client:
        first = client.post("/agent/run", json={"goal": "first goal"})
        second = client.post("/agent/run", json={"goal": "second goal"})

    assert first.status_code == second.status_code == 200
    assert len(model.received_messages) == 2
    assert all(len(messages) == 1 for messages in model.received_messages)
    assert isinstance(model.received_messages[0][0], HumanMessage)
    assert model.received_messages[0][0].content == "first goal"
    assert model.received_messages[1][0].content == "second goal"


def test_agent_api_maps_graph_max_steps_without_recursion_error(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    settings = AppSettings(workspace_path=tmp_path, model_api_key=None, _env_file=None)
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_call("read_file", {"path": "a.txt"}, "last-call")],
            )
        ]
    )

    with TestClient(create_app(settings, model_override=model)) as client:
        response = client.post("/agent/run", json={"goal": "read", "max_steps": 1})

    assert response.status_code == 200
    assert response.json()["status"] == "max_steps_exceeded"
    assert response.json()["steps"] == 1
    assert response.json()["tool_executions"][0]["tool_call_id"] == "last-call"


def test_agent_api_returns_sanitized_p3_policy_audit_without_raw_arguments(
    tmp_path: Path,
) -> None:
    secret = "P3_SECRET_MUST_NOT_LEAK"
    (tmp_path / ".env").write_text(secret, encoding="utf-8")
    settings = AppSettings(workspace_path=tmp_path, model_api_key=None, _env_file=None)
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_call("read_file", {"path": ".env"}, "policy-denied")],
            ),
            AIMessage(content="The protected file was not read."),
        ]
    )

    with TestClient(create_app(settings, model_override=model)) as client:
        response = client.post("/agent/run", json={"goal": "Inspect configuration"})

    audit = response.json()["tool_executions"][0]
    assert response.status_code == 200
    assert audit["phase"] == "policy"
    assert audit["failure_category"] == "policy_denied"
    assert audit["error_code"] == "sensitive_path_denied"
    assert audit["effect"] == "read_only"
    assert audit["policy_allowed"] is False
    assert audit["input"] == {"fields": ["path"]}
    assert ".env" not in response.text
    assert secret not in response.text
