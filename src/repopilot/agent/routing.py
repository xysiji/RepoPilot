"""Pure conditional routing for the P6 repair-and-verification graph."""

from typing import Literal

from langchain_core.messages import AIMessage

from repopilot.agent.state import AgentState
from repopilot.approval.contracts import ApprovalDecision, ApprovalDecisionKind
from repopilot.patching.proposal import PatchProposal
from repopilot.testing.contracts import TestOutcome, TestRunResult

AfterModelRoute = Literal["tools", "reviewer", "final_report"]
AfterToolsRoute = Literal["model", "approval", "reviewer"]
AfterApprovalRoute = Literal["apply_patch", "reject_patch"]
AfterApplyRoute = Literal["tester", "model", "reviewer"]
AfterPatchResolutionRoute = Literal["model", "reviewer"]
AfterTesterRoute = Literal["model", "reviewer"]
AfterReviewRoute = Literal["final_report"]


def route_after_model(state: AgentState) -> AfterModelRoute:
    """Send tool calls to Tools, direct no-change answers to Report, failures to Review."""

    latest = state["messages"][-1] if state["messages"] else None
    if state["status"] == "running" and isinstance(latest, AIMessage) and latest.tool_calls:
        return "tools"
    if state["status"] == "success":
        return "final_report"
    return "reviewer"


def route_after_tools(state: AgentState) -> AfterToolsRoute:
    """Continue read-only work, interrupt for Patch, or review a terminal state."""

    if state["status"] == "awaiting_approval" and state["pending_approval"] is not None:
        return "approval"
    return "model" if state["status"] == "running" else "reviewer"


def route_after_approval(state: AgentState) -> AfterApprovalRoute:
    """Route only a valid matching approval to the writer node."""

    raw_decision = state["approval_decision"]
    try:
        decision = ApprovalDecision.model_validate(raw_decision) if raw_decision else None
    except (TypeError, ValueError):
        decision = None
    raw_proposal = state["pending_approval"]
    try:
        proposal = PatchProposal.model_validate(raw_proposal) if raw_proposal else None
    except (TypeError, ValueError):
        proposal = None
    if (
        decision is not None
        and proposal is not None
        and decision.valid
        and decision.proposal_id == proposal.proposal_id
        and decision.decision is ApprovalDecisionKind.APPROVE
    ):
        return "apply_patch"
    return "reject_patch"


def route_after_apply_patch(state: AgentState) -> AfterApplyRoute:
    """A successful Apply must reach Tester before any next model invocation."""

    if state["applied_patch_context"] is not None:
        return "tester"
    return "model" if state["status"] == "running" else "reviewer"


def route_after_patch_resolution(state: AgentState) -> AfterPatchResolutionRoute:
    """Return immediate Apply/reject errors to the model only while budget remains."""

    return "model" if state["status"] == "running" else "reviewer"


def route_after_tester(state: AgentState) -> AfterTesterRoute:
    """Only exit-code-1 failures with both independent budgets may return to the model."""

    result = _test_result(state["latest_test_result"])
    if (
        result is not None
        and result.outcome is TestOutcome.TEST_FAILURES
        and state["status"] == "running"
        and state["repair_attempts"] < state["max_repair_attempts"]
        and state["model_calls"] < state["max_steps"]
    ):
        return "model"
    return "reviewer"


def route_after_review(state: AgentState) -> AfterReviewRoute:
    """Every deterministic review is projected into one final report."""

    return "final_report"


def _test_result(raw: object) -> TestRunResult | None:
    if raw is None:
        return None
    try:
        return TestRunResult.model_validate(raw)
    except (TypeError, ValueError):
        return None
