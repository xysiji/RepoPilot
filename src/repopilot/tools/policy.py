"""Pure Python tool-effect and workspace safety policy."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

from repopilot.tools.contracts import (
    ToolEffect,
    ToolErrorCode,
    ToolExecutionPhase,
    ToolFailure,
    ToolFailureCategory,
    ToolPolicyAction,
    ToolPolicyDecision,
)

PRODUCTION_TOOL_EFFECTS: Mapping[str, ToolEffect] = {
    "list_files": ToolEffect.READ_ONLY,
    "read_file": ToolEffect.READ_ONLY,
    "search_code": ToolEffect.READ_ONLY,
    "propose_patch": ToolEffect.WRITE,
}

_APPROVAL_TOOLS = frozenset({"propose_patch"})

_EXCLUDED_DIRECTORIES = frozenset({".git", ".repopilot", ".venv", "__pycache__"})
_PRIVATE_KEY_PREFIXES = ("id_rsa", "id_ed25519")
_PRIVATE_KEY_SUFFIXES = frozenset({".pem", ".key"})
_RESERVED_WINDOWS_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_WINDOWS_DRIVE = re.compile(r"^[a-zA-Z]:")


class WorkspacePolicyError(PermissionError):
    """A safe, classified workspace-policy rejection."""

    def __init__(self, code: ToolErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class WorkspaceGuard:
    """Apply one canonical boundary to policy checks and actual file access."""

    def __init__(self, workspace_path: str | Path) -> None:
        root = Path(workspace_path).resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError("workspace root must be a directory")
        self.root = root

    def check(self, relative_path: str) -> Path:
        """Validate a model path without requiring the target to exist."""

        parts = self._lexical_parts(relative_path)
        candidate = self.root.joinpath(*parts)
        self._reject_links(candidate, parts)
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise WorkspacePolicyError(ToolErrorCode.INVALID_PATH) from exc
        if not self._is_within_root(resolved):
            raise WorkspacePolicyError(ToolErrorCode.OUTSIDE_WORKSPACE_DENIED)
        return resolved

    def resolve_existing(self, relative_path: str) -> Path:
        """Reapply the same policy immediately before an existing path is accessed."""

        parts = self._lexical_parts(relative_path)
        candidate = self.root.joinpath(*parts)
        self._reject_links(candidate, parts)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise WorkspacePolicyError(ToolErrorCode.INVALID_PATH) from exc
        if not self._is_within_root(resolved):
            raise WorkspacePolicyError(ToolErrorCode.OUTSIDE_WORKSPACE_DENIED)
        return resolved

    def is_safe_discovered_path(self, path: Path) -> bool:
        """Check paths discovered during bounded traversal without exposing failures."""

        try:
            relative = path.relative_to(self.root).as_posix()
            self.resolve_existing(relative)
        except (OSError, ValueError, WorkspacePolicyError):
            return False
        return True

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _lexical_parts(self, raw_path: str) -> tuple[str, ...]:
        return workspace_relative_parts(raw_path)

    def _reject_links(self, candidate: Path, parts: tuple[str, ...]) -> None:
        current = self.root
        for part in parts:
            current /= part
            try:
                is_link = self._is_link(current)
            except OSError as exc:
                raise WorkspacePolicyError(ToolErrorCode.INVALID_PATH) from exc
            if is_link:
                raise WorkspacePolicyError(ToolErrorCode.LINK_PATH_DENIED)
        if not self._is_within_root(candidate):
            raise WorkspacePolicyError(ToolErrorCode.OUTSIDE_WORKSPACE_DENIED)

    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or path.is_junction()

    def _is_within_root(self, path: Path) -> bool:
        try:
            root = os.path.normcase(str(self.root))
            candidate = os.path.normcase(str(path))
            return os.path.commonpath((root, candidate)) == root
        except ValueError:
            return False


def _is_sensitive_name(name: str) -> bool:
    lowered = name.casefold()
    if lowered in _EXCLUDED_DIRECTORIES or lowered.startswith(".env"):
        return True
    if lowered.startswith(_PRIVATE_KEY_PREFIXES):
        return True
    return Path(lowered).suffix in _PRIVATE_KEY_SUFFIXES


def workspace_relative_parts(raw_path: str) -> tuple[str, ...]:
    """Validate one cross-platform workspace-relative path without touching disk."""

    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise WorkspacePolicyError(ToolErrorCode.INVALID_PATH)

    normalized = raw_path.replace("\\", "/")
    lowered = normalized.casefold()
    if lowered.startswith("//?/") or lowered.startswith("//./"):
        raise WorkspacePolicyError(ToolErrorCode.WINDOWS_DEVICE_PATH_DENIED)
    if normalized.startswith("//"):
        raise WorkspacePolicyError(ToolErrorCode.ABSOLUTE_PATH_DENIED)
    if normalized.startswith("/") or _WINDOWS_DRIVE.match(normalized):
        raise WorkspacePolicyError(ToolErrorCode.ABSOLUTE_PATH_DENIED)
    if ":" in normalized:
        raise WorkspacePolicyError(ToolErrorCode.SENSITIVE_PATH_DENIED)

    raw_parts = normalized.split("/")
    if any(part == ".." for part in raw_parts):
        raise WorkspacePolicyError(ToolErrorCode.PATH_TRAVERSAL_DENIED)
    parts = tuple(part for part in raw_parts if part not in {"", "."})
    for part in parts:
        if part.endswith((" ", ".")):
            raise WorkspacePolicyError(ToolErrorCode.INVALID_PATH)
        stem = part.split(".", maxsplit=1)[0].upper()
        if stem in _RESERVED_WINDOWS_NAMES:
            raise WorkspacePolicyError(ToolErrorCode.WINDOWS_DEVICE_PATH_DENIED)
        if _is_sensitive_name(part):
            raise WorkspacePolicyError(ToolErrorCode.SENSITIVE_PATH_DENIED)
    return parts


class ToolSafetyPolicy:
    """Fail-closed effect and workspace policy with no model or state dependencies."""

    def __init__(
        self,
        workspace_guard: WorkspaceGuard,
        effects: Mapping[str, ToolEffect] | None = None,
    ) -> None:
        self.workspace_guard = workspace_guard
        self._effects = dict(PRODUCTION_TOOL_EFFECTS if effects is None else effects)

    def evaluate(self, *, tool_name: str, validated_args: BaseModel) -> ToolPolicyDecision:
        effect = self._effects.get(tool_name, ToolEffect.UNKNOWN)
        if effect is ToolEffect.UNKNOWN:
            return _denied_decision(
                effect=effect,
                code=ToolErrorCode.UNCLASSIFIED_TOOL_EFFECT,
                message="The requested tool has no trusted effect classification.",
            )
        if effect is ToolEffect.COMMAND:
            return _denied_decision(
                effect=effect,
                code=ToolErrorCode.SIDE_EFFECT_NOT_SUPPORTED,
                message="Command tools are not supported.",
            )
        if effect is ToolEffect.WRITE and tool_name not in _APPROVAL_TOOLS:
            return _denied_decision(
                effect=effect,
                code=ToolErrorCode.SIDE_EFFECT_NOT_SUPPORTED,
                message="This write capability is not registered for human approval.",
            )

        arguments = validated_args.model_dump()
        requested_path = arguments.get("path", arguments.get("directory"))
        if isinstance(requested_path, str):
            try:
                self.workspace_guard.check(requested_path)
            except WorkspacePolicyError as exc:
                return _denied_decision(
                    effect=effect,
                    code=exc.code,
                    message=_safe_path_message(exc.code),
                )
        if effect is ToolEffect.WRITE:
            return ToolPolicyDecision(
                action=ToolPolicyAction.REQUIRE_APPROVAL,
                allowed=False,
                effect=effect,
                requires_approval=True,
                failure=None,
            )
        return ToolPolicyDecision(
            action=ToolPolicyAction.ALLOW,
            allowed=True,
            effect=effect,
            requires_approval=False,
            failure=None,
        )


def _denied_decision(
    *,
    effect: ToolEffect,
    code: ToolErrorCode,
    message: str,
) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        action=ToolPolicyAction.DENY,
        allowed=False,
        effect=effect,
        requires_approval=False,
        failure=ToolFailure(
            phase=ToolExecutionPhase.POLICY,
            category=ToolFailureCategory.POLICY_DENIED,
            code=code,
            message=message,
        ),
    )


def _safe_path_message(code: ToolErrorCode) -> str:
    if code is ToolErrorCode.SENSITIVE_PATH_DENIED:
        return "Access to this path is not allowed."
    if code is ToolErrorCode.LINK_PATH_DENIED:
        return "Linked paths are not available to tools."
    if code is ToolErrorCode.PATH_TRAVERSAL_DENIED:
        return "Parent path traversal is not allowed."
    if code is ToolErrorCode.ABSOLUTE_PATH_DENIED:
        return "Only workspace-relative paths are allowed."
    if code is ToolErrorCode.WINDOWS_DEVICE_PATH_DENIED:
        return "Windows device paths and reserved names are not allowed."
    if code is ToolErrorCode.OUTSIDE_WORKSPACE_DENIED:
        return "The requested path is outside the workspace."
    return "The requested path is invalid."
