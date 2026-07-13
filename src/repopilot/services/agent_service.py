"""Stateless composition service for one P1 agent run."""

from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel

from repopilot.agent.loop import ToolCallingLoop
from repopilot.schemas.agent import AgentRunResult
from repopilot.tools.readonly import build_readonly_tools


class AgentService:
    """Create fresh messages and tools for every independent invocation."""

    def __init__(self, workspace_path: str | Path) -> None:
        self._workspace_path = workspace_path

    def run(self, goal: str, *, model: BaseChatModel, max_steps: int) -> AgentRunResult:
        tools = build_readonly_tools(self._workspace_path)
        return ToolCallingLoop().run(
            goal,
            model=model,
            tools=tools,
            max_steps=max_steps,
        )
