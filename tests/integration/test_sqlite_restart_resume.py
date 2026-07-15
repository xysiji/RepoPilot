"""True P6 restart tests using new services over the same on-disk databases."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.callbacks.manager import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from repopilot.approval.contracts import ApprovalDecisionRequest, ApprovalServiceError
from repopilot.context.contracts import ContextPolicy
from repopilot.context.manager import ContextManager
from repopilot.infrastructure.config import AppSettings
from repopilot.persistence.lifecycle import PersistenceResources, open_persistence
from repopilot.services.agent_service import AgentService
from repopilot.testing.contracts import TestOutcome
from repopilot.tools.contracts import ToolErrorCode
from repopilot.tracing.recorder import TraceRecorder
from tests.fake_runner import ScriptedPytestRunner, make_test_result
from tests.scripted_model import ScriptedToolCallingModel


def _call(content: str, call_id: str) -> dict[str, object]:
    return {
        "name": "propose_patch",
        "args": {"path": "target.py", "new_content": content, "rationale": "repair"},
        "id": call_id,
        "type": "tool_call",
    }


class HistoryDrivenRepairModel(BaseChatModel):
    """Choose the second patch from persisted ToolMessages, never an internal cursor."""

    received_messages: list[list[BaseMessage]] = Field(default_factory=list, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "history-driven-restart-test-model"

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        del tools, tool_choice, kwargs
        return self

    def _answer(self, messages: list[BaseMessage]) -> AIMessage:
        self.received_messages.append(list(messages))
        has_failed_test = any(
            isinstance(message, ToolMessage) and "pytest_tests_failed" in str(message.content)
            for message in messages
        )
        return AIMessage(
            content="",
            tool_calls=[
                _call("value = 2\n", "patch-2")
                if has_failed_test
                else _call("value = 1\n", "patch-1")
            ],
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._answer(messages))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._answer(messages))])


def _settings(tmp_path: Path) -> AppSettings:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return AppSettings(
        workspace_path=workspace,
        data_directory=tmp_path / "server-data",
        model_api_key=None,
        max_repair_attempts=2,
        _env_file=None,
    )


async def _service(
    settings: AppSettings,
    model: BaseChatModel,
    runner: ScriptedPytestRunner,
) -> tuple[PersistenceResources, AgentService]:
    resources = await open_persistence(settings)
    context = ContextManager(
        ContextPolicy(
            max_characters=60_000,
            recent_blocks=8,
            tool_result_max_characters=4_000,
            summary_max_characters=2_000,
        )
    )
    service = AgentService(
        settings.workspace_path,
        model,
        checkpointer=resources.checkpointer,
        runtime_store=resources.runtime_store,
        trace_recorder=TraceRecorder(resources.runtime_store, max_events_per_run=500),
        context_manager=context,
        runner=runner,
        max_repair_attempts=2,
    )
    return resources, service


def test_pending_approval_resumes_after_connections_graph_service_and_locks_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        target = settings.workspace_path / "target.py"
        target.write_text("value = 0\n", encoding="utf-8")
        model_a = ScriptedToolCallingModel(
            responses=[AIMessage(content="", tool_calls=[_call("value = 1\n", "patch-1")])]
        )
        resources_a, service_a = await _service(
            settings,
            model_a,
            ScriptedPytestRunner([make_test_result(TestOutcome.PASSED, exit_code=0)]),
        )
        pending = await service_a.start_run("repair target", max_steps=3)
        assert pending.status == "awaiting_approval" and pending.approval is not None
        await resources_a.close()

        model_b = ScriptedToolCallingModel(responses=[AIMessage(content="unused")])
        runner_b = ScriptedPytestRunner([make_test_result(TestOutcome.PASSED, exit_code=0)])
        resources_b, service_b = await _service(settings, model_b, runner_b)
        restored = await service_b.get_run(pending.run_id)
        assert restored.awaiting_approval is True
        assert restored.approval is not None
        assert restored.approval.proposal_id == pending.approval.proposal_id
        assert restored.approval.tool_call_id == pending.approval.tool_call_id
        completed = await service_b.resume_run(
            pending.run_id,
            ApprovalDecisionRequest(
                proposal_id=pending.approval.proposal_id,
                decision="approve",
            ),
        )
        assert completed.status == "repaired"
        assert target.read_text(encoding="utf-8") == "value = 1\n"
        assert model_b.received_messages == []
        events = await service_b.list_trace_events(pending.run_id, limit=200, after=0)
        assert [item.event_type for item in events.items] == [
            "run_started",
            "model_completed",
            "approval_requested",
            "run_resumed",
            "approval_decided",
            "patch_applied",
            "tests_completed",
            "review_completed",
            "final_report_created",
            "run_completed",
        ]
        await resources_b.close()

    asyncio.run(scenario())


def test_second_patch_approval_resumes_after_failed_test_and_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        target = settings.workspace_path / "target.py"
        target.write_text("value = 0\n", encoding="utf-8")
        resources_a, service_a = await _service(
            settings,
            HistoryDrivenRepairModel(),
            ScriptedPytestRunner(
                [make_test_result(TestOutcome.TEST_FAILURES, exit_code=1, output="failed")]
            ),
        )
        first = await service_a.start_run("repair twice", max_steps=4)
        assert first.approval is not None
        second = await service_a.resume_run(
            first.run_id,
            ApprovalDecisionRequest(
                proposal_id=first.approval.proposal_id,
                decision="approve",
            ),
        )
        assert second.status == "awaiting_approval" and second.approval is not None
        assert second.approval.tool_call_id == "patch-2"
        await resources_a.close()

        restarted_model = HistoryDrivenRepairModel()
        resources_b, service_b = await _service(
            settings,
            restarted_model,
            ScriptedPytestRunner([make_test_result(TestOutcome.PASSED, exit_code=0)]),
        )
        completed = await service_b.resume_run(
            first.run_id,
            ApprovalDecisionRequest(
                proposal_id=second.approval.proposal_id,
                decision="approve",
            ),
        )
        assert completed.status == "repaired"
        assert completed.final_report is not None
        assert completed.final_report.repair_attempts == 2
        assert target.read_text(encoding="utf-8") == "value = 2\n"
        assert restarted_model.received_messages == []
        assert service_b._graph is not None
        snapshot = await service_b._graph.aget_state({"configurable": {"thread_id": first.run_id}})
        assert snapshot.values["repair_attempts"] == 2
        assert len(snapshot.values["test_runs"]) == 2
        assert len(snapshot.values["applied_patches"]) == 2
        await resources_b.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_version", [None, 99])
def test_incompatible_checkpoint_is_not_applied_or_tested(
    tmp_path: Path, bad_version: int | None
) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        target = settings.workspace_path / "target.py"
        target.write_text("value = 0\n", encoding="utf-8")
        runner = ScriptedPytestRunner([make_test_result(TestOutcome.PASSED, exit_code=0)])
        resources, service = await _service(
            settings,
            ScriptedToolCallingModel(
                responses=[AIMessage(content="", tool_calls=[_call("value = 1\n", "patch-1")])]
            ),
            runner,
        )
        pending = await service.start_run("repair", max_steps=3)
        assert pending.approval is not None and service._graph is not None
        await service._graph.aupdate_state(
            {"configurable": {"thread_id": pending.run_id}},
            {"state_schema_version": bad_version},
        )
        try:
            await service.resume_run(
                pending.run_id,
                ApprovalDecisionRequest(
                    proposal_id=pending.approval.proposal_id,
                    decision="approve",
                ),
            )
        except ApprovalServiceError as exc:
            assert exc.code is ToolErrorCode.CHECKPOINT_INCOMPATIBLE
        else:
            raise AssertionError("incompatible checkpoint unexpectedly resumed")
        assert target.read_text(encoding="utf-8") == "value = 0\n"
        assert runner.run_calls == 0
        await resources.close()

    asyncio.run(scenario())
