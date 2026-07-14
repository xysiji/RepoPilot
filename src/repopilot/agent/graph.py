"""Build the sole production P2 agent execution engine."""

from collections.abc import Sequence
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from repopilot.agent.nodes import ModelNode, ToolNode
from repopilot.agent.routing import route_after_model, route_after_tools
from repopilot.agent.state import AgentState
from repopilot.tools.executor import SafeToolExecutor


def build_agent_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    executor: SafeToolExecutor,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """Bind tools once, wire explicit nodes and edges, then compile without persistence."""

    tool_list = list(tools)
    tool_names = {tool.name for tool in tool_list}
    if len(tool_names) != len(tool_list):
        raise ValueError("tool names must be unique")

    bound_model = cast(Runnable[Any, BaseMessage], model.bind_tools(tool_list))
    builder = StateGraph(AgentState)
    builder.add_node("model", ModelNode(bound_model))
    builder.add_node("tools", ToolNode(executor))
    builder.add_edge(START, "model")
    builder.add_conditional_edges(
        "model",
        route_after_model,
        {"tools": "tools", END: END},
    )
    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {"model": "model", END: END},
    )
    return builder.compile(name="repopilot-p3-agent")
