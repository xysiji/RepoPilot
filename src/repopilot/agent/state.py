"""Explicit state contract for the P2 LangGraph agent."""

import operator
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages

from repopilot.schemas.agent import AgentRunError
from repopilot.tools.contracts import ToolExecutionRecord

AgentStatus = Literal[
    "running",
    "success",
    "max_steps_exceeded",
    "model_error",
    "invalid_model_response",
]
AgentError = AgentRunError


class AgentState(TypedDict):
    """Per-invocation graph state; no field is shared between requests."""

    messages: Annotated[list[BaseMessage], add_messages]
    model_calls: int
    max_steps: int
    status: AgentStatus
    final_answer: str | None
    error: AgentError | None
    tool_executions: Annotated[list[ToolExecutionRecord], operator.add]


def create_initial_state(goal: str, max_steps: int) -> AgentState:
    """Validate public run inputs and create fresh state containers."""

    if not goal.strip():
        raise ValueError("goal must not be empty")
    if not 1 <= max_steps <= 10:
        raise ValueError("max_steps must be between 1 and 10")

    return AgentState(
        messages=[HumanMessage(content=goal)],
        model_calls=0,
        max_steps=max_steps,
        status="running",
        final_answer=None,
        error=None,
        tool_executions=[],
    )
