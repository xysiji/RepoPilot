"""Fail-closed trace payload sanitization with small aggregate-only values."""

from __future__ import annotations

from typing import Any

_ALLOWED_KEYS = frozenset(
    {
        "approval_count",
        "compacted_block_count",
        "decision",
        "dropped_block_count",
        "error_code",
        "failure_category",
        "latest_test_outcome",
        "max_repair_attempts",
        "model_calls",
        "model_characters",
        "model_message_count",
        "node",
        "outcome",
        "original_characters",
        "original_message_count",
        "phase",
        "proposal_id",
        "relative_path",
        "original_sha256",
        "proposed_sha256",
        "original_character_count",
        "proposed_character_count",
        "added_line_count",
        "removed_line_count",
        "exit_code",
        "duration_ms",
        "repair_attempts",
        "review_status",
        "state_schema_version",
        "status",
        "tool_call_id",
        "tool_name",
        "tool_results_compacted",
    }
)
_SECRET_MARKERS = ("api_key", "authorization", "base_url", "secret", "token", "password")


def sanitize_trace_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only allowlisted small scalar metadata; reject suspicious keys."""

    safe: dict[str, Any] = {}
    for key, value in payload.items():
        lowered = key.casefold()
        if any(marker in lowered for marker in _SECRET_MARKERS):
            raise ValueError("trace payload contains a forbidden sensitive key")
        if key not in _ALLOWED_KEYS:
            continue
        if value is None or isinstance(value, (bool, int, float)):
            safe[key] = value
        elif isinstance(value, str):
            safe[key] = value[:200]
    return safe
