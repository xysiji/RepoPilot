"""Safe HTTP schemas for starting and resuming an agent run."""

from typing import Literal

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
