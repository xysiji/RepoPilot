"""Safe HTTP schemas for starting and resuming an agent run."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from repopilot.approval.contracts import ApprovalRequestView
from repopilot.review.contracts import FinalReport
from repopilot.tools.contracts import ToolExecutionRecord


class AgentRunError(BaseModel):
    code: Literal[
        "max_steps_exceeded",
        "model_error",
        "invalid_model_response",
        "repair_attempts_exhausted",
        "repair_abandoned",
        "test_timeout",
        "test_infrastructure_error",
        "patch_apply_failed",
        "approval_rejected",
        "context_budget_exceeded",
        "context_protocol_error",
        "checkpoint_incompatible",
    ]
    message: str


class AgentRunResult(BaseModel):
    run_id: str
    status: Literal[
        "success",
        "max_steps_exceeded",
        "model_error",
        "invalid_model_response",
        "awaiting_approval",
        "repaired",
        "no_change",
        "tests_failed",
        "repair_attempts_exhausted",
        "repair_abandoned",
        "test_timeout",
        "test_infrastructure_error",
        "patch_apply_failed",
        "approval_rejected",
        "context_budget_exceeded",
        "context_protocol_error",
        "checkpoint_incompatible",
    ]
    final_answer: str = ""
    steps: int
    tool_executions: list[ToolExecutionRecord] = Field(default_factory=list)
    message_count: int
    error: AgentRunError | None = None
    approval: ApprovalRequestView | None = None
    final_report: FinalReport | None = None


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=4000)
    max_steps: int = Field(default=6, ge=1, le=10)
    max_repair_attempts: int | None = Field(default=None, ge=1, le=5)

    @field_validator("goal")
    @classmethod
    def goal_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        return value


class AgentRunView(BaseModel):
    """Safe restart-aware projection of a persisted run."""

    run_id: str
    status: str
    outcome: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    awaiting_approval: bool
    current_proposal_id: str | None = None
    repair_attempts: int
    max_repair_attempts: int
    model_calls: int
    latest_test_outcome: str | None = None
    review_status: str | None = None
    approval: ApprovalRequestView | None = None
    final_report: FinalReport | None = None
    latest_context_stats: dict[str, int] | None = None


class AgentRunSummary(BaseModel):
    run_id: str
    status: str
    outcome: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    awaiting_approval: bool
    current_proposal_id: str | None = None
    repair_attempts: int
    max_repair_attempts: int
    model_calls: int
    latest_test_outcome: str | None = None
    review_status: str | None = None


class AgentRunListResponse(BaseModel):
    items: list[AgentRunSummary]
    next_cursor: str | None = None


class TraceEventView(BaseModel):
    event_id: int
    event_key: str
    event_type: str
    node_name: str | None = None
    phase: str
    status: str
    created_at: datetime
    safe_payload: dict[str, Any]


class TraceEventListResponse(BaseModel):
    items: list[TraceEventView]


class DeleteRunResponse(BaseModel):
    run_id: str
    deleted: bool = True
