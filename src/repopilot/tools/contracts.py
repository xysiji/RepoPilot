"""Stable contracts for tool validation, policy, execution, and auditing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ToolEffect(StrEnum):
    """Side-effect class assigned by trusted Python code, never by the model."""

    READ_ONLY = "read_only"
    WRITE = "write"
    COMMAND = "command"
    UNKNOWN = "unknown"


class ToolPolicyAction(StrEnum):
    """Trusted three-way policy action decided by Python code."""

    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class ToolExecutionPhase(StrEnum):
    """The pipeline phase that completed or produced a failure."""

    DISPATCH = "dispatch"
    VALIDATION = "validation"
    POLICY = "policy"
    EXECUTION = "execution"
    NORMALIZATION = "normalization"
    PREPARATION = "preparation"
    APPROVAL = "approval"
    APPLY = "apply"
    VERIFICATION = "verification"
    TESTING = "testing"
    REVIEW = "review"
    REPORT = "report"


class ToolFailureCategory(StrEnum):
    """Stable high-level failure families."""

    INVALID_REQUEST = "invalid_request"
    POLICY_DENIED = "policy_denied"
    FILESYSTEM = "filesystem"
    UNSUPPORTED_CONTENT = "unsupported_content"
    RESOURCE_LIMIT = "resource_limit"
    EXECUTION_FAILURE = "execution_failure"
    INTERNAL_FAILURE = "internal_failure"
    APPROVAL = "approval"
    CONFLICT = "conflict"
    PATCH = "patch"
    TEST_FAILURE = "test_failure"
    TEST_INFRASTRUCTURE = "test_infrastructure"
    REPAIR_BUDGET = "repair_budget"
    REVIEW = "review"


class ToolErrorCode(StrEnum):
    """Stable machine-readable codes used in ToolMessages and API audit summaries."""

    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    UNCLASSIFIED_TOOL_EFFECT = "unclassified_tool_effect"
    SIDE_EFFECT_NOT_SUPPORTED = "side_effect_not_supported"
    INVALID_PATH = "invalid_path"
    ABSOLUTE_PATH_DENIED = "absolute_path_denied"
    PATH_TRAVERSAL_DENIED = "path_traversal_denied"
    OUTSIDE_WORKSPACE_DENIED = "outside_workspace_denied"
    SENSITIVE_PATH_DENIED = "sensitive_path_denied"
    LINK_PATH_DENIED = "link_path_denied"
    WINDOWS_DEVICE_PATH_DENIED = "windows_device_path_denied"
    NOT_FOUND = "not_found"
    NOT_A_FILE = "not_a_file"
    NOT_A_DIRECTORY = "not_a_directory"
    PERMISSION_DENIED = "permission_denied"
    BINARY_FILE = "binary_file"
    INVALID_ENCODING = "invalid_encoding"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    INVALID_TOOL_RESULT = "invalid_tool_result"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_BATCH_NOT_SUPPORTED = "approval_batch_not_supported"
    APPROVAL_NOT_STARTED_BUDGET_EXHAUSTED = "approval_not_started_budget_exhausted"
    INVALID_APPROVAL_DECISION = "invalid_approval_decision"
    PROPOSAL_MISMATCH = "proposal_mismatch"
    NO_PENDING_APPROVAL = "no_pending_approval"
    RUN_NOT_FOUND = "run_not_found"
    RUN_ALREADY_COMPLETED = "run_already_completed"
    PATCH_EMPTY = "patch_empty"
    PATCH_SOURCE_TOO_LARGE = "patch_source_too_large"
    PATCH_PROPOSED_CONTENT_TOO_LARGE = "patch_proposed_content_too_large"
    PATCH_DIFF_TOO_LARGE = "patch_diff_too_large"
    PATCH_CHANGED_LINES_EXCEEDED = "patch_changed_lines_exceeded"
    PATCH_TARGET_NOT_SUPPORTED = "patch_target_not_supported"
    PATCH_FILE_CREATION_NOT_SUPPORTED = "patch_file_creation_not_supported"
    STALE_PATCH = "stale_patch"
    PATCH_APPLY_FAILED = "patch_apply_failed"
    PATCH_VERIFICATION_FAILED = "patch_verification_failed"
    PYTEST_TESTS_FAILED = "pytest_tests_failed"
    PYTEST_INTERRUPTED = "pytest_interrupted"
    PYTEST_INTERNAL_ERROR = "pytest_internal_error"
    PYTEST_USAGE_ERROR = "pytest_usage_error"
    PYTEST_NO_TESTS_COLLECTED = "pytest_no_tests_collected"
    PYTEST_WARNINGS_EXCEEDED = "pytest_warnings_exceeded"
    PYTEST_TIMEOUT = "pytest_timeout"
    PYTEST_OUTPUT_LIMIT_EXCEEDED = "pytest_output_limit_exceeded"
    PYTEST_LAUNCH_ERROR = "pytest_launch_error"
    PYTEST_UNKNOWN_EXIT_CODE = "pytest_unknown_exit_code"
    REPAIR_ATTEMPTS_EXHAUSTED = "repair_attempts_exhausted"
    REPAIR_ABANDONED = "repair_abandoned"
    REVIEW_FAILED = "review_failed"
    TEST_RESULT_MISMATCH = "test_result_mismatch"


@dataclass(frozen=True)
class ToolLimits:
    """Central system ceilings; model arguments may only request smaller scopes."""

    max_path_length: int = 500
    max_list_depth: int = 5
    max_list_paths: int = 200
    max_read_characters: int = 20_000
    max_search_results: int = 100
    max_search_file_bytes: int = 256 * 1024
    max_search_depth: int = 8
    max_search_line_characters: int = 500
    max_query_length: int = 200
    max_suffix_length: int = 20
    max_patch_source_characters: int = 100_000
    max_patch_proposed_characters: int = 100_000
    max_patch_diff_characters: int = 200_000
    max_patch_changed_lines: int = 2_000
    max_patch_rationale_characters: int = 1_000
    max_approval_comment_characters: int = 1_000


TOOL_LIMITS = ToolLimits()


class ToolArgsModel(BaseModel):
    """Shared strictness for all model-controlled tool arguments."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="after", check_fields=False)
    @classmethod
    def reject_nul_in_strings(cls, value: Any) -> Any:
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("NUL is not allowed")
        return value


