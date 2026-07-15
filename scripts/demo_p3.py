"""Demonstrate P3 policy denial, recovery, and success without network access."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from repopilot.services.agent_service import AgentService


class DemoSafetyModel(FakeMessagesListChatModel):
    """Accept local tool binding while returning deterministic scripted messages."""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        return self


def _call(name: str, args: dict[str, object], call_id: str) -> dict[str, object]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


async def run_demo() -> None:
    workspace = Path(__file__).resolve().parents[1] / "demo_workspace"
    model = DemoSafetyModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_call("read_file", {"path": ".env"}, "demo-denied")],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    _call(
                        "read_file",
                        {"path": "sample_project/README.md"},
                        "demo-allowed",
                    )
                ],
            ),
            AIMessage(content="安全策略拒绝了敏感文件，并允许读取示例 README。"),
        ]
    )
    result = await AgentService(workspace, model, checkpointer=InMemorySaver()).run(
        "安全地检查示例项目", max_steps=3
    )

    print("engine: langgraph-p3-safe-executor")
    print(f"status: {result.status}")
    print(f"model_calls: {result.steps}")
    for record in result.tool_executions:
        print(
            "tool: "
            f"name={record.tool_name} "
            f"phase={record.phase} "
            f"effect={record.effect} "
            f"policy_allowed={record.policy_allowed} "
            f"success={record.success} "
            f"error_code={record.error_code}"
        )
    print(f"final_answer: {result.final_answer}")


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
