"""Pure routing tests for every legal P2 terminal and continuation branch."""

from copy import deepcopy

from langchain_core.messages import AIMessage
from langgraph.graph import END

from repopilot.agent.routing import route_after_model, route_after_tools
from repopilot.agent.state import create_initial_state


def _call() -> dict[str, object]:
    return {"name": "read_file", "args": {"path": "README.md"}, "id": "c1"}


def test_route_after_model_sends_tool_calls_to_tools_without_mutating_state() -> None:
    state = create_initial_state("goal", 3)
    state["messages"].append(AIMessage(content="", tool_calls=[_call()]))
    before = deepcopy(state)

    assert route_after_model(state) == "tools"
    assert state == before


def test_route_after_model_ends_for_all_terminal_statuses() -> None:
    for status in (
        "success",
        "max_steps_exceeded",
        "model_error",
        "invalid_model_response",
    ):
        state = create_initial_state("goal", 3)
        state["status"] = status
        assert route_after_model(state) == END


def test_route_after_model_defensively_ends_running_state_without_tool_calls() -> None:
    state = create_initial_state("goal", 3)
    state["messages"].append(AIMessage(content="answer"))

    assert route_after_model(state) == END


def test_route_after_tools_only_returns_to_model_while_running() -> None:
    running = create_initial_state("goal", 3)
    terminal = create_initial_state("goal", 3)
    terminal["status"] = "max_steps_exceeded"

    assert route_after_tools(running) == "model"
    assert route_after_tools(terminal) == END
