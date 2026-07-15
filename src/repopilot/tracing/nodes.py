"""Small node decorators that emit idempotent business trace events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from repopilot.agent.state import AgentState
from repopilot.tracing.contracts import TraceEvent, TraceEventType
from repopilot.tracing.recorder import TraceRecorder

AsyncNode = Callable[[AgentState], Awaitable[dict[str, Any]]]
SyncNode = Callable[[AgentState], dict[str, Any]]


def traced_async_node(
    node: AsyncNode,
    recorder: TraceRecorder,
    *,
    node_name: str,
    event_type: TraceEventType,
) -> AsyncNode:
    async def invoke(state: AgentState) -> dict[str, Any]:
        update = await node(state)
        await _record_node_update(recorder, state, update, node_name, event_type)
        return update

    return invoke


def traced_sync_node(
    node: SyncNode,
    recorder: TraceRecorder,
    *,
    node_name: str,
    event_type: TraceEventType,
) -> AsyncNode:
    async def invoke(state: AgentState) -> dict[str, Any]:
        if node_name == "approval":
            proposal = state.get("pending_approval")
            proposal_id = proposal.get("proposal_id") if isinstance(proposal, dict) else None
            await recorder.record(
                TraceEvent(
                    event_key=f"approval_requested:{proposal_id or 'missing'}",
                    run_id=state["run_id"],
                    event_type=TraceEventType.APPROVAL_REQUESTED,
                    node_name=node_name,
                    phase="approval",
                    status="awaiting_approval",
                    payload={"proposal_id": proposal_id},
                )
            )
        update = node(state)
        await _record_node_update(recorder, state, update, node_name, event_type)
        return update

    return invoke


async def _record_node_update(
    recorder: TraceRecorder,
    state: AgentState,
    update: dict[str, Any],
    node_name: str,
    event_type: TraceEventType,
) -> None:
    model_calls = int(update.get("model_calls", state.get("model_calls", 0)))
    repair_attempts = int(update.get("repair_attempts", state.get("repair_attempts", 0)))
    proposal = state.get("pending_approval")
    proposal_id = proposal.get("proposal_id") if isinstance(proposal, dict) else None
    if node_name == "tools":
        proposal = update.get("pending_approval")
        proposal_id = proposal.get("proposal_id") if isinstance(proposal, dict) else None
    suffix = {
        "model": str(model_calls),
        "tools": str(model_calls),
        "approval": str(proposal_id or "missing"),
        "apply_patch": str(proposal_id or "missing"),
        "reject_patch": str(proposal_id or "missing"),
        "tester": f"{proposal_id or 'missing'}:{repair_attempts}",
        "reviewer": f"{repair_attempts}:{state.get('status', 'unknown')}",
        "final_report": str(update.get("status", state.get("status", "unknown"))),
    }[node_name]
    payload: dict[str, Any] = {
        "model_calls": model_calls,
        "repair_attempts": repair_attempts,
        "proposal_id": proposal_id,
        "status": str(update.get("status", state.get("status", "running"))),
    }
    if node_name == "tools":
        executions = update.get("tool_executions")
        if isinstance(executions, list) and executions:
            for execution in executions:
                if not isinstance(execution, dict):
                    continue
                tool_call_id = str(execution.get("tool_call_id", "missing"))
                await recorder.record(
                    TraceEvent(
                        event_key=f"tool_completed:{tool_call_id}",
                        run_id=state["run_id"],
                        event_type=TraceEventType.TOOL_COMPLETED,
                        node_name="tools",
                        phase=str(execution.get("phase", "tools")),
                        status=("success" if execution.get("success") else "error"),
                        payload={
                            "model_calls": model_calls,
                            "tool_call_id": tool_call_id,
                            "tool_name": execution.get("tool_name"),
                            "phase": execution.get("phase"),
                            "failure_category": execution.get("failure_category"),
                            "error_code": execution.get("error_code"),
                        },
                    )
                )
        return
    if node_name == "tester":
        applied = state.get("applied_patch_context")
        if isinstance(applied, dict):
            proposal_id = applied.get("proposal_id")
            suffix = f"{proposal_id or 'missing'}:{repair_attempts}"
    if node_name == "model":
        stats = update.get("latest_context_stats")
        if isinstance(stats, dict):
            payload.update(
                {
                    "model_message_count": stats.get("model_message_count"),
                    "original_message_count": stats.get("original_message_count"),
                    "original_characters": stats.get("original_characters"),
                    "model_characters": stats.get("model_characters"),
                    "compacted_block_count": stats.get("compacted_block_count"),
                    "dropped_block_count": stats.get("dropped_block_count"),
                    "tool_results_compacted": stats.get("tool_results_compacted"),
                }
            )
            if int(stats.get("compacted_block_count", 0)) or int(
                stats.get("dropped_block_count", 0)
            ):
                await recorder.record(
                    TraceEvent(
                        event_key=f"context_compacted:{model_calls}",
                        run_id=state["run_id"],
                        event_type=TraceEventType.CONTEXT_COMPACTED,
                        node_name="model",
                        phase="context",
                        status="compacted",
                        payload=payload,
                    )
                )
    if node_name == "approval":
        decision = update.get("approval_decision")
        if isinstance(decision, dict):
            payload["decision"] = decision.get("decision")
            suffix = f"{proposal_id or 'missing'}:{decision.get('decision', 'invalid')}"
    latest_test = update.get("latest_test_result")
    if isinstance(latest_test, dict):
        payload["latest_test_outcome"] = latest_test.get("outcome")
    review = update.get("review_result")
    if isinstance(review, dict):
        payload["review_status"] = review.get("status")
    await recorder.record(
        TraceEvent(
            event_key=f"{event_type.value}:{suffix}",
            run_id=state["run_id"],
            event_type=event_type,
            node_name=node_name,
            phase=node_name,
            status=str(payload["status"]),
            payload=payload,
        )
    )
