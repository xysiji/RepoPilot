"""Safe deterministic final-report outcome tests."""

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from repopilot.agent.state import create_initial_state
from repopilot.review.contracts import FinalReportOutcome, ReviewResult, ReviewStatus
from repopilot.review.report import FinalReportBuilder
from repopilot.testing.contracts import AppliedPatchContext, TestOutcome
from tests.fake_runner import make_test_result


def _state_with_patch(tmp_path: Path, outcome: TestOutcome):
    content = b"fixed\n"
    (tmp_path / "a.py").write_bytes(content)
    proposal_id = uuid4()
    context = AppliedPatchContext(
        proposal_id=proposal_id,
        tool_call_id="patch",
        relative_path="a.py",
        original_sha256="a" * 64,
        proposed_sha256=hashlib.sha256(content).hexdigest(),
        added_line_count=1,
        removed_line_count=1,
        model_call=1,
    )
    result = make_test_result(
        outcome,
        exit_code=0 if outcome is TestOutcome.PASSED else 1,
        output="short safe output",
    ).model_copy(update={"proposal_id": proposal_id})
    state = create_initial_state("goal", 4, max_repair_attempts=2)
    state["applied_patches"].append(context.model_dump(mode="json"))
    state["latest_test_result"] = result.model_dump(mode="json")
    state["repair_attempts"] = 1
    return state


def test_passing_review_produces_repaired_safe_report(tmp_path: Path) -> None:
    state = _state_with_patch(tmp_path, TestOutcome.PASSED)
    state["review_result"] = ReviewResult(
        status=ReviewStatus.PASSED,
        findings=[],
        verified_patch_hash=True,
        latest_test_outcome=TestOutcome.PASSED,
        repair_attempts=1,
    ).model_dump(mode="json")

    report = FinalReportBuilder().build(state)

    assert report.outcome is FinalReportOutcome.REPAIRED
    assert report.modified_files == ["a.py"]
    assert report.latest_test_exit_code == 0
    serialized = report.model_dump_json()
    assert str(tmp_path) not in serialized
    assert "messages" not in serialized and "proposed_content" not in serialized


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        ("repair_attempts_exhausted", FinalReportOutcome.REPAIR_ATTEMPTS_EXHAUSTED),
        ("repair_abandoned", FinalReportOutcome.REPAIR_ABANDONED),
        ("test_timeout", FinalReportOutcome.TEST_TIMEOUT),
        ("test_infrastructure_error", FinalReportOutcome.TEST_INFRASTRUCTURE_ERROR),
        ("model_error", FinalReportOutcome.MODEL_ERROR),
        ("approval_rejected", FinalReportOutcome.APPROVAL_REJECTED),
        ("max_steps_exceeded", FinalReportOutcome.MAX_STEPS_EXCEEDED),
    ],
)
def test_terminal_statuses_never_use_success_language(
    tmp_path: Path,
    status: str,
    outcome: FinalReportOutcome,
) -> None:
    state = _state_with_patch(tmp_path, TestOutcome.TEST_FAILURES)
    state["status"] = status  # type: ignore[typeddict-item]

    report = FinalReportBuilder().build(state)

    assert report.outcome is outcome
    assert "passed" not in report.summary.casefold()


def test_no_patch_direct_answer_is_no_change_and_model_text_is_bounded() -> None:
    state = create_initial_state("goal", 2)
    state["status"] = "success"
    state["model_final_text"] = "model explanation"

    report = FinalReportBuilder().build(state)

    assert report.outcome is FinalReportOutcome.NO_CHANGE
    assert report.model_final_text == "model explanation"
    assert report.patches_applied == 0 and report.modified_files == []
