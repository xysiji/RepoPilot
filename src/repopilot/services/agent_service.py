"""Durable P6 start, resume, query, and cleanup service boundary."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from repopilot.agent.graph import build_agent_graph
from repopilot.agent.state import (
    CURRENT_STATE_SCHEMA_VERSION,
    AgentState,
    create_initial_state,
)
from repopilot.approval.contracts import (
    ApprovalDecisionRequest,
    ApprovalServiceError,
    approval_view,
)
from repopilot.context.contracts import ContextPolicy
from repopilot.context.manager import ContextManager
from repopilot.patching.applicator import PatchApplicator
from repopilot.patching.proposal import PatchProposal, PatchProposalBuilder
from repopilot.persistence.contracts import (
    TERMINAL_RUN_STATUSES,
    RunCleanupError,
    RunNotFoundError,
    RunNotTerminalError,
    RunPage,
    RunRecord,
    TraceRecord,
)
from repopilot.persistence.runtime_store import RuntimeStore
from repopilot.schemas.agent import (
    AgentRunError,
    AgentRunListResponse,
    AgentRunResult,
    AgentRunSummary,
    AgentRunView,
    TraceEventListResponse,
    TraceEventView,
)
from repopilot.testing.contracts import TestRunner
from repopilot.testing.pytest_runner import PytestRunner
from repopilot.tools.contracts import ToolErrorCode
from repopilot.tools.executor import SafeToolExecutor
from repopilot.tools.patch import build_patch_tool
from repopilot.tools.policy import ToolSafetyPolicy, WorkspaceGuard
from repopilot.tools.readonly import build_readonly_tools
from repopilot.tracing.contracts import TraceEvent, TraceEventType
from repopilot.tracing.recorder import TraceRecorder


class AgentService:
    """Own one compiled graph while durable facts remain restart-recoverable."""

    def __init__(
        self,
        workspace_path: str | Path,
        model: BaseChatModel,
        *,
        checkpointer: BaseCheckpointSaver[str],
        runtime_store: RuntimeStore | None = None,
        trace_recorder: TraceRecorder | None = None,
        context_manager: ContextManager | None = None,
        applicator: PatchApplicator | None = None,
        runner: TestRunner | None = None,
        pytest_target: str = "tests",
        pytest_timeout_seconds: float = 60.0,
        pytest_max_output_characters: int = 20_000,
        max_repair_attempts: int = 3,
        run_retention_days: int = 30,
        trace_retention_days: int = 30,
        known_secrets: tuple[str, ...] = (),
    ) -> None:
        workspace_guard = WorkspaceGuard(workspace_path)
        tools = [*build_readonly_tools(workspace_guard), build_patch_tool()]
        proposal_builder = PatchProposalBuilder(workspace_guard)
        resolved_applicator = applicator or PatchApplicator(workspace_guard)
        resolved_runner = runner or PytestRunner(
            workspace_guard,
            target=pytest_target,
            timeout_seconds=pytest_timeout_seconds,
            max_output_characters=pytest_max_output_characters,
            known_secrets=known_secrets,
        )
        executor = SafeToolExecutor(tools, ToolSafetyPolicy(workspace_guard), proposal_builder)
        if not 1 <= max_repair_attempts <= 5:
            raise ValueError("max_repair_attempts must be between 1 and 5")
        if run_retention_days < 1 or trace_retention_days < 1:
            raise ValueError("retention days must be positive")
        self._checkpointer = checkpointer
        self._runtime_store = runtime_store
        self._trace = trace_recorder
        self._max_repair_attempts = max_repair_attempts
        self._run_retention_days = run_retention_days
        self._trace_retention_days = trace_retention_days
        self._pytest_target = resolved_runner.target
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._graph = None
        self._build_error: str | None = None
        resolved_context = context_manager or ContextManager(
            ContextPolicy(
                max_characters=60_000,
                recent_blocks=8,
                tool_result_max_characters=4_000,
                summary_max_characters=2_000,
            )
        )
        try:
            self._graph = build_agent_graph(
                model,
                tools,
                executor,
                resolved_applicator,
                checkpointer,
                resolved_context,
                trace_recorder,
                resolved_runner,
            )
        except Exception as exc:
            self._build_error = type(exc).__name__

    async def run(
        self,
        goal: str,
        *,
        max_steps: int,
        max_repair_attempts: int | None = None,
    ) -> AgentRunResult:
        return await self.start_run(
            goal,
            max_steps=max_steps,
            max_repair_attempts=max_repair_attempts,
        )

    async def start_run(
        self,
        goal: str,
        *,
        max_steps: int,
        max_repair_attempts: int | None = None,
    ) -> AgentRunResult:
        run_id = str(uuid4())
        requested_attempts = (
            self._max_repair_attempts if max_repair_attempts is None else max_repair_attempts
        )
        if requested_attempts > self._max_repair_attempts:
            raise ValueError("max_repair_attempts exceeds the configured system limit")
        initial_state = create_initial_state(goal, max_steps, run_id, requested_attempts)
        if self._runtime_store is not None:
            await self._runtime_store.create_run(
                run_id=run_id,
                thread_id=run_id,
                state_schema_version=CURRENT_STATE_SCHEMA_VERSION,
                goal=goal,
                max_repair_attempts=requested_attempts,
            )
        await self._record(
            event_key="run_started",
            run_id=run_id,
            event_type=TraceEventType.RUN_STARTED,
            phase="service",
            status="running",
            payload={
                "state_schema_version": CURRENT_STATE_SCHEMA_VERSION,
                "max_repair_attempts": requested_attempts,
            },
        )
        if self._graph is None:
            result = AgentRunResult(
                run_id=run_id,
                status="model_error",
                steps=0,
                message_count=len(initial_state["messages"]),
                error=AgentRunError(
                    code="model_error",
                    message=f"Model tool binding failed: {self._build_error}",
                ),
            )
            failed_state = initial_state | {"status": "model_error"}
            await self._update_registry(failed_state)
            await self._record_completion(failed_state)
            return result
        try:
            state = await self._graph.ainvoke(
                initial_state,
                config=_config(run_id, max_steps, requested_attempts),
            )
        except GraphRecursionError:
            result = _recursion_error(run_id, len(initial_state["messages"]))
            failed_state = initial_state | {"status": result.status}
            await self._update_registry(failed_state)
            await self._record_completion(failed_state)
            return result
        await self._update_registry(state)
        await self._record_completion(state)
        return _run_result(run_id, state, self._pytest_target)

    async def resume_run(
        self,
        run_id: str,
        request: ApprovalDecisionRequest,
    ) -> AgentRunResult:
        if self._runtime_store is not None:
            try:
                await self._runtime_store.get_run(run_id)
            except RunNotFoundError as exc:
                raise _approval_error(
                    ToolErrorCode.RUN_NOT_FOUND, "The requested run was not found."
                ) from exc
        await self._validated_pending_snapshot(run_id, request)
        lock = self._run_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            return await self._resume_run_locked(run_id, request)

    async def _resume_run_locked(
        self,
        run_id: str,
        request: ApprovalDecisionRequest,
    ) -> AgentRunResult:
        values = await self._validated_pending_snapshot(run_id, request)
        max_steps = int(values.get("max_steps", 10))
        max_repair_attempts = int(values.get("max_repair_attempts", self._max_repair_attempts))
        await self._record(
            event_key=f"run_resumed:{request.proposal_id}:{request.decision.value}",
            run_id=run_id,
            event_type=TraceEventType.RUN_RESUMED,
            phase="service",
            status="running",
            payload={
                "proposal_id": str(request.proposal_id),
                "decision": request.decision.value,
            },
        )
        assert self._graph is not None
        try:
            state = await self._graph.ainvoke(
                Command(resume=request.model_dump(mode="json")),
                config=_config(run_id, max_steps, max_repair_attempts),
            )
        except GraphRecursionError:
            result = _recursion_error(run_id, len(values.get("messages", [])))
            failed_state = values | {"status": result.status}
            await self._update_registry(failed_state)
            await self._record_completion(failed_state)
            return result
        await self._update_registry(state)
        await self._record_completion(state)
        return _run_result(run_id, state, self._pytest_target)

    async def get_run(self, run_id: str) -> AgentRunView:
        store = self._require_runtime_store()
        record = await store.get_run(run_id)
        values = await self._checkpoint_values(record.thread_id)
        if not values:
            raise _approval_error(
                ToolErrorCode.CHECKPOINT_NOT_FOUND,
                "The persisted checkpoint for this run was not found.",
            )
        if values.get("run_id") != record.run_id:
            raise _approval_error(
                ToolErrorCode.RUNTIME_STATE_MISMATCH,
                "The run registry does not match its persisted checkpoint.",
            )
        self._validate_state_version(values)
        if _run_record_needs_update(record, values):
            record = await store.update_run_from_state(run_id, values)
        return _run_view(record, values, self._pytest_target)

    async def list_runs(
        self, *, status: str | None, limit: int, cursor: str | None
    ) -> AgentRunListResponse:
        page = await self._require_runtime_store().list_runs(
            status=status, limit=limit, cursor=cursor
        )
        return _run_list(page)

    async def list_trace_events(
        self,
        run_id: str,
        *,
        limit: int,
        after: int,
        event_type: TraceEventType | None = None,
    ) -> TraceEventListResponse:
        events = await self._require_runtime_store().list_trace_events(
            run_id,
            limit=limit,
            after=after,
            event_type=event_type.value if event_type is not None else None,
        )
        return TraceEventListResponse(items=[_trace_view(event) for event in events])

    async def delete_run(self, run_id: str) -> None:
        store = self._require_runtime_store()
        record = await store.get_run(run_id)
        values = await self._checkpoint_values(record.thread_id)
        status = str(values.get("status", record.status))
        if status not in TERMINAL_RUN_STATUSES:
            raise RunNotTerminalError("run is not terminal")
        await store.mark_cleanup(run_id, "in_progress")
        try:
            await self._record(
                event_key="run_deleted",
                run_id=run_id,
                event_type=TraceEventType.RUN_DELETED,
                phase="cleanup",
                status="deleted",
                payload={"status": status},
            )
            await self._checkpointer.adelete_thread(record.thread_id)
            await store.delete_runtime_data(run_id)
        except Exception as exc:
            with suppress(RunNotFoundError):
                await store.mark_cleanup(run_id, "failed", type(exc).__name__)
            raise RunCleanupError("run cleanup failed") from exc
        self._run_locks.pop(run_id, None)

    async def cleanup_expired_runs(self) -> list[str]:
        store = self._require_runtime_store()
        await store.prune_terminal_trace_events(self._trace_retention_days)
        cleaned: list[str] = []
        for run_id in await store.expired_terminal_run_ids(self._run_retention_days):
            try:
                await self.delete_run(run_id)
            except (RunNotFoundError, RunNotTerminalError):
                continue
            cleaned.append(run_id)
        return cleaned

    async def _validated_pending_snapshot(
        self, run_id: str, request: ApprovalDecisionRequest
    ) -> dict[str, Any]:
        if self._graph is None:
            raise _approval_error(
                ToolErrorCode.RUN_NOT_FOUND, "The requested run is not available."
            )
        snapshot = await self._graph.aget_state(_config(run_id, 10, self._max_repair_attempts))
        values = dict(snapshot.values)
        if not values or values.get("run_id") != run_id:
            raise _approval_error(ToolErrorCode.RUN_NOT_FOUND, "The requested run was not found.")
        self._validate_state_version(values)
        if not snapshot.next:
            raise _approval_error(
                ToolErrorCode.RUN_ALREADY_COMPLETED,
                "The requested run has already completed.",
            )
        proposal = _proposal_from_state(values.get("pending_approval"))
        if proposal is None:
            raise _approval_error(
                ToolErrorCode.NO_PENDING_APPROVAL,
                "The requested run is not awaiting approval.",
            )
        if request.proposal_id != proposal.proposal_id:
            raise _approval_error(
                ToolErrorCode.PROPOSAL_MISMATCH,
                "The approval does not match the pending proposal.",
            )
        return values

    async def _checkpoint_values(self, thread_id: str) -> dict[str, Any]:
        if self._graph is None:
            return {}
        snapshot = await self._graph.aget_state(_config(thread_id, 10, self._max_repair_attempts))
        return dict(snapshot.values)

    @staticmethod
    def _validate_state_version(values: dict[str, Any]) -> None:
        if values and values.get("state_schema_version") != CURRENT_STATE_SCHEMA_VERSION:
            raise _approval_error(
                ToolErrorCode.CHECKPOINT_INCOMPATIBLE,
                "The persisted checkpoint schema is not compatible with this server.",
            )

    async def _update_registry(self, state: dict[str, Any]) -> None:
        if self._runtime_store is not None:
            await self._runtime_store.update_run_from_state(str(state["run_id"]), state)

    async def _record_completion(self, state: dict[str, Any]) -> None:
        status = str(state.get("status", "running"))
        if status in {"running", "awaiting_approval"}:
            return
        final_report = state.get("final_report")
        outcome = final_report.get("outcome") if isinstance(final_report, dict) else status
        successful = status in {"success", "repaired", "no_change"}
        event_type = TraceEventType.RUN_COMPLETED if successful else TraceEventType.RUN_FAILED
        await self._record(
            event_key=f"{event_type.value}:{outcome}",
            run_id=str(state["run_id"]),
            event_type=event_type,
            phase="service",
            status=status,
            payload={"outcome": outcome, "status": status},
        )

    async def _record(
        self,
        *,
        event_key: str,
        run_id: str,
        event_type: TraceEventType,
        phase: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        if self._trace is not None:
            await self._trace.record(
                TraceEvent(
                    event_key=event_key,
                    run_id=run_id,
                    event_type=event_type,
                    phase=phase,
                    status=status,
                    payload=payload,
                )
            )

    def _require_runtime_store(self) -> RuntimeStore:
        if self._runtime_store is None:
            raise RuntimeError("runtime store is required for persisted run management")
        return self._runtime_store


def _config(run_id: str, max_steps: int, max_repair_attempts: int) -> dict[str, object]:
    return {
        "configurable": {"thread_id": run_id},
        "recursion_limit": max(40, max_steps * 10 + max_repair_attempts * 10),
    }


def _proposal_from_state(raw: object) -> PatchProposal | None:
    if raw is None:
        return None
    try:
        return PatchProposal.model_validate(raw)
    except (TypeError, ValueError):
        return None


def _run_result(
    run_id: str, state: AgentState | dict[str, Any], pytest_target: str
) -> AgentRunResult:
    proposal = _proposal_from_state(state.get("pending_approval"))
    status = str(state.get("status", "invalid_model_response"))
    return AgentRunResult(
        run_id=run_id,
        status=status,
        final_answer=str(state.get("final_answer") or ""),
        steps=int(state.get("model_calls", 0)),
        tool_executions=list(state.get("tool_executions", [])),
        message_count=len(state.get("messages", [])),
        error=state.get("error"),
        approval=(
            approval_view(run_id, proposal, pytest_target)
            if status == "awaiting_approval" and proposal
            else None
        ),
        final_report=state.get("final_report"),
    )


def _run_view(record: RunRecord, state: dict[str, Any], pytest_target: str) -> AgentRunView:
    proposal = _proposal_from_state(state.get("pending_approval"))
    status = str(state.get("status", record.status))
    values = record.model_dump(
        exclude={"thread_id", "state_schema_version", "goal_sha256", "cleanup_status"}
    )
    values["status"] = status
    return AgentRunView(
        **values,
        awaiting_approval=status == "awaiting_approval",
        approval=(
            approval_view(record.run_id, proposal, pytest_target)
            if status == "awaiting_approval" and proposal
            else None
        ),
        latest_context_stats=state.get("latest_context_stats"),
    )


def _run_record_needs_update(record: RunRecord, state: dict[str, Any]) -> bool:
    proposal = state.get("pending_approval")
    proposal_id = proposal.get("proposal_id") if isinstance(proposal, dict) else None
    latest_test = state.get("latest_test_result")
    test_outcome = latest_test.get("outcome") if isinstance(latest_test, dict) else None
    review = state.get("review_result")
    review_status = review.get("status") if isinstance(review, dict) else None
    final_report = state.get("final_report")
    outcome = final_report.get("outcome") if isinstance(final_report, dict) else None
    status = str(state.get("status", "running"))
    return any(
        (
            record.state_schema_version != int(state.get("state_schema_version", 0)),
            record.status != status,
            record.outcome != outcome,
            record.current_proposal_id != (str(proposal_id) if proposal_id else None),
            record.repair_attempts != int(state.get("repair_attempts", 0)),
            record.max_repair_attempts != int(state.get("max_repair_attempts", 0)),
            record.model_calls != int(state.get("model_calls", 0)),
            record.latest_test_outcome != (str(test_outcome) if test_outcome else None),
            record.review_status != (str(review_status) if review_status else None),
            (record.final_report is None) != (final_report is None),
            status not in {"running", "awaiting_approval"} and record.completed_at is None,
        )
    )


def _run_list(page: RunPage) -> AgentRunListResponse:
    return AgentRunListResponse(
        items=[
            AgentRunSummary(
                **record.model_dump(
                    include={
                        "run_id",
                        "status",
                        "outcome",
                        "created_at",
                        "updated_at",
                        "completed_at",
                        "current_proposal_id",
                        "repair_attempts",
                        "max_repair_attempts",
                        "model_calls",
                        "latest_test_outcome",
                        "review_status",
                    }
                ),
                awaiting_approval=record.status == "awaiting_approval",
            )
            for record in page.items
        ],
        next_cursor=page.next_cursor,
    )


def _trace_view(record: TraceRecord) -> TraceEventView:
    return TraceEventView(**record.model_dump(exclude={"run_id"}))


def _approval_error(code: ToolErrorCode, message: str) -> ApprovalServiceError:
    return ApprovalServiceError(code, message)


def _recursion_error(run_id: str, message_count: int) -> AgentRunResult:
    return AgentRunResult(
        run_id=run_id,
        status="invalid_model_response",
        steps=0,
        message_count=message_count,
        error=AgentRunError(
            code="invalid_model_response",
            message="Agent graph exceeded its defensive recursion limit",
        ),
    )
