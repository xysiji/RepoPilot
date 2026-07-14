"""HTTP and agent-run schemas; P3 tool contracts live in ``tools.contracts``."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from repopilot.tools.contracts import ToolExecutionRecord


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
