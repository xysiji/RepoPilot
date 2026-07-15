"""Explicit, non-destructive runtime database schema migration."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from repopilot.persistence.contracts import UnsupportedSchemaVersionError

RUNTIME_SCHEMA_VERSION = 1


async def migrate_runtime_database(connection: aiosqlite.Connection) -> None:
    """Create v1 once and fail closed for every unknown schema version."""

    await connection.execute("BEGIN IMMEDIATE")
    try:
        table = await connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("schema_info",),
        )
        exists = await table.fetchone()
        await table.close()
        if exists is None:
            await _create_v1(connection)
        else:
            cursor = await connection.execute(
                "SELECT schema_version FROM schema_info WHERE singleton_id = ?",
                (1,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None or int(row[0]) != RUNTIME_SCHEMA_VERSION:
                found = "missing" if row is None else str(row[0])
                raise UnsupportedSchemaVersionError(
                    f"Unsupported runtime database schema version: {found}"
                )
        await connection.commit()
    except Exception:
        await connection.rollback()
        raise


async def _create_v1(connection: aiosqlite.Connection) -> None:
    now = datetime.now(UTC).isoformat()
    statements = [
        """
        CREATE TABLE schema_info (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            schema_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL UNIQUE,
            state_schema_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            outcome TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            current_proposal_id TEXT,
            repair_attempts INTEGER NOT NULL DEFAULT 0,
            max_repair_attempts INTEGER NOT NULL,
            model_calls INTEGER NOT NULL DEFAULT 0,
            latest_test_outcome TEXT,
            review_status TEXT,
            goal_sha256 TEXT NOT NULL,
            final_report_json TEXT,
            deleted_at TEXT,
            cleanup_status TEXT,
            cleanup_error TEXT
        )
        """,
        "CREATE INDEX runs_updated_at_idx ON runs(updated_at DESC, run_id DESC)",
        "CREATE INDEX runs_status_idx ON runs(status, updated_at DESC)",
        """
        CREATE TABLE trace_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL,
            run_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            node_name TEXT,
            phase TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            safe_payload_json TEXT NOT NULL,
            UNIQUE(run_id, event_key),
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX trace_run_event_idx ON trace_events(run_id, event_id)",
    ]
    for statement in statements:
        await connection.execute(statement)
    await connection.execute(
        "INSERT INTO schema_info(singleton_id, schema_version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (1, RUNTIME_SCHEMA_VERSION, now, now),
    )
