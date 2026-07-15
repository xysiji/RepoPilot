"""Pure policy and Windows-aware workspace boundary tests."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from repopilot.tools.contracts import ProposePatchInput, ReadFileArgs, ToolEffect
from repopilot.tools.policy import (
    PRODUCTION_TOOL_EFFECTS,
    ToolSafetyPolicy,
    WorkspaceGuard,
)


class EmptyArgs(BaseModel):
    pass


def test_all_production_tools_have_explicit_three_state_actions(tmp_path: Path) -> None:
    policy = ToolSafetyPolicy(WorkspaceGuard(tmp_path))

    decisions = {
        name: policy.evaluate(tool_name=name, validated_args=EmptyArgs())
        for name in PRODUCTION_TOOL_EFFECTS
    }

    assert set(decisions) == {"list_files", "read_file", "search_code", "propose_patch"}
    assert all(
        decisions[name].action == "allow"
        for name in PRODUCTION_TOOL_EFFECTS
        if name != "propose_patch"
    )
    assert decisions["propose_patch"].action == "require_approval"
    assert decisions["propose_patch"].effect is ToolEffect.WRITE


@pytest.mark.parametrize(
    ("effect", "expected_code"),
    [
        (ToolEffect.WRITE, "side_effect_not_supported"),
        (ToolEffect.COMMAND, "side_effect_not_supported"),
        (ToolEffect.UNKNOWN, "unclassified_tool_effect"),
    ],
)
def test_side_effect_and_unclassified_tools_fail_closed(
    tmp_path: Path,
    effect: ToolEffect,
    expected_code: str,
) -> None:
    effects = {} if effect is ToolEffect.UNKNOWN else {"synthetic": effect}
    policy = ToolSafetyPolicy(WorkspaceGuard(tmp_path), effects)

    decision = policy.evaluate(tool_name="synthetic", validated_args=EmptyArgs())

    assert decision.allowed is False
    assert decision.failure and decision.failure.code == expected_code
    assert decision.requires_approval is False
    assert decision.action == "deny"


@pytest.mark.parametrize(
    ("path", "expected_code"),
    [
        ("../secret", "path_traversal_denied"),
        ("nested\\..\\secret", "path_traversal_denied"),
        ("/etc/passwd", "absolute_path_denied"),
        ("C:\\secret.txt", "absolute_path_denied"),
        ("\\\\server\\share\\secret", "absolute_path_denied"),
        ("\\\\?\\C:\\secret", "windows_device_path_denied"),
        (".env::$DATA", "sensitive_path_denied"),
        (".env", "sensitive_path_denied"),
        ("nested/.ENV.LOCAL", "sensitive_path_denied"),
        (".git/config", "sensitive_path_denied"),
        (".repopilot/runtime.sqlite3", "sensitive_path_denied"),
        (".venv/token", "sensitive_path_denied"),
        ("__PYCACHE__/x.pyc", "sensitive_path_denied"),
        ("id_rsa_backup", "sensitive_path_denied"),
        ("id_ed25519.pub", "sensitive_path_denied"),
        ("client.PEM", "sensitive_path_denied"),
        ("client.key", "sensitive_path_denied"),
        ("CON", "windows_device_path_denied"),
        ("nul.txt", "windows_device_path_denied"),
        ("AUX.py", "windows_device_path_denied"),
        ("folder./file", "invalid_path"),
        ("folder /file", "invalid_path"),
    ],
)
def test_workspace_policy_rejects_aliases_sensitive_paths_and_devices(
    tmp_path: Path,
    path: str,
    expected_code: str,
) -> None:
    policy = ToolSafetyPolicy(WorkspaceGuard(tmp_path))

    decision = policy.evaluate(tool_name="read_file", validated_args=ReadFileArgs(path=path))

    assert decision.allowed is False
    assert decision.failure and decision.failure.phase == "policy"
    assert decision.failure.code == expected_code
    assert path not in decision.failure.message


def test_policy_rejects_resolved_alias_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path.parent / "outside.txt"
    candidate = tmp_path / "alias.txt"
    original_resolve = Path.resolve

    def resolve_alias(path: Path, strict: bool = False) -> Path:
        if path == candidate:
            return original_resolve(outside, strict=False)
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_alias)
    policy = ToolSafetyPolicy(WorkspaceGuard(tmp_path))

    decision = policy.evaluate(
        tool_name="read_file",
        validated_args=ReadFileArgs(path="alias.txt"),
    )

    assert decision.failure and decision.failure.code == "outside_workspace_denied"
    assert str(outside) not in decision.failure.message


def test_policy_rejects_symlink_when_platform_allows_creation(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("safe", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    decision = ToolSafetyPolicy(WorkspaceGuard(tmp_path)).evaluate(
        tool_name="read_file",
        validated_args=ReadFileArgs(path="link.txt"),
    )

    assert decision.failure and decision.failure.code == "link_path_denied"


def test_policy_rejects_simulated_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    link = tmp_path / "link.txt"
    guard = WorkspaceGuard(tmp_path)
    monkeypatch.setattr(guard, "_is_link", lambda path: path == link)

    decision = ToolSafetyPolicy(guard).evaluate(
        tool_name="read_file",
        validated_args=ReadFileArgs(path="link.txt"),
    )

    assert decision.failure and decision.failure.code == "link_path_denied"


def test_policy_rejects_simulated_junction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    junction = tmp_path / "junction"
    junction.mkdir()
    guard = WorkspaceGuard(tmp_path)
    monkeypatch.setattr(guard, "_is_link", lambda path: path == junction)
    decision = ToolSafetyPolicy(guard).evaluate(
        tool_name="read_file",
        validated_args=ReadFileArgs(path="junction/file.txt"),
    )

    assert decision.failure and decision.failure.code == "link_path_denied"


def test_policy_is_pure_and_does_not_mutate_validated_arguments(tmp_path: Path) -> None:
    arguments = ReadFileArgs(path="README.md")
    before = arguments.model_dump()

    decision = ToolSafetyPolicy(WorkspaceGuard(tmp_path)).evaluate(
        tool_name="read_file",
        validated_args=arguments,
    )

    assert decision.allowed is True
    assert arguments.model_dump() == before


def test_only_registered_patch_write_requires_approval(tmp_path: Path) -> None:
    guard = WorkspaceGuard(tmp_path)
    arguments = ProposePatchInput(path="a.py", new_content="new\n", rationale="change")

    decision = ToolSafetyPolicy(guard).evaluate(
        tool_name="propose_patch",
        validated_args=arguments,
    )
    unregistered = ToolSafetyPolicy(guard, {"other_write": ToolEffect.WRITE}).evaluate(
        tool_name="other_write",
        validated_args=arguments,
    )

    assert decision.action == "require_approval"
    assert decision.allowed is False and decision.requires_approval is True
    assert unregistered.action == "deny"
    assert unregistered.failure and unregistered.failure.code == "side_effect_not_supported"
    assert arguments.model_dump() == {
        "path": "a.py",
        "new_content": "new\n",
        "rationale": "change",
    }
