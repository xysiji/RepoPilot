"""Behavior and topology tests for the sole P4 production execution engine."""

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

from repopilot.agent.graph import build_agent_graph
from repopilot.agent.state import create_initial_state
from repopilot.patching.applicator import PatchApplicator
from repopilot.services.agent_service import AgentService
from repopilot.tools.executor import SafeToolExecutor
from repopilot.tools.policy import ToolSafetyPolicy, WorkspaceGuard
from repopilot.tools.readonly import build_readonly_tools
from tests.scripted_model import ScriptedToolCallingModel


def _call(name: str, args: dict[str, object], call_id: str) -> dict[str, object]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _run(service: AgentService, goal: str, max_steps: int = 3):
    return asyncio.run(service.run(goal, max_steps=max_steps))


def _runtime(workspace: Path):
    guard = WorkspaceGuard(workspace)
    tools = build_readonly_tools(guard)
    return tools, SafeToolExecutor(tools, ToolSafetyPolicy(guard)), PatchApplicator(guard)


class BindingFailureModel(ScriptedToolCallingModel):
    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        raise RuntimeError("binding failed")


def test_graph_topology_is_explicit_and_compiled_with_checkpointer(tmp_path: Path) -> None:
    model = ScriptedToolCallingModel(responses=[AIMessage(content="done")])
    tools, executor, applicator = _runtime(tmp_path)
    graph = build_agent_graph(model, tools, executor, applicator)
    drawable = graph.get_graph()

    assert set(drawable.nodes) == {
        "__start__",
        "model",
        "tools",
        "approval",
        "apply_patch",
        "reject_patch",
        "tester",
        "reviewer",
        "final_report",
        "__end__",
    }
    assert isinstance(graph.checkpointer, InMemorySaver)
    assert "model" in drawable.draw_mermaid()
    assert "tools" in drawable.draw_mermaid()


def test_direct_answer_preserves_goal_and_counts_model_rounds(tmp_path: Path) -> None:
    model = ScriptedToolCallingModel(responses=[AIMessage(content="direct answer")])

    result = _run(AgentService(tmp_path, model), "original goal")

    assert result.status == "success"
    assert result.final_answer == "The run completed without applying a patch."
    assert result.final_report is not None
    assert result.final_report.model_final_text == "direct answer"
    assert result.steps == 1
    assert model.bound_tool_names == ["list_files", "read_file", "search_code", "propose_patch"]
    assert isinstance(model.received_messages[0][0], HumanMessage)
    assert model.received_messages[0][0].content == "original goal"


def test_tool_result_is_fed_to_next_model_round_with_matching_call_id(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("P2 content", encoding="utf-8")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_call("read_file", {"path": "README.md"}, "c1")]),
            AIMessage(content="summary"),
        ]
    )

    result = _run(AgentService(tmp_path, model), "summarize")

    assert result.status == "success"
    assert [type(message) for message in model.received_messages[1]] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
    ]
    tool_message = model.received_messages[1][-1]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.tool_call_id == "c1"
    assert json.loads(str(tool_message.content))["data"]["content"] == "P2 content"


def test_multiple_tool_calls_and_multiple_rounds_accumulate_in_order(tmp_path: Path) -> None:
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
            AIMessage(
                content="",
                tool_calls=[_call("search_code", {"query": "A"}, "third")],
            ),
            AIMessage(content="complete"),
        ]
    )

    result = _run(AgentService(tmp_path, model), "inspect", max_steps=4)

    assert result.status == "success"
    assert result.steps == 3
    assert [record.tool_call_id for record in result.tool_executions] == [
        "first",
        "second",
        "third",
    ]
    assert [message.tool_call_id for message in model.received_messages[1][-2:]] == [
        "first",
        "second",
    ]


def test_tool_failures_are_feedback_and_do_not_abort_graph(tmp_path: Path) -> None:
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _call("missing_tool", {}, "unknown"),
                    _call("read_file", {}, "invalid"),
                    _call("read_file", {"path": "missing.txt"}, "not-found"),
                ],
            ),
            AIMessage(content="handled"),
        ]
    )

    result = _run(AgentService(tmp_path, model), "handle errors")

    assert result.status == "success"
    assert [record.error_type for record in result.tool_executions] == [
        "unknown_tool",
        "invalid_arguments",
        "not_found",
    ]
    assert all(isinstance(message, ToolMessage) for message in model.received_messages[1][-3:])


def test_policy_denial_is_fed_back_and_model_can_choose_safe_path(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=never-return", encoding="utf-8")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_call("read_file", {"path": ".env"}, "denied")]),
            AIMessage(
                content="",
                tool_calls=[_call("read_file", {"path": "README.md"}, "allowed")],
            ),
            AIMessage(content="used a safe file"),
        ]
    )

    result = _run(AgentService(tmp_path, model), "inspect", max_steps=3)

    denied_message = model.received_messages[1][-1]
    assert isinstance(denied_message, ToolMessage)
    assert json.loads(str(denied_message.content))["error"]["code"] == "sensitive_path_denied"
    assert result.status == "success"
    assert [record.success for record in result.tool_executions] == [False, True]
    assert [record.error_code for record in result.tool_executions] == [
        "sensitive_path_denied",
        None,
    ]


