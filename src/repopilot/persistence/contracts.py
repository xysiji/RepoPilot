"""Typed contracts for the small P6 runtime index."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PersistenceError(RuntimeError):
    """Base error for startup and runtime persistence failures."""


class UnsupportedSchemaVersionError(PersistenceError):
    """Raised when a database or checkpoint is newer than this process."""


class RunNotFoundError(PersistenceError):
    """Raised when a run is absent or has been deleted."""


class RunNotTerminalError(PersistenceError):
    """Raised when cleanup is requested for a live run."""


class RunCleanupError(PersistenceError):
    """Raised when best-effort cleanup cannot complete."""


class TraceLimitExceededError(PersistenceError):
    """Raised when a run reaches the configured local trace bound."""


class RunRecord(BaseModel):
    """Safe run metadata; it deliberately excludes messages and source content."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    thread_id: str
    state_schema_version: int
    status: str
    outcome: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    current_proposal_id: str | None = None
    repair_attempts: int = 0
    max_repair_attempts: int = 0
    model_calls: int = 0
    latest_test_outcome: str | None = None
    review_status: str | None = None
    goal_sha256: str
    final_report: dict[str, Any] | None = None
    cleanup_status: str | None = None


class RunPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[RunRecord]
    next_cursor: str | None = None


class TraceRecord(BaseModel):
    """One redacted, structured runtime event."""

    model_config = ConfigDict(frozen=True)

    event_id: int
    event_key: str
    run_id: str
    event_type: str
    node_name: str | None = None
    phase: str
    status: str
    created_at: datetime
    safe_payload: dict[str, Any] = Field(default_factory=dict)


TERMINAL_RUN_STATUSES = frozenset(
    {
        "success",
        "repaired",
        "no_change",
        "tests_failed",
        "max_steps_exceeded",
        "model_error",
        "invalid_model_response",
        "context_budget_exceeded",
        "context_protocol_error",
        "repair_attempts_exhausted",
        "repair_abandoned",
        "test_timeout",
        "test_infrastructure_error",
        "patch_apply_failed",
        "approval_rejected",
    }
)
