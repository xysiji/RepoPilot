"""Strict approval request and defensive resume validation tests."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from repopilot.approval.contracts import ApprovalDecisionRequest
from repopilot.approval.validation import validate_resume_decision
from repopilot.tools.contracts import TOOL_LIMITS


def test_approval_request_only_accepts_approve_or_reject() -> None:
    proposal_id = uuid4()
    approved = ApprovalDecisionRequest(proposal_id=proposal_id, decision="approve")
    assert approved.decision == "approve"
    assert ApprovalDecisionRequest(proposal_id=proposal_id, decision="reject").decision == "reject"
    with pytest.raises(ValidationError):
        ApprovalDecisionRequest(proposal_id=proposal_id, decision="edit")


def test_approval_request_rejects_patch_edits_and_long_comments() -> None:
    with pytest.raises(ValidationError):
        ApprovalDecisionRequest.model_validate(
            {"proposal_id": str(uuid4()), "decision": "approve", "new_content": "forged"}
        )
    with pytest.raises(ValidationError):
        ApprovalDecisionRequest(
            proposal_id=uuid4(),
            decision="reject",
            comment="x" * (TOOL_LIMITS.max_approval_comment_characters + 1),
        )


def test_resume_validation_turns_invalid_or_mismatched_data_into_rejection() -> None:
    expected = uuid4()
    invalid = validate_resume_decision({"decision": "approve"}, expected)
    mismatch = validate_resume_decision(
        {"proposal_id": str(uuid4()), "decision": "approve"},
        expected,
    )

    assert invalid.valid is False and invalid.error_code == "invalid_approval_decision"
    assert mismatch.valid is False and mismatch.error_code == "proposal_mismatch"
    assert invalid.decision == mismatch.decision == "reject"
