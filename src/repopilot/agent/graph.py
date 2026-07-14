"""Build the sole production P5 repair-and-verification execution engine."""

from collections.abc import Sequence
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from repopilot.agent.nodes import (
    ApplyPatchNode,
    ApprovalNode,
    FinalReportNode,
    ModelNode,
    RejectPatchNode,
    ReviewerNode,
    TesterNode,
    ToolNode,
)
from repopilot.agent.routing import (
    route_after_apply_patch,
    route_after_approval,
    route_after_model,
    route_after_patch_resolution,
    route_after_review,
    route_after_tester,
    route_after_tools,
)
from repopilot.agent.state import AgentState
from repopilot.patching.applicator import PatchApplicator
from repopilot.review.report import FinalReportBuilder
from repopilot.review.reviewer import DeterministicReviewer
from repopilot.testing.contracts import TestRunner
from repopilot.testing.pytest_runner import PytestRunner
from repopilot.tools.executor import SafeToolExecutor


def build_agent_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    executor: SafeToolExecutor,
    applicator: PatchApplicator,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    runner: TestRunner | None = None,
    reviewer: DeterministicReviewer | None = None,
    report_builder: FinalReportBuilder | None = None,
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """Bind tools once and compile the resumable approval graph."""

    tool_list = list(tools)
    tool_names = {tool.name for tool in tool_list}
    if len(tool_names) != len(tool_list):
        raise ValueError("tool names must be unique")

    bound_model = cast(Runnable[Any, BaseMessage], model.bind_tools(tool_list))
    resolved_runner = runner or PytestRunner(applicator.workspace_guard)
    resolved_reviewer = reviewer or DeterministicReviewer(applicator.workspace_guard)
    resolved_report_builder = report_builder or FinalReportBuilder()
    builder = StateGraph(AgentState)
    builder.add_node("model", ModelNode(bound_model))
    builder.add_node("tools", ToolNode(executor))
    builder.add_node("approval", ApprovalNode(resolved_runner.target))
    builder.add_node("apply_patch", ApplyPatchNode(applicator))
    builder.add_node("reject_patch", RejectPatchNode())
    builder.add_node("tester", TesterNode(resolved_runner))
    builder.add_node("reviewer", ReviewerNode(resolved_reviewer))
    builder.add_node("final_report", FinalReportNode(resolved_report_builder))
    builder.add_edge(START, "model")
    builder.add_conditional_edges(
        "model",
        route_after_model,
        {"tools": "tools", "reviewer": "reviewer", "final_report": "final_report"},
    )
    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {"model": "model", "approval": "approval", "reviewer": "reviewer"},
    )
    builder.add_conditional_edges(
        "approval",
        route_after_approval,
        {"apply_patch": "apply_patch", "reject_patch": "reject_patch"},
    )
    builder.add_conditional_edges(
        "apply_patch",
        route_after_apply_patch,
        {"tester": "tester", "model": "model", "reviewer": "reviewer"},
    )
    builder.add_conditional_edges(
        "reject_patch",
        route_after_patch_resolution,
        {"model": "model", "reviewer": "reviewer"},
    )
    builder.add_conditional_edges(
        "tester",
        route_after_tester,
        {"model": "model", "reviewer": "reviewer"},
    )
    builder.add_conditional_edges(
        "reviewer",
        route_after_review,
        {"final_report": "final_report"},
    )
    builder.add_edge("final_report", END)
    return builder.compile(
        name="repopilot-p5-agent",
        checkpointer=checkpointer or InMemorySaver(),
    )
