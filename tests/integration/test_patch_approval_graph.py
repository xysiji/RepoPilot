"""Offline end-to-end P4 graph interruption and ToolMessage protocol tests."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from repopilot.approval.contracts import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalServiceError,
)
from repopilot.patching.applicator import PatchApplicator
from repopilot.patching.proposal import PatchProposal
from repopilot.services.agent_service import AgentService
from repopilot.testing.contracts import TestOutcome
from repopilot.tools.contracts import (
    ToolErrorCode,
    ToolExecutionPhase,
    ToolFailureCategory,
    ToolResultEnvelope,
    failed_result,
)
from repopilot.tools.policy import WorkspaceGuard
from tests.fake_runner import ScriptedPytestRunner, make_test_result
from tests.scripted_model import ScriptedToolCallingModel


def _call(name: str, args: dict[str, object], call_id: str) -> dict[str, object]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _patch_call(path: str, content: str, call_id: str = "patch-1") -> dict[str, object]:
    return _call(
        "propose_patch",
        {"path": path, "new_content": content, "rationale": "Apply the requested change"},
        call_id,
    )


def _run(coro):
    return asyncio.run(coro)


class CountingPatchApplicator(PatchApplicator):
    """Record apply calls so concurrent duplicate decisions are observable."""

    def __init__(self, guard: WorkspaceGuard) -> None:
        super().__init__(guard)
        self.apply_calls = 0

    def apply(
        self,
        proposal: PatchProposal,
        decision: ApprovalDecision,
    ) -> ToolResultEnvelope:
        self.apply_calls += 1
        return super().apply(proposal, decision)


class FailingPatchApplicator(PatchApplicator):
    def apply(
        self,
        proposal: PatchProposal,
        decision: ApprovalDecision,
    ) -> ToolResultEnvelope:
        del proposal, decision
        return failed_result(
            phase=ToolExecutionPhase.APPLY,
            category=ToolFailureCategory.PATCH,
            code=ToolErrorCode.PATCH_APPLY_FAILED,
            message="The patch could not be applied atomically.",
        )


def test_approve_flow_pauses_without_write_then_applies_and_completes_protocol(
    tmp_path: Path,
) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    proposed = "new\n"
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_patch_call("a.py", proposed, "original-id")]),
            AIMessage(content="Patch applied after approval."),
        ]
    )
    runner = ScriptedPytestRunner([make_test_result(TestOutcome.PASSED, exit_code=0)])
    service = AgentService(
        tmp_path,
        model,
        checkpointer=InMemorySaver(),
        runner=runner,
    )

    pending = _run(service.start_run("change a.py", max_steps=3))

    assert pending.status == "awaiting_approval"
    assert pending.approval is not None
    assert pending.approval.tool_call_id == "original-id"
    assert target.read_bytes() == b"old\n"
    assert pending.tool_executions == []
    assert len(model.received_messages) == 1

    completed = _run(
        service.resume_run(
            pending.run_id,
            ApprovalDecisionRequest(
                proposal_id=pending.approval.proposal_id,
                decision="approve",
            ),
        )
    )

    assert completed.status == "repaired"
    assert target.read_bytes() == b"new\n"
    assert len(completed.tool_executions) == 1
    assert completed.tool_executions[0].success is True
    assert completed.tool_executions[0].tool_call_id == "original-id"
    assert completed.tool_executions[0].phase == "testing"
    assert proposed not in completed.model_dump_json()
    assert runner.run_calls == 1

    with pytest.raises(ApprovalServiceError) as duplicate:
        _run(
            service.resume_run(
                pending.run_id,
                ApprovalDecisionRequest(
                    proposal_id=pending.approval.proposal_id,
                    decision="approve",
                ),
            )
        )
    assert duplicate.value.code == "run_already_completed"


def test_concurrent_duplicate_approval_applies_patch_exactly_once(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_patch_call("a.py", "new\n")]),
            AIMessage(content="done"),
        ]
    )
    applicator = CountingPatchApplicator(WorkspaceGuard(tmp_path))
    runner = ScriptedPytestRunner([make_test_result(TestOutcome.PASSED, exit_code=0)])
    service = AgentService(tmp_path, model, applicator=applicator, runner=runner)
    pending = _run(service.start_run("change", max_steps=3))
    assert pending.approval is not None
    decision = ApprovalDecisionRequest(
        proposal_id=pending.approval.proposal_id,
        decision="approve",
    )

    async def resume_twice() -> list[object]:
        return await asyncio.gather(
            service.resume_run(pending.run_id, decision),
            service.resume_run(pending.run_id, decision),
            return_exceptions=True,
        )

    results = _run(resume_twice())

    assert sum(not isinstance(result, Exception) for result in results) == 1
    errors = [result for result in results if isinstance(result, ApprovalServiceError)]
    assert len(errors) == 1
    assert errors[0].code == "run_already_completed"
    assert applicator.apply_calls == 1
    assert target.read_bytes() == b"new\n"


def test_reject_flow_preserves_bytes_and_returns_error_tool_message(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_patch_call("a.py", "new\n", "reject-id")]),
            AIMessage(content="No change was applied."),
        ]
    )
    runner = ScriptedPytestRunner([])
    service = AgentService(tmp_path, model, runner=runner)
    pending = _run(service.start_run("change", max_steps=3))
    assert pending.approval is not None

    completed = _run(
        service.resume_run(
            pending.run_id,
            ApprovalDecisionRequest(
                proposal_id=pending.approval.proposal_id,
                decision="reject",
            ),
        )
    )

    assert completed.status == "approval_rejected"
    assert target.read_bytes() == b"old\n"
    assert completed.tool_executions[0].error_code == "approval_rejected"
    message = next(m for m in model.received_messages[1] if isinstance(m, ToolMessage))
    assert message.tool_call_id == "reject-id" and message.status == "error"
    assert runner.run_calls == 0


def test_file_change_while_waiting_returns_stale_patch_without_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_patch_call("a.py", "new\n")]),
            AIMessage(content="Detected a stale proposal."),
        ]
    )
    runner = ScriptedPytestRunner([])
    service = AgentService(tmp_path, model, runner=runner)
    pending = _run(service.start_run("change", max_steps=3))
    assert pending.approval is not None
    target.write_bytes(b"external change\n")

    completed = _run(
        service.resume_run(
            pending.run_id,
            ApprovalDecisionRequest(
                proposal_id=pending.approval.proposal_id,
                decision="approve",
            ),
        )
    )

    assert target.read_bytes() == b"external change\n"
    assert completed.status == "patch_apply_failed"
    assert completed.tool_executions[0].error_code == "stale_patch"
    assert runner.run_calls == 0


def test_apply_failure_returns_immediate_tool_error_without_running_pytest(
    tmp_path: Path,
) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_patch_call("a.py", "new\n", "failed-apply")]),
            AIMessage(content="Apply failed safely."),
        ]
    )
    runner = ScriptedPytestRunner([])
    service = AgentService(
        tmp_path,
        model,
        applicator=FailingPatchApplicator(WorkspaceGuard(tmp_path)),
        runner=runner,
    )
    pending = _run(service.start_run("change", max_steps=3))
    assert pending.approval is not None

    completed = _run(
        service.resume_run(
            pending.run_id,
            ApprovalDecisionRequest(
                proposal_id=pending.approval.proposal_id,
                decision="approve",
            ),
        )
    )

    assert completed.status == "patch_apply_failed"
    assert completed.tool_executions[0].error_code == "patch_apply_failed"
    assert runner.run_calls == 0
    assert target.read_bytes() == b"old\n"


@pytest.mark.parametrize(
    "calls",
    [
        [_patch_call("a.py", "new\n"), _call("read_file", {"path": "a.py"}, "read")],
        [_patch_call("a.py", "one\n", "patch-a"), _patch_call("a.py", "two\n", "patch-b")],
    ],
)
def test_patch_mixed_batch_rejects_every_call_without_execution(
    tmp_path: Path,
    calls: list[dict[str, object]],
) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    model = ScriptedToolCallingModel(
        responses=[AIMessage(content="", tool_calls=calls), AIMessage(content="retried safely")]
    )

    result = _run(AgentService(tmp_path, model).start_run("change", max_steps=3))

    assert result.status == "success"
    assert target.read_bytes() == b"old\n"
    assert len(result.tool_executions) == len(calls)
    assert all(r.error_code == "approval_batch_not_supported" for r in result.tool_executions)
    messages = [m for m in model.received_messages[1] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in messages] == [str(call["id"]) for call in calls]


def test_last_model_round_does_not_start_approval_or_write(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    model = ScriptedToolCallingModel(
        responses=[AIMessage(content="", tool_calls=[_patch_call("a.py", "new\n")])]
    )

    result = _run(AgentService(tmp_path, model).start_run("change", max_steps=1))

    assert result.status == "max_steps_exceeded"
    assert result.approval is None
    assert result.tool_executions[0].error_code == "approval_not_started_budget_exhausted"
    assert target.read_bytes() == b"old\n"


def test_two_pending_run_ids_are_isolated_and_wrong_ids_cannot_resume(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"a-old\n")
    (tmp_path / "b.py").write_bytes(b"b-old\n")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_patch_call("a.py", "a-new\n", "a-call")]),
            AIMessage(content="", tool_calls=[_patch_call("b.py", "b-new\n", "b-call")]),
            AIMessage(content="a done"),
            AIMessage(content="b done"),
        ]
    )
    runner = ScriptedPytestRunner([make_test_result(TestOutcome.PASSED, exit_code=0)])
    service = AgentService(tmp_path, model, runner=runner)
    run_a = _run(service.start_run("change a", max_steps=3))
    run_b = _run(service.start_run("change b", max_steps=3))
    assert run_a.approval is not None and run_b.approval is not None
    assert run_a.run_id != run_b.run_id

    with pytest.raises(ApprovalServiceError) as missing:
        _run(
            service.resume_run(
                str(uuid4()),
                ApprovalDecisionRequest(
                    proposal_id=run_a.approval.proposal_id,
                    decision="approve",
                ),
            )
        )
    assert missing.value.code == "run_not_found"

    with pytest.raises(ApprovalServiceError) as mismatch:
        _run(
            service.resume_run(
                run_a.run_id,
                ApprovalDecisionRequest(
                    proposal_id=run_b.approval.proposal_id,
                    decision="approve",
                ),
            )
        )
    assert mismatch.value.code == "proposal_mismatch"
    assert (tmp_path / "a.py").read_bytes() == b"a-old\n"
    assert (tmp_path / "b.py").read_bytes() == b"b-old\n"


def test_new_in_memory_saver_cannot_resume_old_run(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"old\n")
    first_model = ScriptedToolCallingModel(
        responses=[AIMessage(content="", tool_calls=[_patch_call("a.py", "new\n")])]
    )
    first_service = AgentService(tmp_path, first_model, checkpointer=InMemorySaver())
    pending = _run(first_service.start_run("change", max_steps=3))
    assert pending.approval is not None
    restarted = AgentService(
        tmp_path,
        ScriptedToolCallingModel(responses=[AIMessage(content="unused")]),
        checkpointer=InMemorySaver(),
    )

    with pytest.raises(ApprovalServiceError) as missing:
        _run(
            restarted.resume_run(
                pending.run_id,
                ApprovalDecisionRequest(
                    proposal_id=pending.approval.proposal_id,
                    decision="approve",
                ),
            )
        )

    assert missing.value.code == "run_not_found"
    assert (tmp_path / "a.py").read_bytes() == b"old\n"
