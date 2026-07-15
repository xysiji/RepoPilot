"""Run the current P2 graph offline with deterministic scripted messages."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from repopilot.agent.graph import build_agent_graph
from repopilot.patching.applicator import PatchApplicator
from repopilot.services.agent_service import AgentService
from repopilot.tools.executor import SafeToolExecutor
from repopilot.tools.policy import ToolSafetyPolicy, WorkspaceGuard
from repopilot.tools.readonly import build_readonly_tools


class DemoGraphModel(FakeMessagesListChatModel):
    """Accept tool binding while returning only local scripted messages."""

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
    model = DemoGraphModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _call(
                        "list_files",
                        {"directory": "sample_project", "recursive": True},
                        "demo-list",
                    )
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[_call("read_file", {"path": "sample_project/README.md"}, "demo-read")],
            ),
            AIMessage(content="Sample Project 是一个离线只读工具调用示例。"),
        ]
    )
    result = await AgentService(workspace, model, checkpointer=InMemorySaver()).run(
        "总结示例项目", max_steps=4
    )
    diagram_model = DemoGraphModel(responses=[AIMessage(content="unused")])
    guard = WorkspaceGuard(workspace)
    tools = build_readonly_tools(guard)
    executor = SafeToolExecutor(tools, ToolSafetyPolicy(guard))
    graph = build_agent_graph(
        diagram_model,
        tools,
        executor,
        PatchApplicator(guard),
        InMemorySaver(),
    )

    print("engine: langgraph")
    print("nodes: __start__ -> model -> tools -> model -> tools -> model -> __end__")
    print(f"status: {result.status}")
    print(f"model_calls: {result.steps}")
    print("tools: " + " -> ".join(item.tool_name for item in result.tool_executions))
    print(f"final_answer: {result.final_answer}")
    print("mermaid:")
    print(graph.get_graph().draw_mermaid())


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
