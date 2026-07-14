"""Defensive validation of values returned by LangGraph interrupt resume."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from repopilot.approval.contracts import (
    ApprovalDecision,
    ApprovalDecisionKind,
    ApprovalDecisionRequest,
)
from repopilot.tools.contracts import ToolErrorCode


def validate_resume_decision(raw: Any, expected_proposal_id: UUID) -> ApprovalDecision:
    """Convert untrusted resume data to a routing-safe decision without raising."""

    try:
        request = ApprovalDecisionRequest.model_validate(raw)
    except (ValidationError, TypeError, ValueError):
        return ApprovalDecision(
            proposal_id=expected_proposal_id,
            decision=ApprovalDecisionKind.REJECT,
            valid=False,
            error_code=ToolErrorCode.INVALID_APPROVAL_DECISION,
        )
    if request.proposal_id != expected_proposal_id:
        return ApprovalDecision(
            proposal_id=request.proposal_id,
            decision=ApprovalDecisionKind.REJECT,
            comment=request.comment,
            valid=False,
            error_code=ToolErrorCode.PROPOSAL_MISMATCH,
        )
    return ApprovalDecision(
        proposal_id=request.proposal_id,
        decision=request.decision,
        comment=request.comment,
    )
