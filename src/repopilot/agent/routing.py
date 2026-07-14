"""Pure conditional routing for the P2 agent graph."""

from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.graph import END

from repopilot.agent.state import AgentState

AfterModelRoute = Literal["tools", "__end__"]
AfterToolsRoute = Literal["model", "__end__"]


def route_after_model(state: AgentState) -> AfterModelRoute:
    """Continue only when a successful model node produced tool calls."""

    if state["status"] != "running":
        return END
    latest = state["messages"][-1] if state["messages"] else None
    if isinstance(latest, AIMessage) and latest.tool_calls:
        return "tools"
    return END


def route_after_tools(state: AgentState) -> AfterToolsRoute:
    """Return to the model unless the tool node established a terminal status."""

    return "model" if state["status"] == "running" else END
