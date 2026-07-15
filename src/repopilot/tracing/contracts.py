"""Allowlisted P6 trace event contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TraceEventType(StrEnum):
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    MODEL_COMPLETED = "model_completed"
    CONTEXT_COMPACTED = "context_compacted"
    TOOL_COMPLETED = "tool_completed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    PATCH_APPLIED = "patch_applied"
    PATCH_REJECTED = "patch_rejected"
    TESTS_COMPLETED = "tests_completed"
    REVIEW_COMPLETED = "review_completed"
    FINAL_REPORT_CREATED = "final_report_created"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_DELETED = "run_deleted"


class TraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_key: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=100)
    event_type: TraceEventType
    node_name: str | None = Field(default=None, max_length=80)
    phase: str = Field(min_length=1, max_length=80)
    status: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)


class TraceRecordingError(RuntimeError):
    """A visible failure to persist a required trace event."""
