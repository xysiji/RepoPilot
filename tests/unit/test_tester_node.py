"""Tester node single-run, feedback, and budget semantics."""

import asyncio
import json
from uuid import uuid4

from langchain_core.messages import ToolMessage

from repopilot.agent.nodes import TesterNode
from repopilot.agent.routing import route_after_tester
from repopilot.agent.state import create_initial_state
from repopilot.testing.contracts import AppliedPatchContext, TestOutcome
from tests.fake_runner import ScriptedPytestRunner, make_test_result


def _state(*, max_attempts: int = 2):
    state = create_initial_state("goal", 4, max_repair_attempts=max_attempts)
    state["model_calls"] = 1
    context = AppliedPatchContext(
        proposal_id=uuid4(),
        tool_call_id="original-patch-id",
        relative_path="a.py",
        original_sha256="a" * 64,
        proposed_sha256="b" * 64,
        added_line_count=1,
        removed_line_count=1,
        model_call=1,
    )
    state["applied_patch_context"] = context.model_dump(mode="json")
    state["applied_patches"].append(context.model_dump(mode="json"))
    return state, context


def test_tester_passes_once_appends_record_and_consumes_apply_context() -> None:
    state, context = _state()
    runner = ScriptedPytestRunner(
        [make_test_result(TestOutcome.PASSED, exit_code=0, output="not exposed")]
    )

    update = asyncio.run(TesterNode(runner)(state))

    assert runner.run_calls == 1
    assert update["repair_attempts"] == 1
    assert update["applied_patch_context"] is None
    assert update["test_runs"][0]["proposal_id"] == str(context.proposal_id)
    assert update["latest_test_result"]["outcome"] == "passed"
    message = update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "original-patch-id"
    assert json.loads(str(message.content))["data"]["patch_applied"] is True


def test_tester_failure_returns_to_model_only_with_both_budgets() -> None:
    state, _context = _state(max_attempts=2)
    runner = ScriptedPytestRunner(
        [make_test_result(TestOutcome.TEST_FAILURES, exit_code=1, output="assertion failed")]
    )

    update = asyncio.run(TesterNode(runner)(state))
    state.update(update)

    assert state["status"] == "running"
    assert route_after_tester(state) == "model"
    assert state["tool_executions"][-1]["error_code"] == "pytest_tests_failed"


def test_tester_exhausts_repair_budget_without_automatic_rerun() -> None:
    state, _context = _state(max_attempts=1)
    runner = ScriptedPytestRunner([make_test_result(TestOutcome.TEST_FAILURES, exit_code=1)])

    update = asyncio.run(TesterNode(runner)(state))
    state.update(update)

    assert runner.run_calls == 1
    assert state["status"] == "repair_attempts_exhausted"
    assert route_after_tester(state) == "reviewer"


def test_model_budget_can_stop_loop_while_repair_budget_remains() -> None:
    state, _context = _state(max_attempts=3)
    state["model_calls"] = state["max_steps"]
    runner = ScriptedPytestRunner([make_test_result(TestOutcome.TEST_FAILURES, exit_code=1)])

    update = asyncio.run(TesterNode(runner)(state))
    state.update(update)

    assert state["repair_attempts"] == 1
    assert state["repair_attempts"] < state["max_repair_attempts"]
    assert state["status"] == "max_steps_exceeded"
    assert route_after_tester(state) == "reviewer"


def test_tester_infrastructure_result_never_routes_to_model() -> None:
    for outcome in (
        TestOutcome.INTERRUPTED,
        TestOutcome.NO_TESTS_COLLECTED,
        TestOutcome.TIMEOUT,
        TestOutcome.OUTPUT_LIMIT_EXCEEDED,
        TestOutcome.UNKNOWN_EXIT_CODE,
    ):
        state, _context = _state()
        runner = ScriptedPytestRunner(
            [
                make_test_result(
                    outcome,
                    exit_code=None if outcome is TestOutcome.TIMEOUT else 5,
                    timed_out=outcome is TestOutcome.TIMEOUT,
                )
            ]
        )

        update = asyncio.run(TesterNode(runner)(state))
        state.update(update)

        assert route_after_tester(state) == "reviewer"
        assert runner.run_calls == 1


def test_missing_or_consumed_context_does_not_run_pytest() -> None:
    state, _context = _state()
    state["applied_patch_context"] = None
    runner = ScriptedPytestRunner([])

    update = asyncio.run(TesterNode(runner)(state))

    assert runner.run_calls == 0
    assert update["status"] == "test_infrastructure_error"


def test_unexpected_runner_exception_is_stable_infrastructure_result() -> None:
    state, _context = _state()

    class ExplodingRunner:
        target = "tests"
        command_display = "<python> -m pytest -q --tb=short tests"

        async def run(self):
            raise RuntimeError("private runner detail")

    update = asyncio.run(TesterNode(ExplodingRunner())(state))

    assert update["status"] == "test_infrastructure_error"
    assert update["repair_attempts"] == 1
    assert update["latest_test_result"]["outcome"] == "launch_error"
    assert "private runner detail" not in str(update)
