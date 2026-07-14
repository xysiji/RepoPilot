"""Build a reviewable proposal from current and proposed full file content."""

from __future__ import annotations

import difflib
import hashlib
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from repopilot.tools.contracts import (
    TOOL_LIMITS,
    ToolErrorCode,
    ToolExecutionPhase,
    ToolFailure,
    ToolFailureCategory,
)
from repopilot.tools.policy import WorkspaceGuard, WorkspacePolicyError

_MAX_SOURCE_BYTES = TOOL_LIMITS.max_patch_source_characters * 4 + 4
_NO_NEWLINE_SENTINEL = "\x00"


class PatchProposal(BaseModel):
    """Checkpoint-safe proposal; API projections must omit ``proposed_content``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: UUID
    tool_call_id: str
    tool_name: str
    relative_path: str
    rationale: str
    original_sha256: str
    proposed_sha256: str
    unified_diff: str
    original_character_count: int
    proposed_character_count: int
    added_line_count: int
    removed_line_count: int
    created_at: str
    proposed_content: str


class PatchPreparationError(RuntimeError):
    """Expected, safe proposal-preparation failure."""

    def __init__(self, failure: ToolFailure) -> None:
        super().__init__(failure.code.value)
        self.failure = failure


class PatchProposalBuilder:
    """Read one safe existing text file and compute a complete unified diff."""

    def __init__(self, workspace_guard: WorkspaceGuard) -> None:
        self._guard = workspace_guard

    def build(
        self,
        *,
        tool_call_id: str,
        path: str,
        new_content: str,
        rationale: str,
    ) -> PatchProposal:
        try:
            target = self._guard.resolve_existing(path)
        except FileNotFoundError as exc:
            raise _preparation_error(
                ToolErrorCode.PATCH_FILE_CREATION_NOT_SUPPORTED,
                "Patch proposals require an existing file.",
                ToolFailureCategory.PATCH,
            ) from exc
        except WorkspacePolicyError as exc:
            raise _preparation_error(
                exc.code,
                "The patch target does not satisfy the workspace policy.",
                ToolFailureCategory.POLICY_DENIED,
            ) from exc

        try:
            mode = target.stat().st_mode
        except OSError as exc:
            raise _preparation_error(
                ToolErrorCode.PATCH_TARGET_NOT_SUPPORTED,
                "The patch target could not be inspected.",
            ) from exc
        if not stat.S_ISREG(mode):
            raise _preparation_error(
                ToolErrorCode.PATCH_TARGET_NOT_SUPPORTED,
                "Only existing ordinary files can be patched.",
            )

        original = _read_source(target)
        if len(new_content) > TOOL_LIMITS.max_patch_proposed_characters:
            raise _preparation_error(
                ToolErrorCode.PATCH_PROPOSED_CONTENT_TOO_LARGE,
                "The proposed content exceeds the fixed size limit.",
                ToolFailureCategory.RESOURCE_LIMIT,
            )
        if "\x00" in new_content:
            raise _preparation_error(
                ToolErrorCode.PATCH_TARGET_NOT_SUPPORTED,
                "Binary patch content is not supported.",
                ToolFailureCategory.UNSUPPORTED_CONTENT,
            )
        if original == new_content:
            raise _preparation_error(
                ToolErrorCode.PATCH_EMPTY,
                "The proposed content does not change the file.",
            )

        relative_path = self._guard.relative(target)
        unified_diff = _unified_diff(relative_path, original, new_content)
        if not unified_diff:
            raise _preparation_error(
                ToolErrorCode.PATCH_EMPTY,
                "The proposed content does not produce a reviewable diff.",
            )
        if len(unified_diff) > TOOL_LIMITS.max_patch_diff_characters:
            raise _preparation_error(
                ToolErrorCode.PATCH_DIFF_TOO_LARGE,
                "The complete diff exceeds the fixed review limit.",
                ToolFailureCategory.RESOURCE_LIMIT,
            )
        added, removed = _changed_line_counts(original, new_content)
        if added + removed > TOOL_LIMITS.max_patch_changed_lines:
            raise _preparation_error(
                ToolErrorCode.PATCH_CHANGED_LINES_EXCEEDED,
                "The patch changes too many lines for one approval.",
                ToolFailureCategory.RESOURCE_LIMIT,
            )

        return PatchProposal(
            proposal_id=uuid4(),
            tool_call_id=tool_call_id,
            tool_name="propose_patch",
            relative_path=relative_path,
            rationale=rationale,
            original_sha256=_sha256_text(original),
            proposed_sha256=_sha256_text(new_content),
            unified_diff=unified_diff,
            original_character_count=len(original),
            proposed_character_count=len(new_content),
            added_line_count=added,
            removed_line_count=removed,
            created_at=datetime.now(UTC).isoformat(),
            proposed_content=new_content,
        )


def _read_source(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_SOURCE_BYTES + 1)
    except PermissionError as exc:
        raise _preparation_error(
            ToolErrorCode.PERMISSION_DENIED,
            "The patch target cannot be read.",
            ToolFailureCategory.FILESYSTEM,
        ) from exc
    except OSError as exc:
        raise _preparation_error(
            ToolErrorCode.PATCH_TARGET_NOT_SUPPORTED,
            "The patch target could not be read.",
            ToolFailureCategory.FILESYSTEM,
        ) from exc
    if len(raw) > _MAX_SOURCE_BYTES:
        raise _preparation_error(
            ToolErrorCode.PATCH_SOURCE_TOO_LARGE,
            "The source file exceeds the fixed size limit.",
            ToolFailureCategory.RESOURCE_LIMIT,
        )
    if b"\x00" in raw:
        raise _preparation_error(
            ToolErrorCode.BINARY_FILE,
            "Binary files cannot be patched.",
            ToolFailureCategory.UNSUPPORTED_CONTENT,
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _preparation_error(
            ToolErrorCode.INVALID_ENCODING,
            "Only UTF-8 text files can be patched.",
            ToolFailureCategory.UNSUPPORTED_CONTENT,
        ) from exc
    if len(text) > TOOL_LIMITS.max_patch_source_characters:
        raise _preparation_error(
            ToolErrorCode.PATCH_SOURCE_TOO_LARGE,
            "The source file exceeds the fixed size limit.",
            ToolFailureCategory.RESOURCE_LIMIT,
        )
    return text


def _diff_lines(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] = f"{lines[-1]}{_NO_NEWLINE_SENTINEL}\n"
    return lines


def _unified_diff(relative_path: str, original: str, proposed: str) -> str:
    raw = "".join(
        difflib.unified_diff(
            _diff_lines(original),
            _diff_lines(proposed),
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
        )
    )
    return raw.replace(
        f"{_NO_NEWLINE_SENTINEL}\n",
        "\n\\ No newline at end of file\n",
    )


def _changed_line_counts(original: str, proposed: str) -> tuple[int, int]:
    old_lines = original.splitlines()
    new_lines = proposed.splitlines()
    added = 0
    removed = 0
    for tag, old_start, old_end, new_start, new_end in difflib.SequenceMatcher(
        None,
        old_lines,
        new_lines,
        autojunk=False,
    ).get_opcodes():
        if tag in {"replace", "delete"}:
            removed += old_end - old_start
        if tag in {"replace", "insert"}:
            added += new_end - new_start
    if not added and not removed and original != proposed:
        return 1, 1
    return added, removed


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _preparation_error(
    code: ToolErrorCode,
    message: str,
    category: ToolFailureCategory = ToolFailureCategory.PATCH,
) -> PatchPreparationError:
    return PatchPreparationError(
        ToolFailure(
            phase=ToolExecutionPhase.PREPARATION,
            category=category,
            code=code,
            message=message,
        )
    )


def proposal_safe_metadata(proposal: PatchProposal) -> dict[str, object]:
    """Return the bounded metadata permitted in patch audit records."""

    return {
        "relative_path": proposal.relative_path,
        "original_sha256": proposal.original_sha256,
        "proposed_sha256": proposal.proposed_sha256,
        "original_character_count": proposal.original_character_count,
        "proposed_character_count": proposal.proposed_character_count,
        "added_line_count": proposal.added_line_count,
        "removed_line_count": proposal.removed_line_count,
    }


def proposal_review_matches_content(proposal: PatchProposal, original: str) -> bool:
    """Bind the reviewed diff and counts to the exact content later written."""

    added, removed = _changed_line_counts(original, proposal.proposed_content)
    return (
        proposal.unified_diff
        == _unified_diff(proposal.relative_path, original, proposal.proposed_content)
        and proposal.original_character_count == len(original)
        and proposal.proposed_character_count == len(proposal.proposed_content)
        and proposal.added_line_count == added
        and proposal.removed_line_count == removed
    )
