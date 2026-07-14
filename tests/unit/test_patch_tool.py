"""Schema and no-direct-write tests for the model-visible patch proposal tool."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from repopilot.patching.proposal import PatchProposalBuilder
from repopilot.tools.contracts import TOOL_LIMITS, ToolErrorCode
from repopilot.tools.executor import SafeToolExecutor
from repopilot.tools.patch import build_patch_tool
from repopilot.tools.policy import ToolSafetyPolicy, WorkspaceGuard


def test_patch_tool_schema_accepts_exact_fields_without_stripping_content() -> None:
    schema = build_patch_tool().get_input_schema()
    content = "def f():\n    return 1\n"

    parsed = schema.model_validate(
        {"path": "src/a.py", "new_content": content, "rationale": " keep spacing "}
    )

    assert parsed.new_content == content
    assert parsed.rationale == " keep spacing "


@pytest.mark.parametrize("missing", ["path", "new_content", "rationale"])
def test_patch_tool_schema_requires_every_field(missing: str) -> None:
    payload = {"path": "a.py", "new_content": "x\n", "rationale": "change"}
    payload.pop(missing)

    with pytest.raises(ValidationError):
        build_patch_tool().get_input_schema().model_validate(payload)


@pytest.mark.parametrize("extra", ["approved", "effect", "proposal_id", "run_id", "original_hash"])
def test_patch_tool_schema_rejects_control_plane_fields(extra: str) -> None:
    payload = {
        "path": "a.py",
        "new_content": "x\n",
        "rationale": "change",
        extra: "forged",
    }

    with pytest.raises(ValidationError):
        build_patch_tool().get_input_schema().model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"path": "a\x00.py", "new_content": "x", "rationale": "change"},
        {"path": "a.py", "new_content": "x\x00", "rationale": "change"},
        {"path": "a.py", "new_content": "x", "rationale": "\x00"},
        {"path": "x" * (TOOL_LIMITS.max_path_length + 1), "new_content": "x", "rationale": "r"},
        {
            "path": "a.py",
            "new_content": "x",
            "rationale": "r" * (TOOL_LIMITS.max_patch_rationale_characters + 1),
        },
        {
            "path": "a.py",
            "new_content": "x" * (TOOL_LIMITS.max_patch_proposed_characters + 1),
            "rationale": "r",
        },
    ],
)
def test_patch_tool_schema_enforces_fixed_limits(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        build_patch_tool().get_input_schema().model_validate(payload)


def test_executor_validation_error_never_echoes_proposed_content(tmp_path: Path) -> None:
    guard = WorkspaceGuard(tmp_path)
    tool = build_patch_tool()
    executor = SafeToolExecutor(
        [tool],
        ToolSafetyPolicy(guard),
        PatchProposalBuilder(guard),
    )
    marker = "PROPOSED_SECRET_MARKER"

    result = executor.execute(
        model_call=1,
        tool_name="propose_patch",
        tool_call_id="patch-call",
        tool_input={"path": "a.py", "new_content": marker, "approved": True},
    )

    assert result.tool_message is not None
    assert marker not in str(result.tool_message.content)
    assert json.loads(str(result.tool_message.content))["error"]["code"] == "invalid_arguments"


def test_direct_patch_tool_invoke_never_writes(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"old\n")

    payload = json.loads(
        build_patch_tool().invoke({"path": "a.py", "new_content": "new\n", "rationale": "change"})
    )

    assert payload["error"]["code"] == ToolErrorCode.APPROVAL_REQUIRED
    assert target.read_bytes() == b"old\n"
