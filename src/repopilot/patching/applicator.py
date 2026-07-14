"""Revalidate and atomically apply one approved P4 patch proposal."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

from repopilot.approval.contracts import ApprovalDecision, ApprovalDecisionKind
from repopilot.patching.proposal import PatchProposal, proposal_review_matches_content
from repopilot.tools.contracts import (
    TOOL_LIMITS,
    ToolErrorCode,
    ToolExecutionPhase,
    ToolFailureCategory,
    ToolResultEnvelope,
    failed_result,
    successful_result,
)
from repopilot.tools.policy import WorkspaceGuard, WorkspacePolicyError

_MAX_SOURCE_BYTES = TOOL_LIMITS.max_patch_source_characters * 4 + 4


class PatchApplicator:
    """The only production component allowed to write a workspace file in P4."""

    def __init__(self, workspace_guard: WorkspaceGuard) -> None:
        self._guard = workspace_guard

    @property
    def workspace_guard(self) -> WorkspaceGuard:
        """Expose the shared boundary for P5 verification composition."""

        return self._guard

    def apply(
        self,
        proposal: PatchProposal,
        decision: ApprovalDecision,
    ) -> ToolResultEnvelope:
        if (
            not decision.valid
            or decision.decision is not ApprovalDecisionKind.APPROVE
            or decision.proposal_id != proposal.proposal_id
        ):
            return _failure(
                ToolExecutionPhase.APPROVAL,
                ToolFailureCategory.APPROVAL,
                ToolErrorCode.INVALID_APPROVAL_DECISION,
                "A matching approval is required before applying a patch.",
            )

        try:
            target = self._guard.resolve_existing(proposal.relative_path)
        except (FileNotFoundError, WorkspacePolicyError):
            return _failure(
                ToolExecutionPhase.APPLY,
                ToolFailureCategory.POLICY_DENIED,
                ToolErrorCode.PATCH_TARGET_NOT_SUPPORTED,
                "The patch target no longer satisfies the workspace policy.",
            )
        try:
            target_stat = target.stat()
        except OSError:
            return _apply_failed()
        if not stat.S_ISREG(target_stat.st_mode):
            return _failure(
                ToolExecutionPhase.APPLY,
                ToolFailureCategory.PATCH,
                ToolErrorCode.PATCH_TARGET_NOT_SUPPORTED,
                "Only an existing ordinary file can be patched.",
            )

        current = _read_current(target)
        if isinstance(current, ToolResultEnvelope):
            return current
        if _sha256_text(current) != proposal.original_sha256:
            return _failure(
                ToolExecutionPhase.APPLY,
                ToolFailureCategory.CONFLICT,
                ToolErrorCode.STALE_PATCH,
                "The target changed after the proposal was created.",
            )
        if (
            len(proposal.proposed_content) > TOOL_LIMITS.max_patch_proposed_characters
            or "\x00" in proposal.proposed_content
            or _sha256_text(proposal.proposed_content) != proposal.proposed_sha256
            or not proposal_review_matches_content(proposal, current)
        ):
            return _verification_failed("The proposed content failed integrity validation.")
        if (
            len(proposal.unified_diff) > TOOL_LIMITS.max_patch_diff_characters
            or proposal.added_line_count + proposal.removed_line_count
            > TOOL_LIMITS.max_patch_changed_lines
        ):
            return _failure(
                ToolExecutionPhase.VERIFICATION,
                ToolFailureCategory.RESOURCE_LIMIT,
                ToolErrorCode.PATCH_VERIFICATION_FAILED,
                "The proposal no longer satisfies fixed resource limits.",
            )

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=".repopilot-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(proposal.proposed_content.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, stat.S_IMODE(target_stat.st_mode))
            os.replace(temporary_path, target)
            temporary_path = None
        except OSError:
            return _apply_failed()
        finally:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)

        verified = _read_current(target)
        if isinstance(verified, ToolResultEnvelope):
            return _verification_failed("The applied file could not be verified.")
        if _sha256_text(verified) != proposal.proposed_sha256:
            return _verification_failed("The applied file hash did not match the proposal.")
        return successful_result(
            {
                "proposal_id": str(proposal.proposal_id),
                "path": proposal.relative_path,
                "proposed_sha256": proposal.proposed_sha256,
                "added_line_count": proposal.added_line_count,
                "removed_line_count": proposal.removed_line_count,
            }
        )


def _read_current(path: Path) -> str | ToolResultEnvelope:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_SOURCE_BYTES + 1)
    except OSError:
        return _apply_failed()
    if len(raw) > _MAX_SOURCE_BYTES:
        return _failure(
            ToolExecutionPhase.VERIFICATION,
            ToolFailureCategory.RESOURCE_LIMIT,
            ToolErrorCode.PATCH_SOURCE_TOO_LARGE,
            "The target exceeds the fixed source size limit.",
        )
    if b"\x00" in raw:
        return _failure(
            ToolExecutionPhase.VERIFICATION,
            ToolFailureCategory.UNSUPPORTED_CONTENT,
            ToolErrorCode.PATCH_TARGET_NOT_SUPPORTED,
            "The target is no longer supported text content.",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _failure(
            ToolExecutionPhase.VERIFICATION,
            ToolFailureCategory.UNSUPPORTED_CONTENT,
            ToolErrorCode.PATCH_TARGET_NOT_SUPPORTED,
            "The target is no longer valid UTF-8 text.",
        )
    if len(text) > TOOL_LIMITS.max_patch_source_characters:
        return _failure(
            ToolExecutionPhase.VERIFICATION,
            ToolFailureCategory.RESOURCE_LIMIT,
            ToolErrorCode.PATCH_SOURCE_TOO_LARGE,
            "The target exceeds the fixed source size limit.",
        )
    return text


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _failure(
    phase: ToolExecutionPhase,
    category: ToolFailureCategory,
    code: ToolErrorCode,
    message: str,
) -> ToolResultEnvelope:
    return failed_result(phase=phase, category=category, code=code, message=message)


def _apply_failed() -> ToolResultEnvelope:
    return _failure(
        ToolExecutionPhase.APPLY,
        ToolFailureCategory.PATCH,
        ToolErrorCode.PATCH_APPLY_FAILED,
        "The patch could not be applied atomically.",
    )


def _verification_failed(message: str) -> ToolResultEnvelope:
    return _failure(
        ToolExecutionPhase.VERIFICATION,
        ToolFailureCategory.PATCH,
        ToolErrorCode.PATCH_VERIFICATION_FAILED,
        message,
    )
