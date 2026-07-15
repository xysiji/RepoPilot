"""Offline P6 restart, durable trace, and transient-context demonstration."""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.callbacks.manager import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from repopilot.approval.contracts import ApprovalDecisionRequest
from repopilot.context.contracts import ContextPolicy
from repopilot.context.manager import ContextManager
from repopilot.infrastructure.config import AppSettings
from repopilot.persistence.contracts import RunNotFoundError
from repopilot.persistence.lifecycle import PersistenceResources, open_persistence
from repopilot.services.agent_service import AgentService
from repopilot.testing.contracts import TestOutcome, TestRunResult
from repopilot.tracing.recorder import TraceRecorder


class DemoModel(BaseChatModel):
    """Derive the next patch from checkpointed test feedback, not an internal cursor."""

    received_messages: list[list[BaseMessage]] = Field(default_factory=list, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "p6-history-driven-demo"

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
        repairing = any(
            isinstance(message, ToolMessage) and "pytest_tests_failed" in str(message.content)
            for message in messages
        )
        number = 2 if repairing else 1
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "propose_patch",
                    "args": {
                        "path": "target.py",
                        "new_content": f"value = {number}\n",
                        "rationale": f"deterministic repair {number}",
                    },
                    "id": f"patch-{number}",
                    "type": "tool_call",
                }
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


class DemoRunner:
    target = "tests"
    command_display = "<python> -m pytest -q --tb=short tests"

    def __init__(self, outcome: TestOutcome) -> None:
        self._outcome = outcome

    async def run(self) -> TestRunResult:
        passed = self._outcome is TestOutcome.PASSED
        return TestRunResult(
            outcome=self._outcome,
            exit_code=0 if passed else 1,
            duration_ms=10,
            timed_out=False,
            output_truncated=False,
            safe_output_excerpt="passed" if passed else "one assertion failed",
            started_at="2026-07-14T00:00:00+00:00",
            finished_at="2026-07-14T00:00:00.010000+00:00",
        )


def _context_manager(maximum: int = 60_000) -> ContextManager:
    return ContextManager(
        ContextPolicy(
            max_characters=maximum,
            recent_blocks=2,
            tool_result_max_characters=128,
            summary_max_characters=200,
        )
    )


async def _service(
    settings: AppSettings, runner: DemoRunner
) -> tuple[PersistenceResources, AgentService, DemoModel]:
    resources = await open_persistence(settings)
    model = DemoModel()
    service = AgentService(
        settings.workspace_path,
        model,
        checkpointer=resources.checkpointer,
        runtime_store=resources.runtime_store,
        trace_recorder=TraceRecorder(resources.runtime_store, max_events_per_run=500),
        context_manager=_context_manager(2_000),
        runner=runner,
        max_repair_attempts=2,
    )
    return resources, service, model


def _compaction_stats() -> dict[str, int]:
    messages: list[BaseMessage] = [HumanMessage(content="goal")]
    for index in range(4):
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": f"file-{index}.py"},
                            "id": f"read-{index}",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content=json.dumps({"content": "x" * 900}),
                    tool_call_id=f"read-{index}",
                    name="read_file",
                ),
            ]
        )
    return _context_manager(2_000).build(messages).stats.model_dump(mode="json")


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="repopilot-p6-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        target = workspace / "target.py"
        target.write_text("value = 0\n", encoding="utf-8")
        settings = AppSettings(
            workspace_path=workspace,
            data_directory=root / "server-data",
            model_api_key=None,
            max_repair_attempts=2,
            _env_file=None,
        )

        resources_a, service_a, _ = await _service(settings, DemoRunner(TestOutcome.PASSED))
        first = await service_a.start_run("repair target", max_steps=4)
        assert first.approval is not None
        first_view = await service_a.get_run(first.run_id)
        first_events = await service_a.list_trace_events(first.run_id, limit=200, after=0)
        await resources_a.close()

        resources_b, service_b, model_b = await _service(
            settings, DemoRunner(TestOutcome.TEST_FAILURES)
        )
        restored = await service_b.get_run(first.run_id)
        second = await service_b.resume_run(
            first.run_id,
            ApprovalDecisionRequest(
                proposal_id=first.approval.proposal_id,
                decision="approve",
            ),
        )
        assert second.approval is not None
        await resources_b.close()

        resources_c, service_c, model_c = await _service(settings, DemoRunner(TestOutcome.PASSED))
        completed = await service_c.resume_run(
            first.run_id,
            ApprovalDecisionRequest(
                proposal_id=second.approval.proposal_id,
                decision="approve",
            ),
        )
        assert completed.final_report is not None
        final_view = await service_c.get_run(first.run_id)
        events = await service_c.list_trace_events(first.run_id, limit=200, after=0)
        event_types = [event.event_type for event in events.items]
        print("persistence_backend: sqlite")
        print("service_identities:", id(service_a), id(service_b), id(service_c))
        print("run_id_equals_thread_id:", first.run_id)
        print(
            "proposal_ids:",
            str(first.approval.proposal_id),
            str(second.approval.proposal_id),
        )
        print("service_a_status:", first.status)
        print("service_a_query_status:", first_view.status)
        print("service_a_trace_events:", len(first_events.items))
        print("service_b_restored:", restored.status)
        print("service_b_status:", second.status)
        print("service_b_model_calls:", len(model_b.received_messages))
        print("service_c_status:", completed.status)
        print("service_c_model_calls:", len(model_c.received_messages))
        print("final_file:", target.read_text(encoding="utf-8").strip())
        print("pytest_outcomes: test_failures -> passed")
        print("final_run_status:", final_view.status)
        print("final_report:", completed.final_report.model_dump_json())
        print("trace_event_types:", ",".join(event_types))
        print("context_stats:", json.dumps(_compaction_stats(), sort_keys=True))
        await service_c.delete_run(first.run_id)
        try:
            await service_c.get_run(first.run_id)
        except RunNotFoundError:
            print("delete_after_query: 404 run_not_found")
        await resources_c.close()


if __name__ == "__main__":
    asyncio.run(main())
