"""Stable contracts for P3 tool validation, policy, execution, and auditing."""

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


class ToolExecutionPhase(StrEnum):
    """The pipeline phase that completed or produced a failure."""

    DISPATCH = "dispatch"
    VALIDATION = "validation"
    POLICY = "policy"
    EXECUTION = "execution"
    NORMALIZATION = "normalization"


class ToolFailureCategory(StrEnum):
    """Stable high-level failure families."""

    INVALID_REQUEST = "invalid_request"
    POLICY_DENIED = "policy_denied"
    FILESYSTEM = "filesystem"
    UNSUPPORTED_CONTENT = "unsupported_content"
    RESOURCE_LIMIT = "resource_limit"
    EXECUTION_FAILURE = "execution_failure"
    INTERNAL_FAILURE = "internal_failure"


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

    allowed: bool
    effect: ToolEffect
    requires_approval: bool
    failure: ToolFailure | None = None


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
