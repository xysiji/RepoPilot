"""Explicit state contract for the P5 repair-and-verification graph."""

import operator
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages

from repopilot.schemas.agent import AgentRunError

AgentStatus = Literal[
    "running",
    "success",
    "max_steps_exceeded",
    "model_error",
    "invalid_model_response",
    "awaiting_approval",
    "repair_attempts_exhausted",
    "repair_abandoned",
    "test_timeout",
    "test_infrastructure_error",
    "patch_apply_failed",
    "approval_rejected",
    "repaired",
    "no_change",
    "tests_failed",
]
AgentError = AgentRunError


class AgentState(TypedDict):
    """Per-invocation graph state; no field is shared between requests."""

    run_id: str
    messages: Annotated[list[BaseMessage], add_messages]
    model_calls: int
    max_steps: int
    status: AgentStatus
    final_answer: str | None
    error: AgentError | None
    tool_executions: Annotated[list[dict[str, object]], operator.add]
    pending_approval: dict[str, object] | None
    approval_decision: dict[str, object] | None
    repair_attempts: int
    max_repair_attempts: int
    test_runs: Annotated[list[dict[str, object]], operator.add]
    latest_test_result: dict[str, object] | None
    applied_patch_context: dict[str, object] | None
    applied_patches: Annotated[list[dict[str, object]], operator.add]
    review_result: dict[str, object] | None
    final_report: dict[str, object] | None
    approval_count: int
    model_final_text: str
    last_patch_error_code: str | None


def create_initial_state(
    goal: str,
    max_steps: int,
    run_id: str = "local-run",
    max_repair_attempts: int = 3,
) -> AgentState:
    """Validate public run inputs and create fresh state containers."""

    if not goal.strip():
        raise ValueError("goal must not be empty")
    if not 1 <= max_steps <= 10:
        raise ValueError("max_steps must be between 1 and 10")
    if not 1 <= max_repair_attempts <= 5:
        raise ValueError("max_repair_attempts must be between 1 and 5")

    return AgentState(
        run_id=run_id,
        messages=[HumanMessage(content=goal)],
        model_calls=0,
        max_steps=max_steps,
        status="running",
        final_answer=None,
        error=None,
        tool_executions=[],
        pending_approval=None,
        approval_decision=None,
        repair_attempts=0,
        max_repair_attempts=max_repair_attempts,
        test_runs=[],
        latest_test_result=None,
        applied_patch_context=None,
        applied_patches=[],
        review_result=None,
        final_report=None,
        approval_count=0,
        model_final_text="",
        last_patch_error_code=None,
    )
