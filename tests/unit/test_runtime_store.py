"""Runtime schema, summary, pagination, and trace persistence tests."""

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from repopilot.persistence.contracts import (
    TERMINAL_RUN_STATUSES,
    RunNotFoundError,
    UnsupportedSchemaVersionError,
)
from repopilot.persistence.migrations import migrate_runtime_database
from repopilot.persistence.runtime_store import RuntimeStore


def test_context_failures_are_terminal_and_cleanup_eligible() -> None:
    assert "context_budget_exceeded" in TERMINAL_RUN_STATUSES
    assert "context_protocol_error" in TERMINAL_RUN_STATUSES


async def _connection(path: Path | str = ":memory:") -> aiosqlite.Connection:
    connection = await aiosqlite.connect(path)
    connection.row_factory = aiosqlite.Row
    await connection.execute("PRAGMA foreign_keys = ON")
    return connection


def test_v1_migration_is_idempotent_and_future_version_fails_closed() -> None:
    async def scenario() -> None:
        connection = await _connection()
        try:
            await migrate_runtime_database(connection)
            await migrate_runtime_database(connection)
            await connection.execute(
                "UPDATE schema_info SET schema_version = ? WHERE singleton_id = ?",
                (99, 1),
            )
            await connection.commit()
            with pytest.raises(UnsupportedSchemaVersionError):
                await migrate_runtime_database(connection)
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_runtime_store_keeps_only_safe_run_metadata_and_stable_pages() -> None:
    async def scenario() -> None:
        connection = await _connection()
        try:
            await migrate_runtime_database(connection)
            store = RuntimeStore(connection)
            first = await store.create_run(
                run_id="run-a",
                thread_id="run-a",
                state_schema_version=1,
                goal="secret source request",
                max_repair_attempts=2,
            )
            await store.create_run(
                run_id="run-b",
                thread_id="run-b",
                state_schema_version=1,
                goal="other request",
                max_repair_attempts=2,
            )
            await store.update_run_from_state(
                "run-a",
                {
                    "state_schema_version": 1,
                    "status": "success",
                    "repair_attempts": 0,
                    "max_repair_attempts": 2,
                    "model_calls": 1,
                    "final_report": {
                        "run_id": "run-a",
                        "outcome": "no_change",
                        "summary": "safe summary",
                        "model_final_text": "source-secret-that-must-not-be-indexed",
                    },
                },
            )
            cursor = await connection.execute(
                "SELECT final_report_json FROM runs WHERE run_id = ?", ("run-a",)
            )
            report_json = str((await cursor.fetchone())[0])
            await cursor.close()
            assert "source-secret-that-must-not-be-indexed" not in report_json
            assert "safe summary" in report_json
            assert first.goal_sha256 != "secret source request"
            page = await store.list_runs(limit=1)
            assert len(page.items) == 1 and page.next_cursor is not None
            second_page = await store.list_runs(limit=1, cursor=page.next_cursor)
            assert len(second_page.items) == 1
            assert second_page.items[0].run_id != page.items[0].run_id
            with pytest.raises(ValueError, match="cursor"):
                await store.list_runs(limit=1, cursor="not-a-cursor")
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_trace_insert_is_idempotent_and_deleted_run_is_not_queryable() -> None:
    async def scenario() -> None:
        connection = await _connection()
        try:
            await migrate_runtime_database(connection)
            store = RuntimeStore(connection)
            await store.create_run(
                run_id="run-a",
                thread_id="run-a",
                state_schema_version=1,
                goal="goal",
                max_repair_attempts=2,
            )
            arguments = {
                "run_id": "run-a",
                "event_key": "start",
                "event_type": "run_started",
                "node_name": None,
                "phase": "service",
                "status": "running",
                "safe_payload": {"model_calls": 0},
                "max_events": 10,
            }
            assert await store.append_trace(**arguments) is True
            assert await store.append_trace(**arguments) is False
            assert len(await store.list_trace_events("run-a")) == 1
            await store.delete_runtime_data("run-a")
            with pytest.raises(RunNotFoundError):
                await store.get_run("run-a")
            with pytest.raises(RunNotFoundError):
                await store.list_trace_events("run-a")
        finally:
            await connection.close()

    asyncio.run(scenario())
