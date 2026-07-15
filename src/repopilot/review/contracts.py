"""Safe deterministic review and final-report contracts."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from repopilot.testing.contracts import TestOutcome


class ReviewStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ReviewStatus
    findings: list[str] = Field(default_factory=list)
    verified_patch_hash: bool
    latest_test_outcome: TestOutcome | None
    repair_attempts: int = Field(ge=0)


class FinalReportOutcome(StrEnum):
    REPAIRED = "repaired"
    NO_CHANGE = "no_change"
    TESTS_FAILED = "tests_failed"
    REPAIR_ATTEMPTS_EXHAUSTED = "repair_attempts_exhausted"
    REPAIR_ABANDONED = "repair_abandoned"
    TEST_TIMEOUT = "test_timeout"
    TEST_INFRASTRUCTURE_ERROR = "test_infrastructure_error"
    PATCH_APPLY_FAILED = "patch_apply_failed"
    APPROVAL_REJECTED = "approval_rejected"
    MODEL_ERROR = "model_error"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    INVALID_MODEL_RESPONSE = "invalid_model_response"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    CONTEXT_PROTOCOL_ERROR = "context_protocol_error"


class FinalReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    outcome: FinalReportOutcome
    summary: str
    modified_files: list[str] = Field(default_factory=list)
    repair_attempts: int = Field(ge=0)
    max_repair_attempts: int = Field(ge=1)
    model_calls: int = Field(ge=0)
    approval_count: int = Field(ge=0)
    patches_applied: int = Field(ge=0)
    latest_test_outcome: TestOutcome | None
    latest_test_exit_code: int | None
    safe_test_summary: str
    review_status: ReviewStatus | None
    review_findings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    model_final_text: str = ""
