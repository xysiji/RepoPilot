"""Deterministic workflow evidence review tests."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import ToolMessage

from repopilot.agent.state import create_initial_state
from repopilot.review.contracts import ReviewStatus
from repopilot.review.reviewer import DeterministicReviewer
from repopilot.testing.contracts import AppliedPatchContext, TestOutcome
from repopilot.tools.policy import WorkspaceGuard
from tests.fake_runner import make_test_result


def _review_state(tmp_path: Path, outcome: TestOutcome = TestOutcome.PASSED):
    content = b"fixed\n"
    (tmp_path / "a.py").write_bytes(content)
    proposal_id = uuid4()
    context = AppliedPatchContext(
        proposal_id=proposal_id,
        tool_call_id="patch-1",
        relative_path="a.py",
        original_sha256="a" * 64,
        proposed_sha256=hashlib.sha256(content).hexdigest(),
        added_line_count=1,
        removed_line_count=1,
        model_call=1,
    )
    exit_code = 0 if outcome is TestOutcome.PASSED else 1
    result = make_test_result(outcome, exit_code=exit_code).model_copy(
        update={"proposal_id": proposal_id}
    )
    state = create_initial_state("goal", 3)
    state["applied_patches"].append(context.model_dump(mode="json"))
    state["latest_test_result"] = result.model_dump(mode="json")
    state["repair_attempts"] = 1
    state["messages"].append(
        ToolMessage(content="{}", tool_call_id=context.tool_call_id, name="propose_patch")
    )
    return state, context


def test_pass_requires_matching_hash_patch_test_and_single_tool_message(tmp_path: Path) -> None:
    state, _context = _review_state(tmp_path)

    review = DeterministicReviewer(WorkspaceGuard(tmp_path)).review(state)

    assert review.status is ReviewStatus.PASSED
    assert review.verified_patch_hash is True
    assert review.latest_test_outcome is TestOutcome.PASSED
    json.loads(review.model_dump_json())


def test_failed_tests_are_review_failure(tmp_path: Path) -> None:
    state, _context = _review_state(tmp_path, TestOutcome.TEST_FAILURES)

    review = DeterministicReviewer(WorkspaceGuard(tmp_path)).review(state)

    assert review.status is ReviewStatus.FAILED
    assert "latest_test_not_passed" in review.findings


def test_missing_test_pending_approval_and_unconsumed_apply_are_incomplete(
    tmp_path: Path,
) -> None:
    state, context = _review_state(tmp_path)
    state["latest_test_result"] = None
    state["pending_approval"] = {"proposal_id": str(context.proposal_id)}
    state["applied_patch_context"] = context.model_dump(mode="json")

    review = DeterministicReviewer(WorkspaceGuard(tmp_path)).review(state)

    assert review.status is ReviewStatus.INCOMPLETE
    assert {
        "pending_approval_remains",
        "applied_patch_context_not_consumed",
        "latest_test_result_missing",
    }.issubset(review.findings)


def test_changed_file_hash_is_review_failure_without_writing(tmp_path: Path) -> None:
    state, _context = _review_state(tmp_path)
    (tmp_path / "a.py").write_bytes(b"changed later\n")

    review = DeterministicReviewer(WorkspaceGuard(tmp_path)).review(state)

    assert review.status is ReviewStatus.FAILED
    assert review.verified_patch_hash is False
    assert (tmp_path / "a.py").read_bytes() == b"changed later\n"


def test_mismatched_test_patch_and_duplicate_tool_message_fail(tmp_path: Path) -> None:
    state, context = _review_state(tmp_path)
    mismatched = make_test_result(TestOutcome.PASSED, exit_code=0).model_copy(
        update={"proposal_id": uuid4()}
    )
    state["latest_test_result"] = mismatched.model_dump(mode="json")
    state["messages"].append(
        ToolMessage(content="{}", tool_call_id=context.tool_call_id, name="propose_patch")
    )

    review = DeterministicReviewer(WorkspaceGuard(tmp_path)).review(state)

    assert review.status is ReviewStatus.FAILED
    assert "test_result_mismatch" in review.findings
    assert "duplicate_tool_message" in review.findings
