"""Pure deterministic compaction that preserves AI/tool message protocol blocks."""

from __future__ import annotations

import json
from dataclasses import dataclass

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_to_dict,
)

from repopilot.context.contracts import (
    ContextBudgetExceededError,
    ContextPolicy,
    ContextProtocolError,
    ContextStats,
    ContextWindowResult,
)


@dataclass(frozen=True)
class _Block:
    messages: tuple[BaseMessage, ...]
    tool_exchange: bool = False


class ContextManager:
    """Build a bounded model view without changing persisted state messages."""

    def __init__(self, policy: ContextPolicy) -> None:
        self._policy = policy

    def build(self, messages: list[BaseMessage]) -> ContextWindowResult:
        if not messages:
            raise ContextProtocolError("context requires at least one message")
        blocks = _group_protocol_blocks(messages)
        original_characters = _messages_size(messages)
        if original_characters <= self._policy.max_characters:
            return ContextWindowResult(
                list(messages),
                ContextStats(
                    original_message_count=len(messages),
                    model_message_count=len(messages),
                    original_characters=original_characters,
                    model_characters=original_characters,
                    compacted_block_count=0,
                    dropped_block_count=0,
                    tool_results_compacted=0,
                ),
            )

        selected = _selected_indices(blocks, self._policy.recent_blocks)
        compacted: list[BaseMessage] = []
        compacted_blocks = 0
        compacted_results = 0
        dropped = len(blocks) - len(selected)
        if dropped:
            summary = _summary_message(blocks, selected, self._policy.summary_max_characters)
            if summary is not None:
                compacted.append(summary)
        for index in sorted(selected):
            block = blocks[index]
            if block.tool_exchange:
                block_messages, result_count = _compact_tool_block(
                    block.messages,
                    self._policy.tool_result_max_characters,
                )
                if list(block.messages) != block_messages:
                    compacted_blocks += 1
                compacted_results += result_count
                compacted.extend(block_messages)
            else:
                compacted.extend(block.messages)

        compacted = _fit_plain_messages(compacted, self._policy.max_characters)
        model_characters = _messages_size(compacted)
        if model_characters > self._policy.max_characters:
            raise ContextBudgetExceededError(
                "protocol-safe required context exceeds the configured character budget"
            )
        return ContextWindowResult(
            compacted,
            ContextStats(
                original_message_count=len(messages),
                model_message_count=len(compacted),
                original_characters=original_characters,
                model_characters=model_characters,
                compacted_block_count=compacted_blocks,
                dropped_block_count=dropped,
                tool_results_compacted=compacted_results,
            ),
        )


def _group_protocol_blocks(messages: list[BaseMessage]) -> list[_Block]:
    blocks: list[_Block] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if isinstance(message, ToolMessage):
            raise ContextProtocolError("standalone ToolMessage is not protocol safe")
        if isinstance(message, AIMessage) and message.tool_calls:
            expected = [str(call.get("id", "")) for call in message.tool_calls]
            if any(not call_id for call_id in expected):
                raise ContextProtocolError("tool call ID is missing")
            exchange: list[BaseMessage] = [message]
            for expected_id in expected:
                index += 1
                if index >= len(messages):
                    raise ContextProtocolError("tool exchange is missing ToolMessage")
                tool_message = messages[index]
                if not isinstance(tool_message, ToolMessage):
                    raise ContextProtocolError("tool exchange was interrupted")
                if tool_message.tool_call_id != expected_id:
                    raise ContextProtocolError("ToolMessage ID does not match its tool call")
                exchange.append(tool_message)
            blocks.append(_Block(tuple(exchange), tool_exchange=True))
        else:
            blocks.append(_Block((message,)))
        index += 1
    return blocks


def _selected_indices(blocks: list[_Block], recent: int) -> set[int]:
    selected = {0}
    selected.update(range(max(0, len(blocks) - recent), len(blocks)))
    for keyword in ("propose_patch", "pytest", "error"):
        for index in range(len(blocks) - 1, -1, -1):
            if keyword in _block_marker(blocks[index]):
                selected.add(index)
                break
    return selected


def _block_marker(block: _Block) -> str:
    values: list[str] = []
    for message in block.messages:
        if isinstance(message, AIMessage):
            values.extend(str(call.get("name", "")) for call in message.tool_calls)
        if isinstance(message, ToolMessage):
            values.append(str(message.name or ""))
            if message.status == "error":
                values.append("error")
        elif isinstance(message.content, str):
            values.append(message.content[:256])
    return " ".join(values).casefold()


def _summary_message(
    blocks: list[_Block], selected: set[int], maximum: int
) -> SystemMessage | None:
    dropped = [block for index, block in enumerate(blocks) if index not in selected]
    if not dropped:
        return None
    tool_names: list[str] = []
    for block in dropped:
        for message in block.messages:
            if isinstance(message, AIMessage):
                tool_names.extend(str(call.get("name", "unknown")) for call in message.tool_calls)
    names = ", ".join(dict.fromkeys(tool_names)) or "none"
    content = (
        f"[deterministic context summary] {len(dropped)} older message blocks omitted; "
        f"tools observed: {names}. Full history remains in the checkpoint."
    )
    return SystemMessage(content=content[:maximum])


def _compact_tool_block(
    messages: tuple[BaseMessage, ...], maximum: int
) -> tuple[list[BaseMessage], int]:
    ai = messages[0]
    assert isinstance(ai, AIMessage)
    output: list[BaseMessage] = [ai]
    compacted_results = 0
    for message in messages[1:]:
        assert isinstance(message, ToolMessage)
        content = (
            message.content if isinstance(message.content, str) else json.dumps(message.content)
        )
        if len(content) > maximum:
            compacted_results += 1
            content = json.dumps(
                {
                    "compacted": True,
                    "original_characters": len(content),
                    "status": message.status or "unknown",
                },
                separators=(",", ":"),
            )
        output.append(
            ToolMessage(
                content=content,
                tool_call_id=message.tool_call_id,
                name=message.name,
                status=message.status,
            )
        )
    return output, compacted_results


def _fit_plain_messages(messages: list[BaseMessage], maximum: int) -> list[BaseMessage]:
    """Trim only plain text messages; tool protocol messages remain atomic."""

    if _messages_size(messages) <= maximum:
        return messages
    output = list(messages)
    for index in range(1, len(output) - 1):
        message = output[index]
        if isinstance(message, (AIMessage, ToolMessage)):
            continue
        content = message.content
        if not isinstance(content, str) or len(content) <= 256:
            continue
        replacement = content[:256] + "\n[context text truncated]"
        if isinstance(message, HumanMessage):
            output[index] = HumanMessage(content=replacement)
        else:
            output[index] = SystemMessage(content=replacement)
        if _messages_size(output) <= maximum:
            return output
    return output


def _messages_size(messages: list[BaseMessage]) -> int:
    return len(
        json.dumps(
            [message_to_dict(message) for message in messages],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
