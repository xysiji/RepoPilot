"""Atomic apply, stale protection, and zero-write rejection tests."""

import os
from pathlib import Path
from uuid import uuid4

import pytest

from repopilot.approval.contracts import ApprovalDecision
from repopilot.patching.applicator import PatchApplicator
from repopilot.patching.proposal import PatchProposalBuilder
from repopilot.tools.policy import WorkspaceGuard


def _proposal(tmp_path: Path, new_content: str = "new\n"):
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    guard = WorkspaceGuard(tmp_path)
    proposal = PatchProposalBuilder(guard).build(
        tool_call_id="call-1",
        path="a.py",
        new_content=new_content,
        rationale="update",
    )
    return target, guard, proposal


def _decision(proposal, decision: str = "approve") -> ApprovalDecision:
    return ApprovalDecision(proposal_id=proposal.proposal_id, decision=decision)


def test_approved_patch_changes_file_and_preserves_original_mode(tmp_path: Path) -> None:
    target, guard, proposal = _proposal(tmp_path)
    os.chmod(target, 0o640)
    original_mode = target.stat().st_mode & 0o777

    result = PatchApplicator(guard).apply(proposal, _decision(proposal))

    assert result.success is True
    assert target.read_bytes() == b"new\n"
    assert target.stat().st_mode & 0o777 == original_mode


def test_unapproved_or_mismatched_decision_cannot_write(tmp_path: Path) -> None:
    target, guard, proposal = _proposal(tmp_path)
    before = target.read_bytes()

    rejected = PatchApplicator(guard).apply(proposal, _decision(proposal, "reject"))
    mismatch = PatchApplicator(guard).apply(
        proposal,
        ApprovalDecision(proposal_id=uuid4(), decision="approve"),
    )

    assert rejected.error and rejected.error.code == "invalid_approval_decision"
    assert mismatch.error and mismatch.error.code == "invalid_approval_decision"
    assert target.read_bytes() == before


def test_changed_preimage_returns_stale_without_writing(tmp_path: Path) -> None:
    target, guard, proposal = _proposal(tmp_path)
    target.write_bytes(b"changed while waiting\n")

    result = PatchApplicator(guard).apply(proposal, _decision(proposal))

    assert result.error and result.error.code == "stale_patch"
    assert target.read_bytes() == b"changed while waiting\n"


def test_proposed_hash_mismatch_is_rejected_before_temp_file(tmp_path: Path) -> None:
    target, guard, proposal = _proposal(tmp_path)
    tampered = proposal.model_copy(update={"proposed_sha256": "0" * 64})

    result = PatchApplicator(guard).apply(tampered, _decision(tampered))

    assert result.error and result.error.code == "patch_verification_failed"
    assert target.read_bytes() == b"old\n"
    assert list(tmp_path.glob(".repopilot-*.tmp")) == []


def test_reviewed_diff_must_match_exact_content_written(tmp_path: Path) -> None:
    target, guard, proposal = _proposal(tmp_path)
    tampered = proposal.model_copy(update={"unified_diff": "--- fake\n+++ fake\n"})

    result = PatchApplicator(guard).apply(tampered, _decision(tampered))

    assert result.error and result.error.code == "patch_verification_failed"
    assert target.read_bytes() == b"old\n"


def test_workspace_policy_is_rechecked_at_apply_time(tmp_path: Path) -> None:
    target, guard, proposal = _proposal(tmp_path)
    (tmp_path / ".env").write_bytes(b"old\n")
    tampered = proposal.model_copy(update={"relative_path": ".env"})

    result = PatchApplicator(guard).apply(tampered, _decision(tampered))

    assert result.error and result.error.code == "patch_target_not_supported"
    assert target.read_bytes() == b"old\n"
    assert (tmp_path / ".env").read_bytes() == b"old\n"


def test_link_or_junction_detected_during_second_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, guard, proposal = _proposal(tmp_path)
    monkeypatch.setattr(guard, "_is_link", lambda path: path == target)

    result = PatchApplicator(guard).apply(proposal, _decision(proposal))

    assert result.error and result.error.code == "patch_target_not_supported"
    assert target.read_bytes() == b"old\n"


def test_temp_file_is_same_directory_and_closed_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, guard, proposal = _proposal(tmp_path)
    real_replace = os.replace
    observed: list[tuple[Path, Path]] = []

    def checked_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.parent == destination_path.parent == tmp_path
        with source_path.open("ab"):
            pass
        observed.append((source_path, destination_path))
        real_replace(source_path, destination_path)

    monkeypatch.setattr(os, "replace", checked_replace)

    result = PatchApplicator(guard).apply(proposal, _decision(proposal))

    assert result.success is True
    assert len(observed) == 1


def test_replace_failure_cleans_temp_and_keeps_original_without_leaking_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, guard, proposal = _proposal(tmp_path)
    secret = "D:/private/temp-secret"

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        raise OSError(secret)

    monkeypatch.setattr(os, "replace", fail_replace)

    result = PatchApplicator(guard).apply(proposal, _decision(proposal))

    assert result.error and result.error.code == "patch_apply_failed"
    assert secret not in result.stable_json()
    assert target.read_bytes() == b"old\n"
    assert list(tmp_path.glob(".repopilot-*.tmp")) == []


def test_second_application_does_not_replace_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _target, guard, proposal = _proposal(tmp_path)
    real_replace = os.replace
    calls = 0

    def counted_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal calls
        calls += 1
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", counted_replace)
    applicator = PatchApplicator(guard)

    first = applicator.apply(proposal, _decision(proposal))
    second = applicator.apply(proposal, _decision(proposal))

    assert first.success is True
    assert second.error and second.error.code == "stale_patch"
    assert calls == 1


def test_post_replace_hash_is_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _target, guard, proposal = _proposal(tmp_path)
    real_replace = os.replace

    def corrupting_replace(source: str | Path, destination: str | Path) -> None:
        real_replace(source, destination)
        Path(destination).write_bytes(b"corrupted")

    monkeypatch.setattr(os, "replace", corrupting_replace)

    result = PatchApplicator(guard).apply(proposal, _decision(proposal))

    assert result.error and result.error.code == "patch_verification_failed"
