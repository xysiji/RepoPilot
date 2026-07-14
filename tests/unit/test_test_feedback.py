"""Best-effort output redaction and patch/test ToolMessage tests."""

import json
from pathlib import Path
from uuid import uuid4

from repopilot.testing.contracts import AppliedPatchContext, TestOutcome
from repopilot.testing.feedback import build_test_feedback_message, sanitize_test_output
from tests.fake_runner import make_test_result


def _context() -> AppliedPatchContext:
    return AppliedPatchContext(
        proposal_id=uuid4(),
        tool_call_id="patch-original",
        relative_path="src/a.py",
        original_sha256="a" * 64,
        proposed_sha256="b" * 64,
        added_line_count=1,
        removed_line_count=1,
        model_call=1,
    )


def test_output_cleanup_redacts_paths_secrets_tokens_ansi_and_controls(tmp_path: Path) -> None:
    interpreter = tmp_path / ".venv" / "Scripts" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"")
    secret = "known-secret-value"
    raw = f"\x1b[31m{tmp_path} {interpreter}\x1b[0m\x00\napi_key={secret} sk-1234567890abcdef"

    cleaned = sanitize_test_output(
        raw,
        workspace=tmp_path,
        interpreter=interpreter,
        known_secrets=[secret],
        max_characters=200,
    )

    assert "\x1b" not in cleaned and "\x00" not in cleaned
    assert str(tmp_path) not in cleaned and str(interpreter) not in cleaned
    assert secret not in cleaned and "sk-1234567890abcdef" not in cleaned
    assert "<python>" in cleaned and "<redacted>" in cleaned


def test_output_cleanup_enforces_final_character_limit(tmp_path: Path) -> None:
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"")

    cleaned = sanitize_test_output(
        "x" * 1000,
        workspace=tmp_path,
        interpreter=interpreter,
        max_characters=80,
    )

    assert len(cleaned) == 80
    assert cleaned.endswith("[output truncated]")


def test_failure_feedback_preserves_call_id_and_safe_patch_test_facts() -> None:
    context = _context()
    result = make_test_result(
        TestOutcome.TEST_FAILURES,
        exit_code=1,
        output="short safe failure",
    )

    message = build_test_feedback_message(context, result, attempt_number=2)
    payload = json.loads(str(message.content))

    assert message.tool_call_id == "patch-original"
    assert message.status == "error"
    assert payload["success"] is False
    assert payload["data"] == {
        "patch_applied": True,
        "proposal_id": str(context.proposal_id),
        "test_outcome": "test_failures",
        "exit_code": 1,
        "attempt": 2,
        "duration_ms": 10,
        "safe_output_excerpt": "short safe failure",
    }
    assert payload["error"]["code"] == "pytest_tests_failed"


def test_passing_feedback_is_success_without_test_output() -> None:
    context = _context()
    message = build_test_feedback_message(
        context,
        make_test_result(TestOutcome.PASSED, exit_code=0, output="private output"),
        attempt_number=1,
    )
    payload = json.loads(str(message.content))

    assert message.status == "success"
    assert payload["success"] is True and payload["error"] is None
    assert "safe_output_excerpt" not in payload["data"]
    assert "private output" not in str(message.content)
