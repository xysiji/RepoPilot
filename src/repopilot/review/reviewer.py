"""Deterministic evidence reviewer for patch, hash, test, and state consistency."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping

from langchain_core.messages import ToolMessage

from repopilot.review.contracts import ReviewResult, ReviewStatus
from repopilot.testing.contracts import AppliedPatchContext, TestOutcome, TestRunResult
from repopilot.tools.policy import WorkspaceGuard, WorkspacePolicyError


class DeterministicReviewer:
    """Verify workflow evidence without a model, subprocess, or file write."""

    def __init__(self, workspace_guard: WorkspaceGuard) -> None:
        self._guard = workspace_guard

    def review(self, state: Mapping[str, object]) -> ReviewResult:
        failed: list[str] = []
        incomplete: list[str] = []

        if state.get("pending_approval") is not None:
            incomplete.append("pending_approval_remains")
        if state.get("applied_patch_context") is not None:
            incomplete.append("applied_patch_context_not_consumed")

        contexts = _patch_contexts(state.get("applied_patches"))
        latest_patch = contexts[-1] if contexts else None
        latest_test = _latest_test(state.get("latest_test_result"))
        repair_attempts = int(state.get("repair_attempts", 0))
        max_attempts = int(state.get("max_repair_attempts", 1))

        call_ids = [
            message.tool_call_id
            for message in state.get("messages", [])  # type: ignore[union-attr]
            if isinstance(message, ToolMessage)
        ]
        duplicates = {call_id for call_id, count in Counter(call_ids).items() if count > 1}
        if duplicates:
            failed.append("duplicate_tool_message")

        verified_hash = False
        if latest_patch is None:
            incomplete.append("no_applied_patch")
        else:
            matching_messages = call_ids.count(latest_patch.tool_call_id)
            if matching_messages == 0:
                incomplete.append("patch_tool_message_missing")
            try:
                path = self._guard.resolve_existing(latest_patch.relative_path)
                verified_hash = hashlib.sha256(path.read_bytes()).hexdigest() == (
                    latest_patch.proposed_sha256
                )
            except (FileNotFoundError, OSError, WorkspacePolicyError):
                verified_hash = False
            if not verified_hash:
                failed.append("latest_patch_hash_mismatch")

        if latest_test is None:
            incomplete.append("latest_test_result_missing")
        elif latest_patch is not None and latest_test.proposal_id != latest_patch.proposal_id:
            failed.append("test_result_mismatch")

        if repair_attempts > max_attempts:
            failed.append("repair_budget_exceeded")
        if latest_test is not None:
            if latest_test.outcome is not TestOutcome.PASSED:
                failed.append("latest_test_not_passed")
            if latest_test.exit_code != 0:
                failed.append("latest_test_exit_code_not_zero")

        if failed:
            status = ReviewStatus.FAILED
            findings = [*failed, *incomplete]
        elif incomplete:
            status = ReviewStatus.INCOMPLETE
            findings = incomplete
        else:
            status = ReviewStatus.PASSED
            findings = []
        return ReviewResult(
            status=status,
            findings=findings,
            verified_patch_hash=verified_hash,
            latest_test_outcome=latest_test.outcome if latest_test else None,
            repair_attempts=repair_attempts,
        )


def _patch_contexts(raw: object) -> list[AppliedPatchContext]:
    if not isinstance(raw, list):
        return []
    contexts: list[AppliedPatchContext] = []
    for value in raw:
        try:
            contexts.append(AppliedPatchContext.model_validate(value))
        except (TypeError, ValueError):
            continue
    return contexts


def _latest_test(raw: object) -> TestRunResult | None:
    if raw is None:
        return None
    try:
        return TestRunResult.model_validate(raw)
    except (TypeError, ValueError):
        return None
