"""Protocol-safe deterministic context compaction tests."""

import copy
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from repopilot.context.contracts import (
    ContextBudgetExceededError,
    ContextPolicy,
    ContextProtocolError,
)
from repopilot.context.manager import ContextManager


def _manager(maximum: int = 2_000) -> ContextManager:
    return ContextManager(
        ContextPolicy(
            max_characters=maximum,
            recent_blocks=2,
            tool_result_max_characters=128,
            summary_max_characters=200,
        )
    )


def test_small_context_is_returned_unchanged_with_safe_stats() -> None:
    messages = [HumanMessage(content="goal"), AIMessage(content="answer")]
    window = _manager().build(messages)

    assert window.messages == messages
    assert window.stats.original_message_count == 2
    assert window.stats.dropped_block_count == 0


def test_compaction_preserves_atomic_tool_protocol_and_does_not_mutate_state() -> None:
    messages = [HumanMessage(content="goal")]
    for index in range(4):
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": f"file-{index}.py"},
                            "id": f"call-{index}",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content=json.dumps({"content": "x" * 800}),
                    tool_call_id=f"call-{index}",
                    name="read_file",
                ),
            ]
        )
    original = copy.deepcopy(messages)

    window = _manager().build(messages)

    assert messages == original
    assert window.stats.dropped_block_count > 0
    assert window.stats.tool_results_compacted > 0
    for index, message in enumerate(window.messages):
        if isinstance(message, AIMessage) and message.tool_calls:
            following = window.messages[index + 1]
            assert isinstance(following, ToolMessage)
            assert following.tool_call_id == message.tool_calls[0]["id"]
        if isinstance(message, ToolMessage):
            assert isinstance(json.loads(str(message.content)), dict)


def test_malformed_tool_protocol_fails_instead_of_splitting_messages() -> None:
    with pytest.raises(ContextProtocolError, match="standalone"):
        _manager().build([HumanMessage(content="goal"), ToolMessage(content="x", tool_call_id="x")])


def test_multi_tool_exchange_keeps_all_results_in_model_order() -> None:
    messages = [
        HumanMessage(content="goal"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "a.py"},
                    "id": "a",
                    "type": "tool_call",
                },
                {
                    "name": "read_file",
                    "args": {"path": "b.py"},
                    "id": "b",
                    "type": "tool_call",
                },
            ],
        ),
        ToolMessage(content="a", tool_call_id="a"),
        ToolMessage(content="b", tool_call_id="b"),
    ]
    window = _manager().build(messages)
    assert [
        message.tool_call_id for message in window.messages if isinstance(message, ToolMessage)
    ] == ["a", "b"]


def test_oversized_required_tool_block_fails_with_stable_budget_error() -> None:
    messages = [
        HumanMessage(content="goal"),
        AIMessage(
            content="y" * 4_000,
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "a.py"},
                    "id": "call",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="ok", tool_call_id="call", name="read_file"),
    ]
    with pytest.raises(ContextBudgetExceededError):
        _manager().build(messages)
