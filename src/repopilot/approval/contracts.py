"""Serializable approval requests, decisions, and service errors."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from repopilot.patching.proposal import PatchProposal
from repopilot.tools.contracts import TOOL_LIMITS, ToolErrorCode


class ApprovalDecisionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalDecisionRequest(BaseModel):
    """Only client-controlled input accepted by the resume endpoint."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    decision: ApprovalDecisionKind
    comment: str | None = Field(
        default=None,
        max_length=TOOL_LIMITS.max_approval_comment_characters,
    )

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ApprovalDecision(BaseModel):
    """Validated decision stored in graph state after resume."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: UUID
    decision: ApprovalDecisionKind
    comment: str | None = None
    valid: bool = True
    error_code: ToolErrorCode | None = None


class ApprovalRequestView(BaseModel):
    """Complete review payload safe to expose through the HTTP API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    proposal_id: UUID
    tool_call_id: str
    relative_path: str
    rationale: str
    unified_diff: str
    original_sha256: str
    proposed_sha256: str
    original_character_count: int
    proposed_character_count: int
    added_line_count: int
    removed_line_count: int
    created_at: str
    post_apply_verification: PostApplyVerification


class PostApplyVerification(BaseModel):
    """Fixed verification disclosed as part of the human approval contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runner: str = "pytest"
    target: str
    automatic: bool = True


class ApprovalServiceError(RuntimeError):
    """Stable public lookup/conflict error raised before graph resume."""

    def __init__(self, code: ToolErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def approval_view(
    run_id: str,
    proposal: PatchProposal,
    pytest_target: str = "tests",
) -> ApprovalRequestView:
    """Project an internal proposal without exposing its replacement content."""

    return ApprovalRequestView(
        run_id=run_id,
        proposal_id=proposal.proposal_id,
        tool_call_id=proposal.tool_call_id,
        relative_path=proposal.relative_path,
        rationale=proposal.rationale,
        unified_diff=proposal.unified_diff,
        original_sha256=proposal.original_sha256,
        proposed_sha256=proposal.proposed_sha256,
        original_character_count=proposal.original_character_count,
        proposed_character_count=proposal.proposed_character_count,
        added_line_count=proposal.added_line_count,
        removed_line_count=proposal.removed_line_count,
        created_at=proposal.created_at,
        post_apply_verification=PostApplyVerification(target=pytest_target),
    )
