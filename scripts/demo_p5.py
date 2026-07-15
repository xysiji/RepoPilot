"""Offline P5 two-approval repair loop using the real fixed PytestRunner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.callbacks.manager import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from repopilot.approval.contracts import ApprovalDecisionRequest
from repopilot.services.agent_service import AgentService
from repopilot.testing.contracts import TestRunResult
from repopilot.testing.pytest_runner import PytestRunner
from repopilot.tools.policy import WorkspaceGuard


class DemoModel(BaseChatModel):
    """Two deterministic patch proposals with no network access."""

    patches: list[str]
    calls: int = Field(default=0, exclude=True)
    saw_failed_test_feedback: bool = Field(default=False, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "repopilot-p5-offline-demo"

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        del tools, tool_choice, kwargs
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        del messages, kwargs
        raise AssertionError("P5 demo uses async model invocation only")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        if self.calls:
            self.saw_failed_test_feedback = any(
                isinstance(message, ToolMessage)
                and json.loads(str(message.content))["data"].get("test_outcome") == "test_failures"
                for message in messages
            )
        content = self.patches[self.calls]
        self.calls += 1
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "propose_patch",
                    "args": {
                        "path": "src/calculator.py",
                        "new_content": content,
                        "rationale": f"P5 deterministic patch attempt {self.calls}",
                    },
                    "id": f"p5-demo-patch-{self.calls}",
                    "type": "tool_call",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


class RecordingRunner:
    """Demo-only observer around the real fixed runner."""

    def __init__(self, runner: PytestRunner) -> None:
        self._runner = runner
        self.target = runner.target
        self.command_display = runner.command_display
        self.results: list[TestRunResult] = []

    async def run(self) -> TestRunResult:
        result = await self._runner.run()
        self.results.append(result)
        return result


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workspace(parent: Path) -> tuple[Path, Path]:
    workspace = parent / "sample_project"
    target = workspace / "src" / "calculator.py"
    target.parent.mkdir(parents=True)
    target.write_text("def add(left, right):\n    return left - right\n", encoding="utf-8")
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "test_calculator.py").write_text(
        "from src.calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    return workspace, target


async def _approve(service: AgentService, pending):
    assert pending.approval is not None
    print(f"approval proposal_id={pending.approval.proposal_id}")
    print(pending.approval.unified_diff)
    return await service.resume_run(
        pending.run_id,
        ApprovalDecisionRequest(
            proposal_id=pending.approval.proposal_id,
            decision="approve",
        ),
    )


async def _two_round_demo(parent: Path) -> None:
    workspace, target = _workspace(parent)
    first_patch = "def add(left, right):\n    return left * right\n"
    second_patch = "def add(left, right):\n    return left + right\n"
    model = DemoModel(patches=[first_patch, second_patch])
    runner = RecordingRunner(PytestRunner(WorkspaceGuard(workspace)))
    service = AgentService(
        workspace,
        model,
        checkpointer=InMemorySaver(),
        runner=runner,
        max_repair_attempts=2,
    )

    before = _hash(target)
    first_pending = await service.start_run(
        "Repair calculator addition",
        max_steps=4,
        max_repair_attempts=2,
    )
    print("nodes: START -> model -> tools -> approval(interrupt)")
    print(f"hash before first Apply={before}")
    second_pending = await _approve(service, first_pending)
    print(
        "nodes: approval -> apply_patch -> tester(exit 1) -> model -> tools -> approval(interrupt)"
    )
    print(f"hash after first Apply={_hash(target)}")
    print(
        f"test attempt=1 command={runner.command_display} "
        f"exit_code={runner.results[0].exit_code} outcome={runner.results[0].outcome.value}"
    )
    completed = await _approve(service, second_pending)
    print("nodes: approval -> apply_patch -> tester(exit 0) -> reviewer -> final_report -> END")
    print(f"hash after second Apply={_hash(target)}")
    print(
        f"test attempt=2 command={runner.command_display} "
        f"exit_code={runner.results[1].exit_code} outcome={runner.results[1].outcome.value}"
    )
    assert completed.final_report is not None
    print(f"repair_attempts={completed.final_report.repair_attempts}")
    print(f"review_status={completed.final_report.review_status}")
    print(f"final_report={completed.final_report.model_dump_json()}")
    print(f"real_model_network_calls=0 feedback_seen={model.saw_failed_test_feedback}")


async def _exhausted_demo(parent: Path) -> None:
    workspace, _target = _workspace(parent)
    model = DemoModel(patches=["def add(left, right):\n    return left * right\n"])
    runner = RecordingRunner(PytestRunner(WorkspaceGuard(workspace)))
    service = AgentService(
        workspace,
        model,
        checkpointer=InMemorySaver(),
        runner=runner,
        max_repair_attempts=1,
    )
    pending = await service.start_run(
        "Repair calculator addition",
        max_steps=4,
        max_repair_attempts=1,
    )
    completed = await _approve(service, pending)
    assert completed.final_report is not None
    print(
        "exhausted: tester(exit 1) -> reviewer -> final_report -> END; "
        f"status={completed.status} model_calls={model.calls}"
    )


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="repopilot-p5-demo-") as directory:
        parent = Path(directory)
        first = parent / "two-round"
        first.mkdir()
        await _two_round_demo(first)
        exhausted = parent / "exhausted"
        exhausted.mkdir()
        await _exhausted_demo(exhausted)


if __name__ == "__main__":
    asyncio.run(main())
