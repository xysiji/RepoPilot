"""Ordering, fail-closed behavior, classification, and protocol tests for P3."""

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from repopilot.patching.proposal import PatchProposalBuilder
from repopilot.tools.contracts import (
    ProposePatchInput,
    ReadFileArgs,
    ResourceLimitExceededError,
    ToolEffect,
    ToolPolicyDecision,
    successful_result,
)
from repopilot.tools.executor import SafeToolExecutor
from repopilot.tools.policy import ToolSafetyPolicy, WorkspaceGuard


class CountingPolicy:
    def __init__(self, decision: ToolPolicyDecision) -> None:
        self.calls = 0
        self.decision = decision

    def evaluate(self, **kwargs: object) -> ToolPolicyDecision:
        self.calls += 1
        return self.decision


def _allow_read_only() -> ToolPolicyDecision:
    return ToolPolicyDecision(
        allowed=True,
        effect=ToolEffect.READ_ONLY,
        requires_approval=False,
    )


def _tool(function: Any, name: str = "probe") -> StructuredTool:
    return StructuredTool.from_function(
        func=function,
        name=name,
        description="Synthetic P3 executor probe.",
        args_schema=ReadFileArgs,
    )


def _execute(executor: SafeToolExecutor, name: str, args: dict[str, object], call_id: str = "c1"):
    return executor.execute(
        model_call=1,
        tool_name=name,
        tool_call_id=call_id,
        tool_input=args,
    )


def test_unknown_tool_stops_at_dispatch_and_returns_matching_tool_message() -> None:
    policy = CountingPolicy(_allow_read_only())
    result = _execute(SafeToolExecutor([], policy), "missing", {}, "original-id")

    assert policy.calls == 0
    assert result.record.phase == "dispatch"
    assert result.record.error_code == "unknown_tool"
    assert result.tool_message.tool_call_id == "original-id"
    assert result.tool_message.status == "error"


def test_validation_failure_calls_neither_policy_nor_tool_and_hides_input() -> None:
    calls = 0

    def probe(path: str) -> str:
        nonlocal calls
        calls += 1
        return successful_result({"path": path}).stable_json()

    policy = CountingPolicy(_allow_read_only())
    executor = SafeToolExecutor([_tool(probe)], policy)
    secret = "RAW_SECRET_VALUE"
    result = _execute(executor, "probe", {"path": 42, secret: secret})

    assert policy.calls == 0
    assert calls == 0
    assert result.record.phase == "validation"
    assert result.record.error_code == "invalid_arguments"
    assert secret not in str(result.tool_message.content)
    assert secret not in result.record.model_dump_json()
    assert "input_value" not in str(result.tool_message.content)
    assert result.record.input == {"fields": ["path"], "unknown_field_count": 1}


@pytest.mark.parametrize("effect", [ToolEffect.WRITE, ToolEffect.COMMAND, ToolEffect.UNKNOWN])
def test_policy_denial_never_executes_side_effect_or_unclassified_tool(
    tmp_path: Path,
    effect: ToolEffect,
) -> None:
    calls = 0

    def probe(path: str) -> str:
        nonlocal calls
        calls += 1
        return successful_result({"path": path}).stable_json()

    effects = {} if effect is ToolEffect.UNKNOWN else {"probe": effect}
    policy = ToolSafetyPolicy(WorkspaceGuard(tmp_path), effects)
    result = _execute(SafeToolExecutor([_tool(probe)], policy), "probe", {"path": "safe.txt"})

    assert calls == 0
    assert result.record.phase == "policy"
    assert result.record.policy_allowed is False
    expected = (
        "unclassified_tool_effect" if effect is ToolEffect.UNKNOWN else "side_effect_not_supported"
    )
    assert result.record.error_code == expected


