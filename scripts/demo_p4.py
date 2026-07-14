"""Offline P4 interrupt/resume demo with separate approve and reject runs."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.callbacks.manager import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from repopilot.approval.contracts import ApprovalDecisionRequest
from repopilot.services.agent_service import AgentService


class DemoModel(BaseChatModel):
    """Minimal no-network model that returns a fixed patch call and final answer."""

    relative_path: str
    proposed_content: str
    responses: int = Field(default=0, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "repopilot-p4-demo-model"

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
        raise AssertionError("P4 demo uses async model invocation only")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        self.responses += 1
        if self.responses == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "propose_patch",
                        "args": {
                            "path": self.relative_path,
                            "new_content": self.proposed_content,
                            "rationale": "Demonstrate one reviewed P4 replacement",
                        },
                        "id": "demo-patch-call",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            message = AIMessage(content="The human decision was processed safely.")
        return ChatResult(generations=[ChatGeneration(message=message)])


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _demo(decision: str) -> None:
    with tempfile.TemporaryDirectory(prefix=f"repopilot-p4-{decision}-") as directory:
        workspace = Path(directory)
        target = workspace / "sample_project" / "src" / "example.py"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"VALUE = 1\n")
        before = _hash(target)
        model = DemoModel(
            relative_path="sample_project/src/example.py",
            proposed_content="VALUE = 2\n",
        )
        service = AgentService(workspace, model)

        pending = await service.start_run("Change the demo value", max_steps=3)
        assert pending.approval is not None
        print(f"[{decision}] nodes before pause: model -> tools -> approval(interrupt)")
        print(f"[{decision}] run_id={pending.run_id}")
        print(f"[{decision}] proposal_id={pending.approval.proposal_id}")
        print(f"[{decision}] before_hash={before}")
        print(f"[{decision}] paused_hash={_hash(target)}")
        print(pending.approval.unified_diff)

        completed = await service.resume_run(
            pending.run_id,
            ApprovalDecisionRequest(
                proposal_id=pending.approval.proposal_id,
                decision=decision,
            ),
        )
        resolution = "apply_patch" if decision == "approve" else "reject_patch"
        print(f"[{decision}] nodes after resume: approval -> {resolution} -> model -> END")
        print(f"[{decision}] final_status={completed.status}")
        print(f"[{decision}] final_hash={_hash(target)}")
        if decision == "approve":
            assert _hash(target) != before
        else:
            assert _hash(target) == before


async def main() -> None:
    await _demo("approve")
    await _demo("reject")


if __name__ == "__main__":
    asyncio.run(main())
