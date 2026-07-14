"""Bounded redaction and ToolMessage construction for pytest feedback."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage
from pydantic import BaseModel, ConfigDict

from repopilot.testing.contracts import AppliedPatchContext, TestOutcome, TestRunResult
from repopilot.tools.contracts import (
    ToolErrorCode,
    ToolExecutionPhase,
    ToolFailureCategory,
)

_ANSI_ESCAPE = re.compile(r"\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_TOKEN = re.compile(r"(?i)\b(?:sk|rk|pk)-[a-z0-9_-]{8,}\b")
_NAMED_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|authorization)"
    r"\s*([:=])\s*(?:bearer\s+)?[^\s,;]+"
)
_TRUNCATION_MARKER = "\n[output truncated]"


class TestFeedbackEnvelope(BaseModel):
    """Patch/test feedback may contain safe facts alongside a stable failure."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    data: dict[str, Any]
    error: dict[str, str] | None

    def stable_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )


def sanitize_test_output(
    raw: str,
    *,
    workspace: Path,
    interpreter: Path,
    known_secrets: Iterable[str] = (),
    max_characters: int,
) -> str:
    """Best-effort cleanup and bounded redaction for untrusted test output."""

    cleaned = _ANSI_ESCAPE.sub("", raw).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "".join(
        character
        for character in cleaned
        if character in {"\n", "\t"} or (ord(character) >= 32 and ord(character) != 127)
    )
    replacements: list[tuple[str, str]] = []
    workspace_text = str(workspace.resolve())
    interpreter_text = str(interpreter.resolve())
    for value, marker in (
        (workspace_text, "<workspace>"),
        (workspace_text.replace("\\", "/"), "<workspace>"),
        (interpreter_text, "<python>"),
        (interpreter_text.replace("\\", "/"), "<python>"),
    ):
        replacements.append((value, marker))
    replacements.extend((secret, "<redacted>") for secret in known_secrets if secret)
    for value, marker in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        cleaned = re.sub(re.escape(value), marker, cleaned, flags=re.IGNORECASE)
    cleaned = _TOKEN.sub("<redacted-token>", cleaned)
    cleaned = _NAMED_SECRET.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        cleaned,
    )
    if len(cleaned) > max_characters:
        keep = max(0, max_characters - len(_TRUNCATION_MARKER))
        return cleaned[:keep] + _TRUNCATION_MARKER
    return cleaned


def build_test_feedback_message(
    context: AppliedPatchContext,
    result: TestRunResult,
    *,
    attempt_number: int,
    model_excerpt_characters: int = 4000,
) -> ToolMessage:
    """Create the single ToolMessage resolving one successful patch application."""

    success = result.outcome is TestOutcome.PASSED
    data: dict[str, Any] = {
        "patch_applied": True,
        "proposal_id": str(context.proposal_id),
        "test_outcome": result.outcome.value,
        "exit_code": result.exit_code,
        "attempt": attempt_number,
        "duration_ms": result.duration_ms,
    }
    if not success and result.safe_output_excerpt:
        data["safe_output_excerpt"] = result.safe_output_excerpt[:model_excerpt_characters]
    error = None
    if not success:
        error = {
            "phase": ToolExecutionPhase.TESTING.value,
            "category": test_failure_category(result.outcome).value,
            "code": test_error_code(result.outcome).value,
            "message": test_error_message(result.outcome),
        }
    envelope = TestFeedbackEnvelope(success=success, data=data, error=error)
    return ToolMessage(
        content=envelope.stable_json(),
        tool_call_id=context.tool_call_id,
        name=context.tool_name,
        status="success" if success else "error",
    )


def test_failure_category(outcome: TestOutcome) -> ToolFailureCategory:
    if outcome is TestOutcome.TEST_FAILURES:
        return ToolFailureCategory.TEST_FAILURE
    return ToolFailureCategory.TEST_INFRASTRUCTURE


def test_error_code(outcome: TestOutcome) -> ToolErrorCode:
    return {
        TestOutcome.TEST_FAILURES: ToolErrorCode.PYTEST_TESTS_FAILED,
        TestOutcome.INTERRUPTED: ToolErrorCode.PYTEST_INTERRUPTED,
        TestOutcome.PYTEST_INTERNAL_ERROR: ToolErrorCode.PYTEST_INTERNAL_ERROR,
        TestOutcome.PYTEST_USAGE_ERROR: ToolErrorCode.PYTEST_USAGE_ERROR,
        TestOutcome.NO_TESTS_COLLECTED: ToolErrorCode.PYTEST_NO_TESTS_COLLECTED,
        TestOutcome.WARNINGS_EXCEEDED: ToolErrorCode.PYTEST_WARNINGS_EXCEEDED,
        TestOutcome.TIMEOUT: ToolErrorCode.PYTEST_TIMEOUT,
        TestOutcome.OUTPUT_LIMIT_EXCEEDED: ToolErrorCode.PYTEST_OUTPUT_LIMIT_EXCEEDED,
        TestOutcome.LAUNCH_ERROR: ToolErrorCode.PYTEST_LAUNCH_ERROR,
        TestOutcome.UNKNOWN_EXIT_CODE: ToolErrorCode.PYTEST_UNKNOWN_EXIT_CODE,
    }[outcome]


def test_error_message(outcome: TestOutcome) -> str:
    return {
        TestOutcome.TEST_FAILURES: "The approved patch was applied, but project tests failed.",
        TestOutcome.INTERRUPTED: "Pytest reported an interrupted test run.",
        TestOutcome.PYTEST_INTERNAL_ERROR: "Pytest reported an internal error.",
        TestOutcome.PYTEST_USAGE_ERROR: "The fixed pytest invocation was rejected.",
        TestOutcome.NO_TESTS_COLLECTED: "Pytest did not collect any tests.",
        TestOutcome.WARNINGS_EXCEEDED: "Pytest exceeded its warning limit.",
        TestOutcome.TIMEOUT: "The fixed pytest run exceeded its time limit.",
        TestOutcome.OUTPUT_LIMIT_EXCEEDED: "The fixed pytest run exceeded its output limit.",
        TestOutcome.LAUNCH_ERROR: "The fixed pytest process could not be started.",
        TestOutcome.UNKNOWN_EXIT_CODE: "Pytest returned an unknown exit code.",
    }[outcome]