def test_model_cannot_forge_effect_in_arguments(tmp_path: Path) -> None:
    calls = 0

    def probe(path: str) -> str:
        nonlocal calls
        calls += 1
        return successful_result({"path": path}).stable_json()

    policy = CountingPolicy(_allow_read_only())
    result = _execute(
        SafeToolExecutor([_tool(probe)], policy),
        "probe",
        {"path": "README.md", "effect": "read_only"},
    )

    assert result.record.error_code == "invalid_arguments"
    assert policy.calls == calls == 0


def test_allowed_tool_executes_once_and_returns_stable_success_protocol() -> None:
    calls = 0

    def probe(path: str) -> str:
        nonlocal calls
        calls += 1
        return successful_result(
            {"path": path, "character_count": 2, "truncated": False}
        ).stable_json()

    result = _execute(
        SafeToolExecutor([_tool(probe)], CountingPolicy(_allow_read_only())),
        "probe",
        {"path": "ok.txt"},
        "same-id",
    )

    payload = json.loads(str(result.tool_message.content))
    assert calls == 1
    assert result.tool_message.status == "success"
    assert result.tool_message.tool_call_id == "same-id"
    assert payload == {
        "success": True,
        "data": {"path": "ok.txt", "character_count": 2, "truncated": False},
        "error": None,
    }
    assert result.record.failure_category is None
    assert result.record.error_code is None
    assert result.record.policy_allowed is True


@pytest.mark.parametrize(
    ("exception", "category", "code"),
    [
        (FileNotFoundError("D:/outside/secret"), "filesystem", "not_found"),
        (IsADirectoryError("D:/outside/secret"), "filesystem", "not_a_file"),
        (NotADirectoryError("D:/outside/secret"), "filesystem", "not_a_directory"),
        (PermissionError("API_KEY=secret"), "filesystem", "permission_denied"),
        (ResourceLimitExceededError("huge secret"), "resource_limit", "resource_limit_exceeded"),
        (RuntimeError("private traceback detail"), "execution_failure", "tool_execution_error"),
    ],
)
def test_execution_exceptions_are_safely_classified(
    exception: Exception,
    category: str,
    code: str,
) -> None:
    def probe(path: str) -> str:
        raise exception

    result = _execute(
        SafeToolExecutor([_tool(probe)], CountingPolicy(_allow_read_only())),
        "probe",
        {"path": "safe.txt"},
    )
    serialized = result.record.model_dump_json() + str(result.tool_message.content)

    assert result.record.phase == "execution"
    assert result.record.failure_category == category
    assert result.record.error_code == code
    assert str(exception) not in serialized
    assert "Traceback" not in serialized


@pytest.mark.parametrize("invalid_result", ["plain text", "{}", [], 42, {"success": True}])
def test_invalid_tool_result_stops_at_normalization_without_breaking_protocol(
    invalid_result: object,
) -> None:
    def probe(path: str) -> object:
        return invalid_result

    result = _execute(
        SafeToolExecutor([_tool(probe)], CountingPolicy(_allow_read_only())),
        "probe",
        {"path": "safe.txt"},
    )

    assert result.record.phase == "normalization"
    assert result.record.error_code == "invalid_tool_result"
    assert result.tool_message.status == "error"
    assert json.loads(str(result.tool_message.content))["error"]["code"] == "invalid_tool_result"


def test_approval_action_prepares_proposal_without_invoking_tool_function(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")
    calls = 0

    def forbidden_write(path: str, new_content: str, rationale: str) -> str:
        nonlocal calls
        calls += 1
        target.write_text(new_content, encoding="utf-8")
        return rationale + path

    tool = StructuredTool.from_function(
        func=forbidden_write,
        name="propose_patch",
        description="test proposal tool",
        args_schema=ProposePatchInput,
    )
    guard = WorkspaceGuard(tmp_path)
    executor = SafeToolExecutor(
        [tool],
        ToolSafetyPolicy(guard),
        PatchProposalBuilder(guard),
    )

    result = _execute(
        executor,
        "propose_patch",
        {"path": "a.py", "new_content": "new\n", "rationale": "change"},
    )

    assert calls == 0
    assert target.read_bytes() == b"old\n"
    assert result.proposal is not None
    assert result.tool_message is None and result.record is None
