"""Protocol and termination tests for the hand-written P1 Tool Calling loop."""

import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from repopilot.agent.loop import ToolCallingLoop
from repopilot.tools.readonly import build_readonly_tools
from tests.scripted_model import ScriptedToolCallingModel


def _call(name: str, args: dict[str, object], call_id: str) -> dict[str, object]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_model_can_answer_without_tools_and_goal_is_preserved(tmp_path: Path) -> None:
    model = ScriptedToolCallingModel(responses=[AIMessage(content="direct answer")])

    result = ToolCallingLoop().run(
        "original goal",
        model=model,
        tools=build_readonly_tools(tmp_path),
        max_steps=3,
    )

    assert result.status == "success"
    assert result.final_answer == "direct answer"
    assert result.steps == 1
    assert model.bound_tool_names == ["list_files", "read_file", "search_code"]
    assert isinstance(model.received_messages[0][0], HumanMessage)
    assert model.received_messages[0][0].content == "original goal"


def test_one_tool_call_is_returned_as_matching_tool_message(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("P1 content", encoding="utf-8")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_call("read_file", {"path": "README.md"}, "c1")]),
            AIMessage(content="summary"),
        ]
    )

    result = ToolCallingLoop().run(
        "summarize README",
        model=model,
        tools=build_readonly_tools(tmp_path),
        max_steps=3,
    )

    assert result.status == "success"
    assert [type(message) for message in model.received_messages[1]] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
    ]
    tool_message = model.received_messages[1][-1]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.tool_call_id == "c1"
    assert json.loads(str(tool_message.content))["content"] == "P1 content"
    assert result.tool_executions[0].output_summary == (
        "read README.md (10 characters, truncated=False)"
    )


def test_all_tool_calls_in_one_ai_message_execute_in_order(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _call("read_file", {"path": "a.txt"}, "first"),
                    _call("read_file", {"path": "b.txt"}, "second"),
                ],
            ),
            AIMessage(content="both read"),
        ]
    )

    result = ToolCallingLoop().run(
        "read both",
        model=model,
        tools=build_readonly_tools(tmp_path),
        max_steps=3,
    )

    assert [record.tool_call_id for record in result.tool_executions] == ["first", "second"]
    returned = model.received_messages[1][-2:]
    assert [message.tool_call_id for message in returned if isinstance(message, ToolMessage)] == [
        "first",
        "second",
    ]


def test_unknown_tool_error_is_fed_back_and_loop_continues(tmp_path: Path) -> None:
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_call("missing_tool", {}, "unknown-1")]),
            AIMessage(content="handled"),
        ]
    )

    result = ToolCallingLoop().run(
        "try a tool",
        model=model,
        tools=build_readonly_tools(tmp_path),
        max_steps=3,
    )

    assert result.status == "success"
    assert result.tool_executions[0].success is False
    assert result.tool_executions[0].error_type == "unknown_tool"
    message = model.received_messages[1][-1]
    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert message.tool_call_id == "unknown-1"


def test_missing_tool_argument_is_fed_back_to_model(tmp_path: Path) -> None:
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_call("read_file", {}, "bad-args")]),
            AIMessage(content="reported argument error"),
        ]
    )

    result = ToolCallingLoop().run(
        "read",
        model=model,
        tools=build_readonly_tools(tmp_path),
        max_steps=3,
    )

    assert result.status == "success"
    assert result.tool_executions[0].error_type == "invalid_arguments"
    assert "Invalid tool arguments" in str(model.received_messages[1][-1].content)


def test_extra_tool_argument_is_fed_back_to_model(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_call("read_file", {"path": "a.txt", "extra": True}, "extra")],
            ),
            AIMessage(content="reported extra argument"),
        ]
    )

    result = ToolCallingLoop().run(
        "read",
        model=model,
        tools=build_readonly_tools(tmp_path),
        max_steps=3,
    )

    assert result.tool_executions[0].error_type == "invalid_arguments"


def test_structured_tool_failure_is_fed_back_to_model(tmp_path: Path) -> None:
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_call("read_file", {"path": "missing.txt"}, "missing")],
            ),
            AIMessage(content="file missing"),
        ]
    )

    result = ToolCallingLoop().run(
        "read missing",
        model=model,
        tools=build_readonly_tools(tmp_path),
        max_steps=3,
    )

    assert result.status == "success"
    assert result.tool_executions[0].error_type == "not_found"
    assert isinstance(model.received_messages[1][-1], ToolMessage)


def test_tool_exception_is_converted_to_stable_error(tmp_path: Path) -> None:
    def explode(value: int) -> str:
        """Raise a deterministic test exception."""

        raise RuntimeError(f"boom {value}")

    broken_tool = StructuredTool.from_function(func=explode, name="explode")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_call("explode", {"value": 1}, "explode-1")]),
            AIMessage(content="recovered"),
        ]
    )

    result = ToolCallingLoop().run(
        "explode safely",
        model=model,
        tools=[*build_readonly_tools(tmp_path), broken_tool],
        max_steps=3,
    )

    assert result.status == "success"
    assert result.tool_executions[-1].error_type == "tool_exception"
    assert result.tool_executions[-1].error_message == "Tool raised RuntimeError"


def test_max_steps_stops_repeated_tool_calls(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    repeated = AIMessage(
        content="",
        tool_calls=[_call("read_file", {"path": "a.txt"}, "repeat")],
    )
    model = ScriptedToolCallingModel(responses=[repeated, repeated.model_copy(deep=True)])

    result = ToolCallingLoop().run(
        "repeat forever",
        model=model,
        tools=build_readonly_tools(tmp_path),
        max_steps=2,
    )

    assert result.status == "max_steps_exceeded"
    assert result.steps == 2
    assert len(result.tool_executions) == 2
    assert len(model.received_messages) == 2
    assert result.error is not None and result.error.code == "max_steps_exceeded"


def test_model_exception_returns_model_error(tmp_path: Path) -> None:
    model = ScriptedToolCallingModel(responses=[], raise_on_invoke=True)

    result = ToolCallingLoop().run(
        "fail model",
        model=model,
        tools=build_readonly_tools(tmp_path),
        max_steps=2,
    )

    assert result.status == "model_error"
    assert result.error is not None
    assert result.error.message == "Model invocation failed: RuntimeError"


def test_empty_final_content_is_invalid_model_response(tmp_path: Path) -> None:
    model = ScriptedToolCallingModel(responses=[AIMessage(content="")])

    result = ToolCallingLoop().run(
        "empty",
        model=model,
        tools=build_readonly_tools(tmp_path),
        max_steps=2,
    )

    assert result.status == "invalid_model_response"
    assert result.error is not None and "empty" in result.error.message.lower()


def test_non_ai_message_is_invalid_model_response(tmp_path: Path) -> None:
    model = ScriptedToolCallingModel(responses=[HumanMessage(content="wrong role")])

    result = ToolCallingLoop().run(
        "invalid response",
        model=model,
        tools=build_readonly_tools(tmp_path),
        max_steps=2,
    )

    assert result.status == "invalid_model_response"
