"""P3 contract, schema, and stable serialization tests."""

import json

import pytest
from pydantic import ValidationError

from repopilot.tools.contracts import (
    ListFilesArgs,
    ReadFileArgs,
    SearchCodeArgs,
    ToolErrorCode,
    ToolExecutionPhase,
    ToolFailureCategory,
    ToolResultEnvelope,
    failed_result,
    successful_result,
)


def test_success_and_failure_envelopes_have_stable_field_order() -> None:
    success = successful_result({"value": 1}).stable_json()
    failure = failed_result(
        phase=ToolExecutionPhase.POLICY,
        category=ToolFailureCategory.POLICY_DENIED,
        code=ToolErrorCode.SENSITIVE_PATH_DENIED,
        message="Access to this path is not allowed.",
    ).stable_json()

    assert list(json.loads(success)) == ["success", "data", "error"]
    assert list(json.loads(failure)) == ["success", "data", "error"]
    assert list(json.loads(failure)["error"]) == ["phase", "category", "code", "message"]
    assert ToolResultEnvelope.model_validate_json(success).success is True
    assert ToolResultEnvelope.model_validate_json(failure).success is False


@pytest.mark.parametrize(
    "payload",
    [
        {"success": True, "data": None, "error": None},
        {"success": False, "data": {}, "error": None},
        {"success": True, "data": {}, "error": {"phase": "policy"}},
    ],
)
def test_envelope_rejects_inconsistent_or_partial_shapes(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ToolResultEnvelope.model_validate(payload)


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (ReadFileArgs, {}),
        (ReadFileArgs, {"path": "x", "extra": "secret"}),
        (ReadFileArgs, {"path": 42}),
        (ReadFileArgs, {"path": "bad\x00path"}),
        (ListFilesArgs, {"recursive": "true"}),
        (ListFilesArgs, {"max_depth": True}),
        (SearchCodeArgs, {"query": ""}),
        (SearchCodeArgs, {"query": "   "}),
        (SearchCodeArgs, {"query": "x", "max_results": 0}),
        (SearchCodeArgs, {"query": "x", "max_results": 101}),
        (SearchCodeArgs, {"query": "x", "max_results": "10"}),
        (SearchCodeArgs, {"query": "x" * 201}),
        (SearchCodeArgs, {"query": "x", "file_suffix": "../py"}),
    ],
)
def test_explicit_tool_schemas_reject_unsafe_or_coerced_inputs(
    schema: type[ReadFileArgs | ListFilesArgs | SearchCodeArgs],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_schema_strips_strings_and_normalizes_suffix() -> None:
    read = ReadFileArgs.model_validate({"path": "  README.md  "})
    search = SearchCodeArgs.model_validate({"query": "  needle  ", "file_suffix": " PY "})

    assert read.path == "README.md"
    assert search.query == "needle"
    assert search.file_suffix == ".py"
