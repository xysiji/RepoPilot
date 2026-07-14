"""Model, safe-tool, approval, and patch-resolution graph nodes."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import Runnable
from langgraph.types import interrupt

from repopilot.agent.state import AgentState
from repopilot.approval.contracts import (
    ApprovalDecision,
    approval_view,
)
from repopilot.approval.validation import validate_resume_decision
from repopilot.patching.applicator import PatchApplicator
from repopilot.patching.proposal import PatchProposal, proposal_safe_metadata
from repopilot.review.report import FinalReportBuilder
from repopilot.review.reviewer import DeterministicReviewer
from repopilot.schemas.agent import AgentRunError
from repopilot.testing.contracts import (
    AppliedPatchContext,
    TestOutcome,
    TestRunner,
    TestRunRecord,
    TestRunResult,
)
from repopilot.testing.feedback import (
    build_test_feedback_message,
    test_error_code,
    test_error_message,
    test_failure_category,
)
from repopilot.tools.contracts import (
    ToolEffect,
    ToolErrorCode,
    ToolExecutionPhase,
    ToolExecutionRecord,
    ToolFailureCategory,
    ToolResultEnvelope,
    failed_result,
)
from repopilot.tools.executor import SafeToolExecutor

_MAX_FINAL_ANSWER_CHARACTERS = 4000
_PATCH_TOOL_NAME = "propose_patch"


class ModelNode:
    """Invoke one already-bound model and emit only a partial state update."""

    def __init__(self, bound_model: Runnable[Any, BaseMessage]) -> None:
        self._bound_model = bound_model

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        model_calls = state["model_calls"] + 1
        try:
            response = await self._bound_model.ainvoke(list(state["messages"]))
        except Exception as exc:
            return _terminal_update(
                "model_error",
                f"Model invocation failed: {type(exc).__name__}",
                model_calls=model_calls,
            )

        if not isinstance(response, AIMessage):
            return _terminal_update(
                "invalid_model_response",
                "Model must return an AIMessage",
                model_calls=model_calls,
            )

        if response.tool_calls:
            for tool_call in response.tool_calls:
                call_id = tool_call.get("id")
                if not isinstance(call_id, str) or not call_id:
                    update = _terminal_update(
                        "invalid_model_response",
                        "Every tool call must have a non-empty ID",
                        model_calls=model_calls,
                    )
                    update["messages"] = [response]
                    return update
            return {
                "messages": [response],
                "model_calls": model_calls,
                "status": "running",
                "error": None,
            }

        final_answer = response.text.strip()
        if not final_answer:
            update = _terminal_update(
                "invalid_model_response",
                "Model returned empty final content",
                model_calls=model_calls,
            )
            update["messages"] = [response]
            return update
        if len(final_answer) > _MAX_FINAL_ANSWER_CHARACTERS:
            final_answer = final_answer[:_MAX_FINAL_ANSWER_CHARACTERS] + "\n[truncated]"
        latest_test = _test_result_from_state(state.get("latest_test_result"))
        if latest_test is not None and latest_test.outcome is TestOutcome.TEST_FAILURES:
            return {
                "messages": [response],
                "model_calls": model_calls,
                "status": "repair_abandoned",
                "final_answer": None,
                "model_final_text": final_answer,
                "error": AgentRunError(
                    code="repair_abandoned",
                    message="The model stopped proposing patches while tests were failing.",
                ),
            }
        return {
            "messages": [response],
            "model_calls": model_calls,
            "status": "success",
            "final_answer": final_answer,
            "model_final_text": final_answer,
            "error": None,
        }


class ToolNode:
    """Delegate every model-ordered call to the P3 safety executor."""

    def __init__(self, executor: SafeToolExecutor) -> None:
        self._executor = executor

    def __call__(self, state: AgentState) -> dict[str, Any]:
        latest = state["messages"][-1] if state["messages"] else None
        if not isinstance(latest, AIMessage) or not latest.tool_calls:
            return _terminal_update(
                "invalid_model_response",
                "Tool node requires an AIMessage with tool calls",
                model_calls=state["model_calls"],
            )

        messages: list[ToolMessage] = []
        executions: list[ToolExecutionRecord] = []
        calls = list(latest.tool_calls)
        includes_patch = any(call["name"] == _PATCH_TOOL_NAME for call in calls)
        if includes_patch and len(calls) != 1:
            for tool_call in calls:
                result = self._executor.failure(
                    model_call=state["model_calls"],
                    tool_name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                    tool_input=dict(tool_call["args"]),
                    phase=ToolExecutionPhase.POLICY,
                    category=ToolFailureCategory.APPROVAL,
                    code=ToolErrorCode.APPROVAL_BATCH_NOT_SUPPORTED,
                    message="A patch proposal must be the only tool call in its model response.",
                )
                assert result.tool_message is not None and result.record is not None
                messages.append(result.tool_message)
                executions.append(result.record)
            return self._completed_tool_update(state, messages, executions)

        if includes_patch and state["repair_attempts"] >= state["max_repair_attempts"]:
            tool_call = calls[0]
            result = self._executor.failure(
                model_call=state["model_calls"],
                tool_name=tool_call["name"],
                tool_call_id=tool_call["id"],
                tool_input=dict(tool_call["args"]),
                phase=ToolExecutionPhase.TESTING,
                category=ToolFailureCategory.REPAIR_BUDGET,
                code=ToolErrorCode.REPAIR_ATTEMPTS_EXHAUSTED,
                message="No additional patch can be proposed after the repair budget is exhausted.",
                effect=ToolEffect.WRITE,
            )
            assert result.tool_message is not None and result.record is not None
            update = self._completed_tool_update(state, [result.tool_message], [result.record])
            update.update(
                _terminal_update(
                    "repair_attempts_exhausted",
                    "The approved patch test-attempt limit has been reached.",
                    model_calls=state["model_calls"],
                )
            )
            return update

        if includes_patch and state["model_calls"] >= state["max_steps"]:
            tool_call = calls[0]
            result = self._executor.failure(
                model_call=state["model_calls"],
                tool_name=tool_call["name"],
                tool_call_id=tool_call["id"],
                tool_input=dict(tool_call["args"]),
                phase=ToolExecutionPhase.APPROVAL,
                category=ToolFailureCategory.APPROVAL,
                code=ToolErrorCode.APPROVAL_NOT_STARTED_BUDGET_EXHAUSTED,
                message="Approval was not started because no later model round remains.",
                effect=ToolEffect.WRITE,
            )
            assert result.tool_message is not None and result.record is not None
            return self._completed_tool_update(state, [result.tool_message], [result.record])

        for tool_call in calls:
            tool_name = tool_call["name"]
            tool_call_id = tool_call["id"]
            tool_input = dict(tool_call["args"])
            result = self._executor.execute(
                model_call=state["model_calls"],
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_input=tool_input,
            )
            if result.proposal is not None:
                return {
                    "pending_approval": result.proposal.model_dump(mode="json"),
                    "approval_decision": None,
                    "status": "awaiting_approval",
                    "error": None,
                }
            assert result.tool_message is not None and result.record is not None
            messages.append(result.tool_message)
            executions.append(result.record)

        return self._completed_tool_update(state, messages, executions)

    @staticmethod
    def _completed_tool_update(
        state: AgentState,
        messages: list[ToolMessage],
        executions: list[ToolExecutionRecord],
    ) -> dict[str, Any]:

        update: dict[str, Any] = {
            "messages": messages,
            "tool_executions": [record.model_dump(mode="json") for record in executions],
        }
        if state["model_calls"] >= state["max_steps"]:
            update.update(
                _terminal_update(
                    "max_steps_exceeded",
                    f"Maximum model steps exceeded: {state['max_steps']}",
                    model_calls=state["model_calls"],
                )
            )
        else:
            update.update({"status": "running", "error": None})
        return update


class ApprovalNode:
    """The sole node allowed to call ``interrupt()``; it has no side effects."""

    def __init__(self, pytest_target: str = "tests") -> None:
        self._pytest_target = pytest_target

    def __call__(self, state: AgentState) -> dict[str, Any]:
        proposal = _proposal_from_state(state["pending_approval"])
        if proposal is None:
            return {
                "approval_decision": validate_resume_decision(
                    {},
                    _nil_proposal_id(),
                ).model_dump(mode="json")
            }
        payload = approval_view(state["run_id"], proposal, self._pytest_target)
        raw_decision = interrupt(payload.model_dump(mode="json"))
        return {
            "approval_decision": validate_resume_decision(
                raw_decision,
                proposal.proposal_id,
            ).model_dump(mode="json"),
            "approval_count": state["approval_count"] + 1,
        }


class ApplyPatchNode:
    """Apply exactly the proposal that was checkpointed and approved."""

    def __init__(self, applicator: PatchApplicator) -> None:
        self._applicator = applicator

    def __call__(self, state: AgentState) -> dict[str, Any]:
        proposal = _proposal_from_state(state["pending_approval"])
        decision = _decision_from_state(state["approval_decision"])
        if proposal is None or decision is None:
            return _missing_resolution_update(state)
        envelope = self._applicator.apply(proposal, decision)
        if envelope.success:
            return _successful_apply_update(state, proposal)
        return _patch_resolution_update(state, proposal, envelope)


class RejectPatchNode:
    """Resolve a rejected or invalid approval without calling the applicator."""

    def __call__(self, state: AgentState) -> dict[str, Any]:
        proposal = _proposal_from_state(state["pending_approval"])
        decision = _decision_from_state(state["approval_decision"])
        if proposal is None:
            return _missing_resolution_update(state)
        code = ToolErrorCode.APPROVAL_REJECTED
        message = "The human reviewer rejected the patch proposal."
        if decision is None or not decision.valid:
            code = (
                decision.error_code
                if decision and decision.error_code
                else ToolErrorCode.INVALID_APPROVAL_DECISION
            )
            message = "The approval decision was invalid; the patch was not applied."
        envelope = failed_result(
            phase=ToolExecutionPhase.APPROVAL,
            category=ToolFailureCategory.APPROVAL,
            code=code,
            message=message,
        )
        return _patch_resolution_update(state, proposal, envelope)


class TesterNode:
    """Run fixed pytest exactly once for one consumed successful Apply context."""

    __test__ = False

    def __init__(self, runner: TestRunner) -> None:
        self._runner = runner

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        context = _applied_context_from_state(state.get("applied_patch_context"))
        if context is None:
            return _terminal_update(
                "test_infrastructure_error",
                "Applied patch evidence is missing before pytest verification.",
                model_calls=state["model_calls"],
            )

        attempt = state["repair_attempts"] + 1
        try:
            raw_result = TestRunResult.model_validate(await self._runner.run())
        except Exception:
            timestamp = datetime.now(UTC).isoformat()
            raw_result = TestRunResult(
                outcome=TestOutcome.LAUNCH_ERROR,
                exit_code=None,
                duration_ms=0,
                timed_out=False,
                output_truncated=False,
                safe_output_excerpt="The fixed pytest runner failed to produce a result.",
                started_at=timestamp,
                finished_at=timestamp,
            )
        result = raw_result.model_copy(update={"proposal_id": context.proposal_id})
        record = TestRunRecord.from_result(
            result,
            attempt_number=attempt,
            proposal_id=context.proposal_id,
            command_display=self._runner.command_display,
        )
        message = build_test_feedback_message(context, result, attempt_number=attempt)
        execution = _test_execution_record(context, result)
        status, error = _status_after_test(state, result, attempt)
        return {
            "messages": [message],
            "tool_executions": [execution.model_dump(mode="json")],
            "repair_attempts": attempt,
            "test_runs": [record.model_dump(mode="json")],
            "latest_test_result": result.model_dump(mode="json"),
            "applied_patch_context": None,
            "status": status,
            "error": error,
        }


class ReviewerNode:
    """Run the deterministic evidence review without invoking a model."""

    def __init__(self, reviewer: DeterministicReviewer) -> None:
        self._reviewer = reviewer

    def __call__(self, state: AgentState) -> dict[str, Any]:
        result = self._reviewer.review(state)
        return {"review_result": result.model_dump(mode="json")}


class FinalReportNode:
    """Build the sole terminal user summary and structured safe report."""

    def __init__(self, builder: FinalReportBuilder) -> None:
        self._builder = builder

    def __call__(self, state: AgentState) -> dict[str, Any]:
        report = self._builder.build(state)
        public_status = "success" if report.outcome.value == "no_change" else report.outcome.value
        return {
            "final_report": report.model_dump(mode="json"),
            "final_answer": report.summary,
            "status": public_status,
        }


def _patch_resolution_update(
    state: AgentState,
    proposal: PatchProposal,
    envelope: ToolResultEnvelope,
) -> dict[str, Any]:
    failure = envelope.error
    message = ToolMessage(
        content=envelope.stable_json(),
        tool_call_id=proposal.tool_call_id,
        name=proposal.tool_name,
        status="success" if envelope.success else "error",
    )
    record = ToolExecutionRecord(
        step=state["model_calls"],
        tool_name=proposal.tool_name,
        tool_call_id=proposal.tool_call_id,
        input=proposal_safe_metadata(proposal),
        success=envelope.success,
        output_summary=(
            f"applied patch to {proposal.relative_path}"
            if envelope.success
            else failure.message
            if failure
            else "patch failed"
        ),
        error_type=failure.code.value if failure else None,
        error_message=failure.message if failure else None,
        phase=failure.phase if failure else ToolExecutionPhase.APPLY,
        failure_category=failure.category if failure else None,
        error_code=failure.code if failure else None,
        effect=ToolEffect.WRITE,
        policy_allowed=True,
    )
    update: dict[str, Any] = {
        "messages": [message],
        "tool_executions": [record.model_dump(mode="json")],
        "pending_approval": None,
        "approval_decision": None,
        "status": "running",
        "error": None,
        "last_patch_error_code": failure.code.value if failure else None,
    }
    if state["model_calls"] >= state["max_steps"]:
        update.update(
            _terminal_update(
                "max_steps_exceeded",
                f"Maximum model steps exceeded: {state['max_steps']}",
                model_calls=state["model_calls"],
            )
        )
    return update


def _successful_apply_update(state: AgentState, proposal: PatchProposal) -> dict[str, Any]:
    """Hand safe apply evidence to Tester without resolving the tool call early."""

    context = AppliedPatchContext(
        proposal_id=proposal.proposal_id,
        tool_call_id=proposal.tool_call_id,
        tool_name=proposal.tool_name,
        relative_path=proposal.relative_path,
        original_sha256=proposal.original_sha256,
        proposed_sha256=proposal.proposed_sha256,
        added_line_count=proposal.added_line_count,
        removed_line_count=proposal.removed_line_count,
        model_call=state["model_calls"],
        approved=True,
    )
    return {
        "pending_approval": None,
        "approval_decision": None,
        "applied_patch_context": context.model_dump(mode="json"),
        "applied_patches": [context.model_dump(mode="json")],
        "status": "running",
        "error": None,
        "last_patch_error_code": None,
    }


def _test_execution_record(
    context: AppliedPatchContext,
    result: TestRunResult,
) -> ToolExecutionRecord:
    success = result.outcome is TestOutcome.PASSED
    return ToolExecutionRecord(
        step=context.model_call,
        tool_name=context.tool_name,
        tool_call_id=context.tool_call_id,
        input={
            "proposal_id": str(context.proposal_id),
            "relative_path": context.relative_path,
            "original_sha256": context.original_sha256,
            "proposed_sha256": context.proposed_sha256,
            "added_line_count": context.added_line_count,
            "removed_line_count": context.removed_line_count,
        },
        success=success,
        output_summary=(
            f"applied patch to {context.relative_path}; pytest passed"
            if success
            else test_error_message(result.outcome)
        ),
        error_type=None if success else test_error_code(result.outcome).value,
        error_message=None if success else test_error_message(result.outcome),
        phase=ToolExecutionPhase.TESTING,
        failure_category=None if success else test_failure_category(result.outcome),
        error_code=None if success else test_error_code(result.outcome),
        effect=ToolEffect.WRITE,
        policy_allowed=True,
    )


def _status_after_test(
    state: AgentState,
    result: TestRunResult,
    attempt: int,
) -> tuple[str, AgentRunError | None]:
    if result.outcome is TestOutcome.PASSED:
        return "running", None
    if result.outcome is TestOutcome.TEST_FAILURES:
        if attempt >= state["max_repair_attempts"]:
            return (
                "repair_attempts_exhausted",
                AgentRunError(
                    code="repair_attempts_exhausted",
                    message="The approved patch test-attempt limit has been reached.",
                ),
            )
        if state["model_calls"] >= state["max_steps"]:
            return (
                "max_steps_exceeded",
                AgentRunError(
                    code="max_steps_exceeded",
                    message=f"Maximum model steps exceeded: {state['max_steps']}",
                ),
            )
        return "running", None
    if result.outcome is TestOutcome.TIMEOUT:
        return (
            "test_timeout",
            AgentRunError(code="test_timeout", message=test_error_message(result.outcome)),
        )
    return (
        "test_infrastructure_error",
        AgentRunError(
            code="test_infrastructure_error",
            message=test_error_message(result.outcome),
        ),
    )


def _missing_resolution_update(state: AgentState) -> dict[str, Any]:
    return _terminal_update(
        "invalid_model_response",
        "Patch resolution state is incomplete",
        model_calls=state["model_calls"],
    )


def _proposal_from_state(raw: object) -> PatchProposal | None:
    if raw is None:
        return None
    if isinstance(raw, PatchProposal):
        return raw
    try:
        return PatchProposal.model_validate(raw)
    except (TypeError, ValueError):
        return None


def _applied_context_from_state(raw: object) -> AppliedPatchContext | None:
    if raw is None:
        return None
    try:
        return AppliedPatchContext.model_validate(raw)
    except (TypeError, ValueError):
        return None


def _test_result_from_state(raw: object) -> TestRunResult | None:
    if raw is None:
        return None
    try:
        return TestRunResult.model_validate(raw)
    except (TypeError, ValueError):
        return None


def _decision_from_state(raw: object) -> ApprovalDecision | None:
    if raw is None:
        return None
    if isinstance(raw, ApprovalDecision):
        return raw
    try:
        return ApprovalDecision.model_validate(raw)
    except (TypeError, ValueError):
        return None


def _nil_proposal_id() -> UUID:
    return UUID(int=0)


def _terminal_update(code: str, message: str, *, model_calls: int) -> dict[str, Any]:
    return {
        "model_calls": model_calls,
        "status": code,
        "final_answer": None,
        "error": AgentRunError(code=code, message=message),
    }
