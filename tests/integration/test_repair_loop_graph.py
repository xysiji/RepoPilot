"""Offline P5 repair loop with real pytest and repeated human approval."""

import asyncio
import json
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage

from repopilot.approval.contracts import ApprovalDecisionRequest
from repopilot.services.agent_service import AgentService
from repopilot.testing.contracts import TestOutcome
from tests.fake_runner import ScriptedPytestRunner, make_test_result
from tests.scripted_model import ScriptedToolCallingModel


def _patch(content: str, call_id: str) -> dict[str, object]:
    return {
        "name": "propose_patch",
        "args": {
            "path": "calculator.py",
            "new_content": content,
            "rationale": "Repair deterministic calculator behavior",
        },
        "id": call_id,
        "type": "tool_call",
    }


def _approve(service: AgentService, pending):
    assert pending.approval is not None
    return asyncio.run(
        service.resume_run(
            pending.run_id,
            ApprovalDecisionRequest(
                proposal_id=pending.approval.proposal_id,
                decision="approve",
            ),
        )
    )


def test_two_approved_patches_fail_then_pass_with_real_pytest(tmp_path: Path) -> None:
    (tmp_path / "calculator.py").write_text(
        "def add(left, right):\n    return left - right\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    first_content = "def add(left, right):\n    return left * right\n"
    second_content = "def add(left, right):\n    return left + right\n"
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_patch(first_content, "patch-one")]),
            AIMessage(content="", tool_calls=[_patch(second_content, "patch-two")]),
        ]
    )
    service = AgentService(tmp_path, model, max_repair_attempts=2)

    first_pending = asyncio.run(service.start_run("repair add", max_steps=4, max_repair_attempts=2))
    second_pending = _approve(service, first_pending)

    assert second_pending.status == "awaiting_approval"
    assert second_pending.approval is not None
    assert first_pending.approval is not None
    assert second_pending.approval.proposal_id != first_pending.approval.proposal_id
    assert (tmp_path / "calculator.py").read_text(encoding="utf-8") == first_content
    failed_feedback = next(
        message for message in model.received_messages[1] if isinstance(message, ToolMessage)
    )
    failed_payload = json.loads(str(failed_feedback.content))
    assert failed_feedback.tool_call_id == "patch-one"
    assert failed_payload["data"]["test_outcome"] == "test_failures"

    completed = _approve(service, second_pending)

    assert completed.status == "repaired"
    assert completed.final_report is not None
    assert completed.final_report.outcome == "repaired"
    assert completed.final_report.repair_attempts == 2
    assert completed.final_report.approval_count == 2
    assert completed.final_report.patches_applied == 2
    assert completed.final_report.latest_test_exit_code == 0
    assert completed.final_report.review_status == "passed"
    assert (tmp_path / "calculator.py").read_text(encoding="utf-8") == second_content
    assert [record.tool_call_id for record in completed.tool_executions] == [
        "patch-one",
        "patch-two",
    ]


def test_one_attempt_budget_stops_without_requesting_second_patch(tmp_path: Path) -> None:
    (tmp_path / "calculator.py").write_text("value = 1\n", encoding="utf-8")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_patch("value = 2\n", "only-patch")]),
            AIMessage(content="must not be invoked"),
        ]
    )
    runner = ScriptedPytestRunner(
        [make_test_result(TestOutcome.TEST_FAILURES, exit_code=1, output="failed")]
    )
    service = AgentService(tmp_path, model, runner=runner, max_repair_attempts=1)
    pending = asyncio.run(service.start_run("repair", max_steps=4, max_repair_attempts=1))

    completed = _approve(service, pending)

    assert completed.status == "repair_attempts_exhausted"
    assert completed.final_report is not None
    assert completed.final_report.outcome == "repair_attempts_exhausted"
    assert runner.run_calls == 1
    assert len(model.received_messages) == 1


def test_model_text_after_failed_tests_is_repair_abandoned_not_success(tmp_path: Path) -> None:
    (tmp_path / "calculator.py").write_text("value = 1\n", encoding="utf-8")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_patch("value = 2\n", "patch")]),
            AIMessage(content="I cannot repair this further."),
        ]
    )
    runner = ScriptedPytestRunner([make_test_result(TestOutcome.TEST_FAILURES, exit_code=1)])
    service = AgentService(tmp_path, model, runner=runner, max_repair_attempts=2)
    pending = asyncio.run(service.start_run("repair", max_steps=4))

    completed = _approve(service, pending)

    assert completed.status == "repair_abandoned"
    assert completed.final_report is not None
    assert completed.final_report.outcome == "repair_abandoned"
    assert completed.final_report.model_final_text == "I cannot repair this further."
