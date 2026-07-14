"""PatchProposalBuilder integrity, boundary, and resource-limit tests."""

import hashlib
from pathlib import Path

import pytest

from repopilot.patching.proposal import PatchPreparationError, PatchProposalBuilder
from repopilot.tools.contracts import TOOL_LIMITS
from repopilot.tools.policy import WorkspaceGuard


def _error_code(builder: PatchProposalBuilder, **kwargs: str) -> str:
    with pytest.raises(PatchPreparationError) as captured:
        builder.build(tool_call_id="call", **kwargs)
    return captured.value.failure.code.value


def test_builder_generates_complete_unique_hashed_diff_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir()
    target.write_bytes(b"a = 1\nb = 2\n")
    builder = PatchProposalBuilder(WorkspaceGuard(tmp_path))
    proposed = "a = 10\nc = 3\n"

    first = builder.build(
        tool_call_id="original-call",
        path="src/./a.py",
        new_content=proposed,
        rationale="Update values",
    )
    second = builder.build(
        tool_call_id="second-call",
        path="src/a.py",
        new_content=proposed,
        rationale="Update values",
    )

    assert first.proposal_id != second.proposal_id
    assert first.tool_call_id == "original-call"
    assert first.relative_path == "src/a.py"
    assert first.original_sha256 == hashlib.sha256(b"a = 1\nb = 2\n").hexdigest()
    assert first.proposed_sha256 == hashlib.sha256(proposed.encode()).hexdigest()
    assert first.added_line_count == first.removed_line_count == 2
    assert first.unified_diff.startswith("--- a/src/a.py\n+++ b/src/a.py\n")
    assert "-a = 1" in first.unified_diff and "+a = 10" in first.unified_diff
    assert target.read_bytes() == b"a = 1\nb = 2\n"
    assert str(tmp_path) not in first.model_dump_json()


def test_builder_rejects_empty_and_unsupported_targets(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"same\n")
    (tmp_path / "folder").mkdir()
    (tmp_path / "binary.bin").write_bytes(b"a\x00b")
    (tmp_path / "bad.txt").write_bytes(b"\xff")
    builder = PatchProposalBuilder(WorkspaceGuard(tmp_path))

    assert (
        _error_code(builder, path="a.txt", new_content="same\n", rationale="none") == "patch_empty"
    )
    assert (
        _error_code(builder, path="missing.txt", new_content="x", rationale="create")
        == "patch_file_creation_not_supported"
    )
    assert (
        _error_code(builder, path="folder", new_content="x", rationale="directory")
        == "patch_target_not_supported"
    )
    assert (
        _error_code(builder, path="binary.bin", new_content="x", rationale="binary")
        == "binary_file"
    )
    assert (
        _error_code(builder, path="bad.txt", new_content="x", rationale="encoding")
        == "invalid_encoding"
    )


@pytest.mark.parametrize(
    "path",
    [".env", ".git/config", "../outside.py", "C:\\outside.py", ".env::$DATA"],
)
def test_builder_reuses_workspace_policy_for_sensitive_and_escape_paths(
    tmp_path: Path,
    path: str,
) -> None:
    builder = PatchProposalBuilder(WorkspaceGuard(tmp_path))

    code = _error_code(builder, path=path, new_content="x", rationale="unsafe")

    assert code in {
        "sensitive_path_denied",
        "path_traversal_denied",
        "absolute_path_denied",
    }


def test_builder_rejects_source_and_proposed_content_limits(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_bytes(b"x" * (TOOL_LIMITS.max_patch_source_characters + 1))
    (tmp_path / "small.txt").write_bytes(b"old")
    builder = PatchProposalBuilder(WorkspaceGuard(tmp_path))

    assert (
        _error_code(builder, path="large.txt", new_content="new", rationale="large")
        == "patch_source_too_large"
    )
    assert (
        _error_code(
            builder,
            path="small.txt",
            new_content="x" * (TOOL_LIMITS.max_patch_proposed_characters + 1),
            rationale="large",
        )
        == "patch_proposed_content_too_large"
    )


def test_builder_rejects_changed_line_and_complete_diff_limits(tmp_path: Path) -> None:
    (tmp_path / "lines.txt").write_bytes(("a\n" * 1_500).encode())
    builder = PatchProposalBuilder(WorkspaceGuard(tmp_path))
    assert (
        _error_code(
            builder,
            path="lines.txt",
            new_content="b\n" * 1_500,
            rationale="too many lines",
        )
        == "patch_changed_lines_exceeded"
    )

    (tmp_path / "diff.txt").write_bytes(("a\n" * 40_000).encode())
    assert (
        _error_code(
            builder,
            path="diff.txt",
            new_content="b\n" * 40_000,
            rationale="too large diff",
        )
        == "patch_diff_too_large"
    )


def test_builder_rejects_link_when_platform_supports_it(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"old")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    code = _error_code(
        PatchProposalBuilder(WorkspaceGuard(tmp_path)),
        path="link.txt",
        new_content="new",
        rationale="link",
    )

    assert code == "link_path_denied"


def test_builder_rejects_simulated_junction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "junction" / "a.py"
    target.parent.mkdir()
    target.write_bytes(b"old")
    guard = WorkspaceGuard(tmp_path)
    monkeypatch.setattr(guard, "_is_link", lambda path: path == target.parent)

    code = _error_code(
        PatchProposalBuilder(guard),
        path="junction/a.py",
        new_content="new",
        rationale="junction",
    )

    assert code == "link_path_denied"
