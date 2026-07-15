"""Safe P6 run query, trace pagination, and terminal cleanup API tests."""

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage

from repopilot.api.app import create_app
from repopilot.infrastructure.config import AppSettings
from tests.scripted_model import ScriptedToolCallingModel


class BindingFailureModel(ScriptedToolCallingModel):
    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        del tools, tool_choice, kwargs
        raise RuntimeError("binding failed")


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        workspace_path=tmp_path,
        data_directory=tmp_path.parent / f"{tmp_path.name}-runtime",
        model_api_key=None,
        _env_file=None,
    )


def test_terminal_run_can_be_queried_traced_listed_and_deleted(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="nothing to change"),
            AIMessage(content="second safe answer"),
        ]
    )
    with TestClient(create_app(settings, model_override=model)) as client:
        started = client.post("/agent/run", json={"goal": "inspect", "max_steps": 2})
        assert started.status_code == 200
        run_id = started.json()["run_id"]
        other = client.post("/agent/run", json={"goal": "inspect again", "max_steps": 2})
        other_run_id = other.json()["run_id"]

        run = client.get(f"/agent/runs/{run_id}")
        repeated_run = client.get(f"/agent/runs/{run_id}")
        listing = client.get("/agent/runs", params={"status": "success", "limit": 1})
        events = client.get(f"/agent/runs/{run_id}/events", params={"limit": 200})

        assert run.status_code == listing.status_code == events.status_code == 200
        assert repeated_run.json()["updated_at"] == run.json()["updated_at"]
        assert run.json()["status"] == "success"
        assert "nothing to change" not in run.text
        assert "messages" not in run.json() and "thread_id" not in run.json()
        assert len(listing.json()["items"]) == 1
        assert listing.json()["next_cursor"] is not None
        next_page = client.get(
            "/agent/runs",
            params={"status": "success", "limit": 1, "cursor": listing.json()["next_cursor"]},
        )
        assert next_page.status_code == 200
        assert {
            listing.json()["items"][0]["run_id"],
            next_page.json()["items"][0]["run_id"],
        } == {run_id, other_run_id}
        event_types = [item["event_type"] for item in events.json()["items"]]
        assert event_types[0] == "run_started"
        assert "model_completed" in event_types
        assert "run_completed" in event_types
        assert "nothing to change" not in events.text
        model_events = client.get(
            f"/agent/runs/{run_id}/events", params={"event_type": "model_completed"}
        )
        assert [item["event_type"] for item in model_events.json()["items"]] == ["model_completed"]
        first_event_id = events.json()["items"][0]["event_id"]
        incremental = client.get(
            f"/agent/runs/{run_id}/events",
            params={"after_event_id": first_event_id, "limit": 200},
        )
        assert all(item["event_id"] > first_event_id for item in incremental.json()["items"])

        deleted = client.delete(f"/agent/runs/{run_id}")
        assert deleted.status_code == 200 and deleted.json()["deleted"] is True
        assert client.delete(f"/agent/runs/{run_id}").status_code == 404
        assert client.get(f"/agent/runs/{run_id}").status_code == 404
        assert client.get(f"/agent/runs/{run_id}/events").status_code == 404
        assert client.get(f"/agent/runs/{other_run_id}").status_code == 200

    with sqlite3.connect(settings.data_directory / settings.checkpoint_database_name) as database:
        assert (
            database.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (run_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            database.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (other_run_id,)
            ).fetchone()[0]
            > 0
        )
    with sqlite3.connect(settings.data_directory / settings.runtime_database_name) as database:
        assert (
            database.execute(
                "SELECT COUNT(*) FROM trace_events WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            database.execute(
                "SELECT deleted_at IS NOT NULL FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            == 1
        )


def test_live_approval_run_cannot_be_deleted(tmp_path: Path) -> None:
    (tmp_path / "target.py").write_text("old\n", encoding="utf-8")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "propose_patch",
                        "args": {
                            "path": "target.py",
                            "new_content": "new\n",
                            "rationale": "change",
                        },
                        "id": "patch",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    with TestClient(create_app(_settings(tmp_path), model_override=model)) as client:
        started = client.post("/agent/run", json={"goal": "change", "max_steps": 3})
        assert started.status_code == 202
        deleted = client.delete(f"/agent/runs/{started.json()['run_id']}")

    assert deleted.status_code == 409
    assert deleted.json()["detail"]["code"] == "run_not_terminal"


def test_model_binding_failure_records_terminal_trace(tmp_path: Path) -> None:
    with TestClient(
        create_app(_settings(tmp_path), model_override=BindingFailureModel(responses=[]))
    ) as client:
        started = client.post("/agent/run", json={"goal": "inspect"})
        events = client.get(f"/agent/runs/{started.json()['run_id']}/events")

    assert started.status_code == 200
    assert started.json()["status"] == "model_error"
    assert [event["event_type"] for event in events.json()["items"]] == [
        "run_started",
        "run_failed",
    ]
