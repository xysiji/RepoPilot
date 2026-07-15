"""P5 HTTP lifecycle, input constraints, and safe report tests."""

from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from repopilot.api.app import create_app
from repopilot.infrastructure.config import AppSettings
from repopilot.testing.contracts import TestOutcome
from tests.fake_runner import ScriptedPytestRunner, make_test_result
from tests.scripted_model import ScriptedToolCallingModel


def _patch(content: str, call_id: str) -> dict[str, object]:
    return {
        "name": "propose_patch",
        "args": {
            "path": "calculator.py",
            "new_content": content,
            "rationale": "repair",
        },
        "id": call_id,
        "type": "tool_call",
    }


def _app(tmp_path: Path, outcomes: list[TestOutcome]):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "calculator.py").write_text("value = 0\n", encoding="utf-8")
    settings = AppSettings(
        workspace_path=tmp_path,
        data_directory=tmp_path.parent / f"{tmp_path.name}-runtime",
        model_api_key="api-secret-must-not-leak",
        max_repair_attempts=2,
        _env_file=None,
    )
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_patch("value = 1\n", "first")]),
            AIMessage(content="", tool_calls=[_patch("value = 2\n", "second")]),
        ]
    )
    exit_codes = {
        TestOutcome.PASSED: 0,
        TestOutcome.TEST_FAILURES: 1,
        TestOutcome.TIMEOUT: None,
    }
    runner = ScriptedPytestRunner(
        [
            make_test_result(
                outcome,
                exit_code=exit_codes.get(outcome, 3),
                output="safe test summary",
                timed_out=outcome is TestOutcome.TIMEOUT,
            )
            for outcome in outcomes
        ]
    )
    return create_app(settings, model_override=model, runner_override=runner), runner


def _decision(client: TestClient, payload: dict[str, object]):
    approval = payload["approval"]
    assert isinstance(approval, dict)
    return client.post(
        f"/agent/runs/{payload['run_id']}/decision",
        json={"proposal_id": approval["proposal_id"], "decision": "approve"},
    )


def test_api_returns_202_for_each_patch_then_200_repaired(tmp_path: Path) -> None:
    app, runner = _app(tmp_path, [TestOutcome.TEST_FAILURES, TestOutcome.PASSED])

    with TestClient(app) as client:
        first = client.post(
            "/agent/run",
            json={"goal": "repair", "max_steps": 4, "max_repair_attempts": 2},
        )
        second = _decision(client, first.json())
        completed = _decision(client, second.json())
        health = client.get("/health")

    assert first.status_code == second.status_code == 202
    assert completed.status_code == 200
    assert completed.json()["status"] == "repaired"
    assert completed.json()["final_report"]["outcome"] == "repaired"
    assert completed.json()["final_report"]["repair_attempts"] == 2
    assert runner.run_calls == 2
    assert health.status_code == 200
    assert "api-secret-must-not-leak" not in completed.text
    assert str(tmp_path) not in completed.text


def test_api_repair_exhausted_and_timeout_are_stable_http_200(tmp_path: Path) -> None:
    exhausted_app, _runner = _app(tmp_path / "exhausted", [TestOutcome.TEST_FAILURES])
    timeout_path = tmp_path / "timeout"
    timeout_path.mkdir()
    timeout_app, _timeout_runner = _app(timeout_path, [TestOutcome.TIMEOUT])

    with TestClient(exhausted_app) as client:
        pending = client.post(
            "/agent/run",
            json={"goal": "repair", "max_repair_attempts": 1},
        )
        exhausted = _decision(client, pending.json())
    with TestClient(timeout_app) as client:
        pending = client.post("/agent/run", json={"goal": "repair"})
        timed_out = _decision(client, pending.json())

    assert exhausted.status_code == 200
    assert exhausted.json()["status"] == "repair_attempts_exhausted"
    assert timed_out.status_code == 200
    assert timed_out.json()["status"] == "test_timeout"
    assert timed_out.json()["final_report"]["outcome"] == "test_timeout"


def test_api_rejects_command_controls_and_system_limit_override(tmp_path: Path) -> None:
    app, runner = _app(tmp_path, [TestOutcome.PASSED])

    with TestClient(app) as client:
        responses = [
            client.post("/agent/run", json={"goal": "x", field: "forged"})
            for field in ("test_command", "pytest_args", "shell", "cwd", "env", "executable")
        ]
        too_many = client.post(
            "/agent/run",
            json={"goal": "x", "max_repair_attempts": 3},
        )

    assert all(response.status_code == 422 for response in responses)
    assert too_many.status_code == 422
    assert runner.run_calls == 0