def test_repeated_policy_denials_end_only_at_model_step_budget(tmp_path: Path) -> None:
    responses = [
        AIMessage(
            content="",
            tool_calls=[_call("read_file", {"path": ".env"}, f"denied-{index}")],
        )
        for index in range(2)
    ]
    model = ScriptedToolCallingModel(responses=responses)

    result = _run(AgentService(tmp_path, model), "keep trying", max_steps=2)

    assert result.status == "max_steps_exceeded"
    assert result.steps == 2
    assert len(result.tool_executions) == 2
    assert all(record.error_code == "sensitive_path_denied" for record in result.tool_executions)


def test_max_steps_terminates_after_last_tool_batch(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    repeated = AIMessage(
        content="",
        tool_calls=[_call("read_file", {"path": "a.txt"}, "repeat")],
    )
    model = ScriptedToolCallingModel(responses=[repeated, repeated.model_copy(deep=True)])

    result = _run(AgentService(tmp_path, model), "repeat", max_steps=2)

    assert result.status == "max_steps_exceeded"
    assert result.steps == 2
    assert len(result.tool_executions) == 2
    assert len(model.received_messages) == 2


def test_model_error_and_empty_answer_are_terminal(tmp_path: Path) -> None:
    failed = _run(
        AgentService(
            tmp_path,
            ScriptedToolCallingModel(responses=[], raise_on_invoke=True),
        ),
        "fail",
    )
    empty = _run(
        AgentService(tmp_path, ScriptedToolCallingModel(responses=[AIMessage(content="")])),
        "empty",
    )

    assert failed.status == "model_error"
    assert failed.error is not None and failed.error.message.endswith("RuntimeError")
    assert empty.status == "invalid_model_response"
    assert empty.error is not None and "empty" in empty.error.message.lower()


def test_non_ai_response_and_tool_binding_failure_are_stable(tmp_path: Path) -> None:
    non_ai = _run(
        AgentService(tmp_path, ScriptedToolCallingModel(responses=[HumanMessage(content="wrong")])),
        "invalid",
    )
    binding = _run(AgentService(tmp_path, BindingFailureModel(responses=[])), "bind")

    assert non_ai.status == "invalid_model_response"
    assert non_ai.error is not None and non_ai.error.message == "Model must return an AIMessage"
    assert binding.status == "model_error"
    assert binding.steps == 0
    assert binding.error is not None and binding.error.message.endswith("RuntimeError")


def test_graph_builder_rejects_duplicate_tool_names() -> None:
    def first(value: int) -> str:
        """First duplicate."""

        return str(value)

    def second(value: int) -> str:
        """Second duplicate."""

        return str(value)

    tools = [
        StructuredTool.from_function(first, name="duplicate"),
        StructuredTool.from_function(second, name="duplicate"),
    ]
    model = ScriptedToolCallingModel(responses=[AIMessage(content="unused")])

    with pytest.raises(ValueError, match="tool names must be unique"):
        build_agent_graph(model, tools, None, None)  # type: ignore[arg-type]


def test_unexpected_graph_recursion_error_is_converted_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecursingGraph:
        async def ainvoke(self, *args: object, **kwargs: object) -> None:
            raise GraphRecursionError("internal graph detail")

    monkeypatch.setattr(
        "repopilot.services.agent_service.build_agent_graph",
        lambda *args, **kwargs: RecursingGraph(),
    )
    service = AgentService(
        tmp_path,
        ScriptedToolCallingModel(responses=[AIMessage(content="unused")]),
    )

    result = _run(service, "defensive failure")

    assert result.status == "invalid_model_response"
    assert result.error is not None
    assert result.error.message == "Agent graph exceeded its defensive recursion limit"
    assert "internal graph detail" not in result.model_dump_json()


def test_compiled_graph_reuse_does_not_share_state_between_invocations(tmp_path: Path) -> None:
    model = ScriptedToolCallingModel(
        responses=[AIMessage(content="first"), AIMessage(content="second")]
    )
    tools, executor, applicator = _runtime(tmp_path)
    graph = build_agent_graph(model, tools, executor, applicator)

    first = asyncio.run(
        graph.ainvoke(
            create_initial_state("goal one", 2, "run-one"),
            {"configurable": {"thread_id": "run-one"}},
        )
    )
    second = asyncio.run(
        graph.ainvoke(
            create_initial_state("goal two", 2, "run-two"),
            {"configurable": {"thread_id": "run-two"}},
        )
    )

    assert first["model_final_text"] == "first"
    assert second["model_final_text"] == "second"
    assert (
        first["final_answer"]
        == second["final_answer"]
        == ("The run completed without applying a patch.")
    )
    assert len(first["messages"]) == len(second["messages"]) == 2
    assert first["messages"][0].content == "goal one"
    assert second["messages"][0].content == "goal two"


def test_final_graph_state_has_complete_protocol_sequence(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    model = ScriptedToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_call("read_file", {"path": "a.txt"}, "read")],
            ),
            AIMessage(content="done"),
        ]
    )
    tools, executor, applicator = _runtime(tmp_path)
    graph = build_agent_graph(model, tools, executor, applicator)

    final = asyncio.run(
        graph.ainvoke(
            create_initial_state("goal", 3, "run-final"),
            {"configurable": {"thread_id": "run-final"}},
        )
    )

    assert [message.type for message in final["messages"]] == ["human", "ai", "tool", "ai"]
    assert final["model_calls"] == 2
    assert final["status"] == "success"
    assert final["tool_executions"][0]["tool_call_id"] == "read"
