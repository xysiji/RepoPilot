"""Run the P1 list -> read -> answer flow without a network model."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from repopilot.services.agent_service import AgentService


class DemoToolCallingModel(FakeMessagesListChatModel):
    """Only adds bind_tools support required by the offline scripted demo."""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        return self


def main() -> None:
    workspace = Path(__file__).resolve().parents[1] / "demo_workspace"
    model = DemoToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_files",
                        "args": {"directory": "sample_project", "recursive": True},
                        "id": "demo-list",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": "sample_project/README.md"},
                        "id": "demo-read",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Sample Project 展示了只读工具调用，并包含一个简单问候函数。"),
        ]
    )
    result = AgentService(workspace).run(
        "总结 sample_project/README.md",
        model=model,
        max_steps=4,
    )

    print(f"status: {result.status}")
    print("tools: " + " -> ".join(item.tool_name for item in result.tool_executions))
    print(f"answer: {result.final_answer}")


if __name__ == "__main__":
    main()
