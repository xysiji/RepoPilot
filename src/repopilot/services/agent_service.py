"""Stateless composition service for one P3 LangGraph invocation."""

from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.errors import GraphRecursionError

from repopilot.agent.graph import build_agent_graph
from repopilot.agent.state import create_initial_state
from repopilot.schemas.agent import AgentRunError, AgentRunResult
from repopilot.tools.executor import SafeToolExecutor
from repopilot.tools.policy import ToolSafetyPolicy, WorkspaceGuard
from repopilot.tools.readonly import build_readonly_tools


class AgentService:
    """Own one reusable compiled graph while creating fresh state for every run."""

    def __init__(self, workspace_path: str | Path, model: BaseChatModel) -> None:
        workspace_guard = WorkspaceGuard(workspace_path)
        tools = build_readonly_tools(workspace_guard)
        executor = SafeToolExecutor(tools, ToolSafetyPolicy(workspace_guard))
        self._graph = None
        self._build_error: str | None = None
        try:
            self._graph = build_agent_graph(model, tools, executor)
        except Exception as exc:
            self._build_error = type(exc).__name__

    async def run(self, goal: str, *, max_steps: int) -> AgentRunResult:
        initial_state = create_initial_state(goal, max_steps)
        if self._graph is None:
            return AgentRunResult(
                status="model_error",
                steps=0,
                message_count=len(initial_state["messages"]),
                error=AgentRunError(
                    code="model_error",
                    message=f"Model tool binding failed: {self._build_error}",
                ),
            )

        try:
            final_state = await self._graph.ainvoke(
                initial_state,
                config={"recursion_limit": max(10, max_steps * 2 + 4)},
            )
        except GraphRecursionError:
            return AgentRunResult(
                status="invalid_model_response",
                steps=0,
                message_count=len(initial_state["messages"]),
                error=AgentRunError(
                    code="invalid_model_response",
                    message="Agent graph exceeded its defensive recursion limit",
                ),
            )

        return AgentRunResult(
            status=final_state["status"],
            final_answer=final_state["final_answer"] or "",
            steps=final_state["model_calls"],
            tool_executions=final_state["tool_executions"],
            message_count=len(final_state["messages"]),
            error=final_state["error"],
        )
