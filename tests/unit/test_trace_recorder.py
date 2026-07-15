"""Trace redaction and idempotency tests."""

import asyncio
from typing import Any

import aiosqlite
import pytest

from repopilot.agent.state import AgentState, create_initial_state
from repopilot.persistence.migrations import migrate_runtime_database
from repopilot.persistence.runtime_store import RuntimeStore
from repopilot.tracing.contracts import TraceEvent, TraceEventType
from repopilot.tracing.nodes import traced_async_node
from repopilot.tracing.recorder import TraceRecorder
from repopilot.tracing.sanitization import sanitize_trace_payload


def test_trace_sanitization_drops_content_and_rejects_secret_keys() -> None:
    safe = sanitize_trace_payload(
        {"model_calls": 2, "content": "source", "diff": "patch", "status": "ok"}
    )
    assert safe == {"model_calls": 2, "status": "ok"}
    with pytest.raises(ValueError, match="sensitive"):
        sanitize_trace_payload({"api_key": "secret"})


def test_recorder_uses_event_key_for_idempotency() -> None:
    async def scenario() -> None:
        connection = await aiosqlite.connect(":memory:")
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        try:
            await migrate_runtime_database(connection)
            store = RuntimeStore(connection)
            await store.create_run(
                run_id="run",
                thread_id="run",
                state_schema_version=1,
                goal="goal",
                max_repair_attempts=1,
            )
            recorder = TraceRecorder(store, max_events_per_run=10)
            event = TraceEvent(
                event_key="model:1",
                run_id="run",
                event_type=TraceEventType.MODEL_COMPLETED,
                node_name="model",
                phase="model",
                status="running",
                payload={"model_calls": 1},
            )
            assert await recorder.record(event) is True
            assert await recorder.record(event) is False
            events = await store.list_trace_events("run")
            assert len(events) == 1 and events[0].safe_payload == {"model_calls": 1}
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_compaction_trace_is_ordered_and_replay_idempotent() -> None:
    async def scenario() -> None:
        connection = await aiosqlite.connect(":memory:")
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        try:
            await migrate_runtime_database(connection)
            store = RuntimeStore(connection)
            await store.create_run(
                run_id="run",
                thread_id="run",
                state_schema_version=1,
                goal="goal",
                max_repair_attempts=1,
            )
            recorder = TraceRecorder(store, max_events_per_run=10)

            async def compacted_model_node(state: AgentState) -> dict[str, Any]:
                del state
                return {
                    "model_calls": 1,
                    "status": "running",
                    "latest_context_stats": {
                        "original_message_count": 10,
                        "model_message_count": 4,
                        "original_characters": 5_000,
                        "model_characters": 1_000,
                        "compacted_block_count": 2,
                        "dropped_block_count": 3,
                        "tool_results_compacted": 2,
                    },
                }

            node = traced_async_node(
                compacted_model_node,
                recorder,
                node_name="model",
                event_type=TraceEventType.MODEL_COMPLETED,
            )
            state = create_initial_state("goal", 3, "run", 1)
            await node(state)
            await node(state)
            events = await store.list_trace_events("run")
            assert [event.event_type for event in events] == [
                "context_compacted",
                "model_completed",
            ]
        finally:
            await connection.close()

    asyncio.run(scenario())
