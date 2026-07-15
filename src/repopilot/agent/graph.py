"""Build the durable P6 repair-and-verification execution engine."""

from collections.abc import Sequence
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
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
from repopilot.context.manager import ContextManager
from repopilot.patching.applicator import PatchApplicator
from repopilot.review.report import FinalReportBuilder
from repopilot.review.reviewer import DeterministicReviewer
from repopilot.testing.contracts import TestRunner
from repopilot.testing.pytest_runner import PytestRunner
from repopilot.tools.executor import SafeToolExecutor
from repopilot.tracing.contracts import TraceEventType
from repopilot.tracing.nodes import traced_async_node, traced_sync_node
from repopilot.tracing.recorder import TraceRecorder


def build_agent_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    executor: SafeToolExecutor,
    applicator: PatchApplicator,
    checkpointer: BaseCheckpointSaver[str],
    context_manager: ContextManager | None = None,
    trace_recorder: TraceRecorder | None = None,
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
    model_node = ModelNode(bound_model, context_manager)
    tool_node = ToolNode(executor)
    approval_node = ApprovalNode(resolved_runner.target)
    apply_node = ApplyPatchNode(applicator)
    reject_node = RejectPatchNode()
    tester_node = TesterNode(resolved_runner)
    reviewer_node = ReviewerNode(resolved_reviewer)
    report_node = FinalReportNode(resolved_report_builder)
    builder = StateGraph(AgentState)
    if trace_recorder is None:
        builder.add_node("model", model_node)
        builder.add_node("tools", tool_node)
        builder.add_node("approval", approval_node)
        builder.add_node("apply_patch", apply_node)
        builder.add_node("reject_patch", reject_node)
        builder.add_node("tester", tester_node)
        builder.add_node("reviewer", reviewer_node)
        builder.add_node("final_report", report_node)
    else:
        builder.add_node(
            "model",
            traced_async_node(
                model_node,
                trace_recorder,
                node_name="model",
                event_type=TraceEventType.MODEL_COMPLETED,
            ),
        )
        builder.add_node(
            "tools",
            traced_sync_node(
                tool_node,
                trace_recorder,
                node_name="tools",
                event_type=TraceEventType.TOOL_COMPLETED,
            ),
        )
        builder.add_node(
            "approval",
            traced_sync_node(
                approval_node,
                trace_recorder,
                node_name="approval",
                event_type=TraceEventType.APPROVAL_DECIDED,
            ),
        )
        builder.add_node(
            "apply_patch",
            traced_sync_node(
                apply_node,
                trace_recorder,
                node_name="apply_patch",
                event_type=TraceEventType.PATCH_APPLIED,
            ),
        )
        builder.add_node(
            "reject_patch",
            traced_sync_node(
                reject_node,
                trace_recorder,
                node_name="reject_patch",
                event_type=TraceEventType.PATCH_REJECTED,
            ),
        )
        builder.add_node(
            "tester",
            traced_async_node(
                tester_node,
                trace_recorder,
                node_name="tester",
                event_type=TraceEventType.TESTS_COMPLETED,
            ),
        )
        builder.add_node(
            "reviewer",
            traced_sync_node(
                reviewer_node,
                trace_recorder,
                node_name="reviewer",
                event_type=TraceEventType.REVIEW_COMPLETED,
            ),
        )
        builder.add_node(
            "final_report",
            traced_sync_node(
                report_node,
                trace_recorder,
                node_name="final_report",
                event_type=TraceEventType.FINAL_REPORT_CREATED,
            ),
        )
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
        name="repopilot-p6-agent",
        checkpointer=checkpointer,
    )
