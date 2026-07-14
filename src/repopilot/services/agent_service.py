"""Composition and start/resume lifecycle for the resumable P5 graph."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from repopilot.agent.graph import build_agent_graph
from repopilot.agent.state import AgentState, create_initial_state
from repopilot.approval.contracts import (
    ApprovalDecisionRequest,
    ApprovalServiceError,
    approval_view,
)
from repopilot.patching.applicator import PatchApplicator
from repopilot.patching.proposal import PatchProposal, PatchProposalBuilder
from repopilot.schemas.agent import AgentRunError, AgentRunResult
from repopilot.testing.contracts import TestRunner
from repopilot.testing.pytest_runner import PytestRunner
from repopilot.tools.contracts import ToolErrorCode
from repopilot.tools.executor import SafeToolExecutor
from repopilot.tools.patch import build_patch_tool
from repopilot.tools.policy import ToolSafetyPolicy, WorkspaceGuard
from repopilot.tools.readonly import build_readonly_tools


class AgentService:
    """Own one reusable graph/checkpointer and isolate runs by server-generated UUID."""

    def __init__(
        self,
        workspace_path: str | Path,
        model: BaseChatModel,
        *,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        applicator: PatchApplicator | None = None,
        runner: TestRunner | None = None,
        pytest_target: str = "tests",
        pytest_timeout_seconds: float = 60.0,
        pytest_max_output_characters: int = 20_000,
        max_repair_attempts: int = 3,
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
        executor = SafeToolExecutor(
            tools,
            ToolSafetyPolicy(workspace_guard),
            proposal_builder,
        )
        self._checkpointer = checkpointer or InMemorySaver()
        if not 1 <= max_repair_attempts <= 5:
            raise ValueError("max_repair_attempts must be between 1 and 5")
        self._max_repair_attempts = max_repair_attempts
        self._pytest_target = resolved_runner.target
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._graph = None
        self._build_error: str | None = None
        try:
            self._graph = build_agent_graph(
                model,
                tools,
                executor,
                resolved_applicator,
                self._checkpointer,
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
        """Backward-compatible alias for a new P5 run."""

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
        if self._graph is None:
            return AgentRunResult(
                run_id=run_id,
                status="model_error",
                steps=0,
                message_count=len(initial_state["messages"]),
                error=AgentRunError(
                    code="model_error",
                    message=f"Model tool binding failed: {self._build_error}",
                ),
            )
        try:
            state = await self._graph.ainvoke(
                initial_state,
                config=_config(run_id, max_steps, requested_attempts),
            )
        except GraphRecursionError:
            return _recursion_error(run_id, len(initial_state["messages"]))
        return _run_result(run_id, state, self._pytest_target)

    async def resume_run(
        self,
        run_id: str,
        request: ApprovalDecisionRequest,
    ) -> AgentRunResult:
        lock = self._run_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            return await self._resume_run_locked(run_id, request)

    async def _resume_run_locked(
        self,
        run_id: str,
        request: ApprovalDecisionRequest,
    ) -> AgentRunResult:
        """Recheck and resume one run while duplicate decisions are serialized."""

        if self._graph is None:
            raise ApprovalServiceError(
                ToolErrorCode.RUN_NOT_FOUND,
                "The requested run is not available in this process.",
            )
        config = _config(run_id, 10, self._max_repair_attempts)
        snapshot = await self._graph.aget_state(config)
        values = snapshot.values
        if not values or values.get("run_id") != run_id:
            raise ApprovalServiceError(
                ToolErrorCode.RUN_NOT_FOUND,
                "The requested run is not available in this process.",
            )
        if not snapshot.next:
            raise ApprovalServiceError(
                ToolErrorCode.RUN_ALREADY_COMPLETED,
                "The requested run has already completed.",
            )
        proposal = _proposal_from_state(values.get("pending_approval"))
        if proposal is None:
            raise ApprovalServiceError(
                ToolErrorCode.NO_PENDING_APPROVAL,
                "The requested run is not awaiting approval.",
            )
        if request.proposal_id != proposal.proposal_id:
            raise ApprovalServiceError(
                ToolErrorCode.PROPOSAL_MISMATCH,
                "The approval does not match the pending proposal.",
            )
        max_steps = int(values.get("max_steps", 10))
        max_repair_attempts = int(values.get("max_repair_attempts", self._max_repair_attempts))
        try:
            state = await self._graph.ainvoke(
                Command(resume=request.model_dump(mode="json")),
                config=_config(run_id, max_steps, max_repair_attempts),
            )
        except GraphRecursionError:
            return _recursion_error(run_id, len(values.get("messages", [])))
        return _run_result(run_id, state, self._pytest_target)


def _config(run_id: str, max_steps: int, max_repair_attempts: int) -> dict[str, object]:
    return {
        "configurable": {"thread_id": run_id},
        "recursion_limit": max(40, max_steps * 10 + max_repair_attempts * 10),
    }


def _proposal_from_state(raw: object) -> PatchProposal | None:
    if raw is None:
        return None
    if isinstance(raw, PatchProposal):
        return raw
    try:
        return PatchProposal.model_validate(raw)
    except (TypeError, ValueError):
        return None


def _run_result(
    run_id: str,
    state: AgentState | dict[str, object],
    pytest_target: str,
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