class ListFilesArgs(ToolArgsModel):
    directory: str = Field(default=".", min_length=1, max_length=TOOL_LIMITS.max_path_length)
    recursive: bool = Field(default=False, strict=True)
    max_depth: int = Field(default=2, ge=1, le=TOOL_LIMITS.max_list_depth, strict=True)


class ReadFileArgs(ToolArgsModel):
    path: str = Field(min_length=1, max_length=TOOL_LIMITS.max_path_length)


class SearchCodeArgs(ToolArgsModel):
    query: str = Field(min_length=1, max_length=TOOL_LIMITS.max_query_length)
    directory: str = Field(default=".", min_length=1, max_length=TOOL_LIMITS.max_path_length)
    file_suffix: str | None = Field(default=None, max_length=TOOL_LIMITS.max_suffix_length)
    max_results: int = Field(default=20, ge=1, le=TOOL_LIMITS.max_search_results, strict=True)

    @field_validator("query")
    @classmethod
    def query_must_contain_text(cls, value: str) -> str:
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("file_suffix")
    @classmethod
    def normalize_suffix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or any(character in value for character in ("/", "\\", ":", "*", "?")):
            raise ValueError("file suffix has an invalid format")
        normalized = value if value.startswith(".") else f".{value}"
        if normalized in {".", ".."} or any(character.isspace() for character in normalized):
            raise ValueError("file suffix has an invalid format")
        return normalized.casefold()


class ProposePatchInput(BaseModel):
    """Model input for one reviewable full-content replacement proposal."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    path: str = Field(min_length=1, max_length=TOOL_LIMITS.max_path_length)
    new_content: str = Field(max_length=TOOL_LIMITS.max_patch_proposed_characters)
    rationale: str = Field(min_length=1, max_length=TOOL_LIMITS.max_patch_rationale_characters)

    @field_validator("path", "new_content", "rationale")
    @classmethod
    def reject_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("NUL is not allowed")
        return value

    @field_validator("path", "rationale")
    @classmethod
    def require_non_whitespace_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain non-whitespace text")
        return value


class SearchMatch(BaseModel):
    path: str
    line_number: int
    line: str


class ToolFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: ToolExecutionPhase
    category: ToolFailureCategory
    code: ToolErrorCode
    message: str


class ToolResultEnvelope(BaseModel):
    """Exactly one of data or error is populated."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    data: dict[str, Any] | None
    error: ToolFailure | None

    @model_validator(mode="after")
    def result_shape_matches_success(self) -> Self:
        if self.success and (self.data is None or self.error is not None):
            raise ValueError("successful tool results require data and no error")
        if not self.success and (self.data is not None or self.error is None):
            raise ValueError("failed tool results require an error and no data")
        return self

    def stable_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )


class ToolPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: ToolPolicyAction | None = None
    allowed: bool
    effect: ToolEffect
    requires_approval: bool
    failure: ToolFailure | None = None

    @model_validator(mode="after")
    def derive_and_validate_action(self) -> Self:
        action = self.action
        if action is None:
            action = (
                ToolPolicyAction.REQUIRE_APPROVAL
                if self.requires_approval
                else ToolPolicyAction.ALLOW
                if self.allowed
                else ToolPolicyAction.DENY
            )
            object.__setattr__(self, "action", action)
        expected = {
            ToolPolicyAction.ALLOW: (True, False),
            ToolPolicyAction.REQUIRE_APPROVAL: (False, True),
            ToolPolicyAction.DENY: (False, False),
        }[action]
        if (self.allowed, self.requires_approval) != expected:
            raise ValueError("policy compatibility fields do not match action")
        if action is ToolPolicyAction.DENY and self.failure is None:
            raise ValueError("denied policy decisions require a failure")
        if action is not ToolPolicyAction.DENY and self.failure is not None:
            raise ValueError("non-denied policy decisions cannot include a failure")
        return self


class ToolExecutionRecord(BaseModel):
    """Sanitized audit record safe for the public P3 API response."""

    step: int
    tool_name: str
    tool_call_id: str
    input: dict[str, Any]
    success: bool
    output_summary: str
    error_type: str | None = None
    error_message: str | None = None
    phase: ToolExecutionPhase
    failure_category: ToolFailureCategory | None = None
    error_code: ToolErrorCode | None = None
    effect: ToolEffect = ToolEffect.UNKNOWN
    policy_allowed: bool | None = None


class ResourceLimitExceededError(RuntimeError):
    """Expected signal for a bounded tool that cannot safely truncate its result."""


def successful_result(data: dict[str, Any]) -> ToolResultEnvelope:
    return ToolResultEnvelope(success=True, data=data, error=None)


def failed_result(
    *,
    phase: ToolExecutionPhase,
    category: ToolFailureCategory,
    code: ToolErrorCode,
    message: str,
) -> ToolResultEnvelope:
    return ToolResultEnvelope(
        success=False,
        data=None,
        error=ToolFailure(phase=phase, category=category, code=code, message=message),
    )
