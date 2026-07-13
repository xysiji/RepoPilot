"""Pydantic contracts for P1 read-only tools, loop results, and HTTP input."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ListFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = Field(
        default=".",
        min_length=1,
        max_length=500,
        description="Workspace-relative directory to list.",
    )
    recursive: bool = Field(default=False, description="Whether to descend into subdirectories.")
    max_depth: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Maximum recursive directory depth when recursive is true.",
    )


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        min_length=1,
        max_length=500,
        description="Workspace-relative UTF-8 text file to read.",
    )


class SearchCodeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        max_length=200, description="Literal text to find; whitespace-only is invalid."
    )
    directory: str = Field(
        default=".",
        min_length=1,
        max_length=500,
        description="Workspace-relative directory to search.",
    )
    file_suffix: str | None = Field(
        default=None,
        max_length=20,
        description="Optional file suffix filter, such as .py or py.",
    )
    max_results: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of matching lines to return.",
    )


class ListFilesResult(BaseModel):
    success: bool
    paths: list[str] = Field(default_factory=list)
    truncated: bool = False
    error_type: str | None = None
    error_message: str | None = None


class ReadFileResult(BaseModel):
    success: bool
    path: str | None = None
    content: str = ""
    character_count: int = 0
    truncated: bool = False
    error_type: str | None = None
    error_message: str | None = None


class SearchMatch(BaseModel):
    path: str
    line_number: int
    line: str


class SearchCodeResult(BaseModel):
    success: bool
    matches: list[SearchMatch] = Field(default_factory=list)
    truncated: bool = False
    error_type: str | None = None
    error_message: str | None = None


class ToolExecutionRecord(BaseModel):
    step: int
    tool_name: str
    tool_call_id: str
    input: dict[str, Any]
    success: bool
    output_summary: str
    error_type: str | None = None
    error_message: str | None = None


class AgentRunError(BaseModel):
    code: Literal["max_steps_exceeded", "model_error", "invalid_model_response"]
    message: str


class AgentRunResult(BaseModel):
    status: Literal["success", "max_steps_exceeded", "model_error", "invalid_model_response"]
    final_answer: str = ""
    steps: int
    tool_executions: list[ToolExecutionRecord] = Field(default_factory=list)
    message_count: int
    error: AgentRunError | None = None


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=4000)
    max_steps: int = Field(default=6, ge=1, le=10)

    @field_validator("goal")
    @classmethod
    def goal_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        return value
