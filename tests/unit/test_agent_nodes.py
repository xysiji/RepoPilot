"""Focused tests for the model node and P3 SafeToolExecutor integration."""

import asyncio
import json
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

from repopilot.agent.nodes import ModelNode, ToolNode
from repopilot.agent.state import create_initial_state
from repopilot.tools.contracts import ToolEffect
from repopilot.tools.executor import SafeToolExecutor
from repopilot.tools.policy import PRODUCTION_TOOL_EFFECTS, ToolSafetyPolicy, WorkspaceGuard
from repopilot.tools.readonly import build_readonly_tools
from tests.scripted_model import ScriptedToolCallingModel


def _call(name: str, args: dict[str, object], call_id: str) -> dict[str, object]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _tool_node(tmp_path: Path, *extra_tools: StructuredTool) -> ToolNode:
    guard = WorkspaceGuard(tmp_path)
    tools = [*build_readonly_tools(guard), *extra_tools]
    effects = dict(PRODUCTION_TOOL_EFFECTS)
    effects.update({tool.name: ToolEffect.READ_ONLY for tool in extra_tools})
    return ToolNode(SafeToolExecutor(tools, ToolSafetyPolicy(guard, effects)))


def test_model_node_emits_ai_message_and_final_fields() -> None:
    model = ScriptedToolCallingModel(responses=[AIMessage(content="final answer")])
    node = ModelNode(model.bind_tools([]))

    update = asyncio.run(node(create_initial_state("goal", 2)))

    assert update["model_calls"] == 1
    assert update["status"] == "success"
    assert update["final_answer"] == "final answer"
    assert isinstance(update["messages"][0], AIMessage)


def test_model_node_returns_single_and_multiple_tool_calls_without_executing_them() -> None:
    single = AIMessage(content="", tool_calls=[_call("missing", {}, "one")])
    multiple = AIMessage(
        content="",
        tool_calls=[_call("missing", {}, "two"), _call("missing", {}, "three")],
    )
    model = ScriptedToolCallingModel(responses=[single, multiple])
    node = ModelNode(model.bind_tools([]))

    first = asyncio.run(node(create_initial_state("first", 2)))
    second = asyncio.run(node(create_initial_state("second", 2)))

    assert first["status"] == second["status"] == "running"
    assert len(first["messages"][0].tool_calls) == 1
    assert len(second["messages"][0].tool_calls) == 2


def test_model_node_converts_model_exception_to_stable_error() -> None:
    model = ScriptedToolCallingModel(responses=[], raise_on_invoke=True)
    node = ModelNode(model.bind_tools([]))

    update = asyncio.run(node(create_initial_state("goal", 2)))

    assert update["status"] == "model_error"
    assert update["model_calls"] == 1
    assert update["error"].message == "Model invocation failed: RuntimeError"


def test_tool_node_executes_all_calls_in_order_and_preserves_ids(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")
    state = create_initial_state("read both", 3)
    state["model_calls"] = 1
    state["messages"].append(
        AIMessage(
            content="",
            tool_calls=[
                _call("read_file", {"path": "a.txt"}, "first"),
                _call("read_file", {"path": "b.txt"}, "second"),
            ],
        )
    )

    update = _tool_node(tmp_path)(state)

    assert [message.tool_call_id for message in update["messages"]] == ["first", "second"]
    assert [record.tool_call_id for record in update["tool_executions"]] == [
        "first",
        "second",
    ]
    assert all(isinstance(message, ToolMessage) for message in update["messages"])


def test_tool_node_returns_stable_unknown_validation_and_exception_errors(
    tmp_path: Path,
) -> None:
    def explode(value: int) -> str:
        """Raise a deterministic test exception."""

        raise RuntimeError(str(value))

    broken_tool = StructuredTool.from_function(func=explode, name="explode")
    state = create_initial_state("errors", 3)
    state["model_calls"] = 1
    state["messages"].append(
        AIMessage(
            content="",
            tool_calls=[
                _call("missing", {}, "unknown"),
                _call("read_file", {}, "invalid"),
                _call("explode", {"value": 1}, "exception"),
            ],
        )
    )

    update = _tool_node(tmp_path, broken_tool)(state)

    assert [record.error_type for record in update["tool_executions"]] == [
        "unknown_tool",
        "invalid_arguments",
        "tool_execution_error",
    ]
    payloads = [json.loads(str(message.content)) for message in update["messages"]]
    assert [payload["success"] for payload in payloads] == [False, False, False]
    assert "RuntimeError" not in str(payloads)


def test_failed_tool_does_not_prevent_later_structured_result(tmp_path: Path) -> None:
    (tmp_path / "ok.txt").write_text("ok", encoding="utf-8")
    state = create_initial_state("continue", 3)
    state["model_calls"] = 1
    state["messages"].append(
        AIMessage(
            content="",
            tool_calls=[
                _call("read_file", {"path": "missing.txt"}, "missing"),
                _call("read_file", {"path": "ok.txt"}, "ok"),
            ],
        )
    )

    update = _tool_node(tmp_path)(state)

    assert [record.success for record in update["tool_executions"]] == [False, True]
    assert [message.tool_call_id for message in update["messages"]] == ["missing", "ok"]


def test_tool_node_sets_max_steps_only_after_completing_current_batch(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    state = create_initial_state("read", 1)
    state["model_calls"] = 1
    state["messages"].append(
        AIMessage(
            content="",
            tool_calls=[
                _call("read_file", {"path": "a.txt"}, "first"),
                _call("read_file", {"path": "a.txt"}, "second"),
            ],
        )
    )

    update = _tool_node(tmp_path)(state)

    assert update["status"] == "max_steps_exceeded"
    assert len(update["messages"]) == 2
    assert len(update["tool_executions"]) == 2
