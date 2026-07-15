"""State initialization and reducer tests for the P2 graph."""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from repopilot.agent.state import AgentState, create_initial_state
from repopilot.tools.contracts import ToolExecutionPhase, ToolExecutionRecord


def test_initial_state_uses_fresh_containers_and_model_round_semantics() -> None:
    first = create_initial_state("first", 3)
    second = create_initial_state("second", 4)

    assert first["model_calls"] == 0
    assert first["max_steps"] == 3
    assert first["status"] == "running"
    assert isinstance(first["messages"][0], HumanMessage)
    assert first["messages"] is not second["messages"]
    assert first["tool_executions"] is not second["tool_executions"]
    assert set(AgentState.__annotations__) == {
        "messages",
        "run_id",
        "state_schema_version",
        "model_calls",
        "max_steps",
        "status",
        "final_answer",
        "error",
        "tool_executions",
        "pending_approval",
        "approval_decision",
        "repair_attempts",
        "max_repair_attempts",
        "test_runs",
        "latest_test_result",
        "applied_patch_context",
        "applied_patches",
        "review_result",
        "final_report",
        "approval_count",
        "model_final_text",
        "last_patch_error_code",
        "latest_context_stats",
    }
    assert first["pending_approval"] is None
    assert first["state_schema_version"] == 1
    assert first["latest_context_stats"] is None
    assert first["approval_decision"] is None
    assert first["repair_attempts"] == 0
    assert first["max_repair_attempts"] == 3
    assert first["test_runs"] is not second["test_runs"]
    assert first["applied_patches"] is not second["applied_patches"]


def test_message_and_execution_reducers_append_partial_node_updates() -> None:
    execution = ToolExecutionRecord(
        step=1,
        tool_name="read_file",
        tool_call_id="call-1",
        input={"path": "README.md"},
        success=True,
        output_summary="read README.md",
        phase=ToolExecutionPhase.NORMALIZATION,
    )

    def append_updates(state: AgentState) -> dict[str, object]:
        assert len(state["messages"]) == 1
        return {
            "messages": [AIMessage(content="done")],
            "tool_executions": [execution.model_dump(mode="json")],
        }

    builder = StateGraph(AgentState)
    builder.add_node("append", append_updates)
    builder.add_edge(START, "append")
    builder.add_edge("append", END)

    result = builder.compile().invoke(create_initial_state("goal", 2))

    assert [message.type for message in result["messages"]] == ["human", "ai"]
    assert result["tool_executions"] == [execution.model_dump(mode="json")]
