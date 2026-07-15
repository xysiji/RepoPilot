"""FastAPI-owned lifecycle for the two local SQLite databases."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from repopilot.infrastructure.config import AppSettings
from repopilot.persistence.migrations import migrate_runtime_database
from repopilot.persistence.runtime_store import RuntimeStore


class PersistenceResources:
    """Open connections and stores owned by exactly one application lifespan."""

    def __init__(
        self,
        *,
        checkpoint_connection: aiosqlite.Connection,
        runtime_connection: aiosqlite.Connection,
        checkpointer: AsyncSqliteSaver,
        runtime_store: RuntimeStore,
    ) -> None:
        self.checkpoint_connection = checkpoint_connection
        self.runtime_connection = runtime_connection
        self.checkpointer = checkpointer
        self.runtime_store = runtime_store

    async def close(self) -> None:
        await self.checkpoint_connection.close()
        await self.runtime_connection.close()


async def open_persistence(settings: AppSettings) -> PersistenceResources:
    """Validate paths, create the server directory, and initialize both databases."""

    data_directory = _validated_data_directory(settings)
    data_directory.mkdir(parents=True, exist_ok=True)
    checkpoint_path = data_directory / settings.checkpoint_database_name
    runtime_path = data_directory / settings.runtime_database_name
    checkpoint_connection = await aiosqlite.connect(checkpoint_path)
    runtime_connection = await aiosqlite.connect(runtime_path)
    try:
        checkpoint_connection.row_factory = aiosqlite.Row
        runtime_connection.row_factory = aiosqlite.Row
        await _configure_connection(checkpoint_connection)
        await _configure_connection(runtime_connection)
        checkpointer = AsyncSqliteSaver(
            checkpoint_connection,
            serde=JsonPlusSerializer(allowed_msgpack_modules=None),
        )
        await checkpointer.setup()
        await migrate_runtime_database(runtime_connection)
        return PersistenceResources(
            checkpoint_connection=checkpoint_connection,
            runtime_connection=runtime_connection,
            checkpointer=checkpointer,
            runtime_store=RuntimeStore(runtime_connection),
        )
    except Exception:
        await checkpoint_connection.close()
        await runtime_connection.close()
        raise


async def _configure_connection(connection: aiosqlite.Connection) -> None:
    await connection.execute("PRAGMA busy_timeout = 5000")
    await connection.execute("PRAGMA foreign_keys = ON")
    await connection.execute("PRAGMA journal_mode = WAL")
    await connection.commit()


def _validated_data_directory(settings: AppSettings) -> Path:
    data_directory = settings.data_directory.expanduser().resolve(strict=False)
    workspace = settings.workspace_path.expanduser().resolve(strict=False)
    demo_workspace = Path("demo_workspace").resolve(strict=False)
    if _contains(workspace, data_directory) or _contains(demo_workspace, data_directory):
        raise ValueError("data_directory must be outside workspace and demo_workspace")
    return data_directory


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
