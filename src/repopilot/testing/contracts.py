"""JSON-safe contracts for fixed pytest execution and patch verification."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TestOutcome(StrEnum):
    """Deterministic result classes derived by Python, never by the model."""

    PASSED = "passed"
    TEST_FAILURES = "test_failures"
    INTERRUPTED = "interrupted"
    PYTEST_INTERNAL_ERROR = "pytest_internal_error"
    PYTEST_USAGE_ERROR = "pytest_usage_error"
    NO_TESTS_COLLECTED = "no_tests_collected"
    WARNINGS_EXCEEDED = "warnings_exceeded"
    TIMEOUT = "timeout"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    LAUNCH_ERROR = "launch_error"
    UNKNOWN_EXIT_CODE = "unknown_exit_code"

    __test__ = False


class TestRunResult(BaseModel):
    """One bounded runner result before graph-specific attempt metadata is added."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    __test__ = False

    outcome: TestOutcome
    exit_code: int | None
    duration_ms: int = Field(ge=0)
    timed_out: bool
    output_truncated: bool
    safe_output_excerpt: str
    started_at: str
    finished_at: str
    proposal_id: UUID | None = None


class TestRunRecord(BaseModel):
    """Sanitized, bounded audit record stored in graph state and exposed by the API."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    __test__ = False

    attempt_number: int = Field(ge=1)
    proposal_id: UUID
    outcome: TestOutcome
    exit_code: int | None
    duration_ms: int = Field(ge=0)
    timed_out: bool
    output_truncated: bool
    safe_output_excerpt: str
    command_display: str
    working_directory: str = "."
    started_at: str
    finished_at: str

    @classmethod
    def from_result(
        cls,
        result: TestRunResult,
        *,
        attempt_number: int,
        proposal_id: UUID,
        command_display: str,
    ) -> TestRunRecord:
        return cls(
            attempt_number=attempt_number,
            proposal_id=proposal_id,
            outcome=result.outcome,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            output_truncated=result.output_truncated,
            safe_output_excerpt=result.safe_output_excerpt,
            command_display=command_display,
            started_at=result.started_at,
            finished_at=result.finished_at,
        )


class AppliedPatchContext(BaseModel):
    """Safe evidence passed exactly once from ApplyPatch to Tester."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: UUID
    tool_call_id: str
    tool_name: str = "propose_patch"
    relative_path: str
    original_sha256: str
    proposed_sha256: str
    added_line_count: int = Field(ge=0)
    removed_line_count: int = Field(ge=0)
    model_call: int = Field(ge=1)
    approved: bool = True


class TestRunner(Protocol):
    """Minimal injectable boundary used by Tester; production uses PytestRunner."""

    target: str
    command_display: str
    __test__ = False

    async def run(self) -> TestRunResult: ...


def classify_pytest_exit_code(exit_code: int) -> TestOutcome:
    """Map the complete public pytest exit-code range plus unknown values."""

    return {
        0: TestOutcome.PASSED,
        1: TestOutcome.TEST_FAILURES,
        2: TestOutcome.INTERRUPTED,
        3: TestOutcome.PYTEST_INTERNAL_ERROR,
        4: TestOutcome.PYTEST_USAGE_ERROR,
        5: TestOutcome.NO_TESTS_COLLECTED,
        6: TestOutcome.WARNINGS_EXCEEDED,
    }.get(exit_code, TestOutcome.UNKNOWN_EXIT_CODE)
