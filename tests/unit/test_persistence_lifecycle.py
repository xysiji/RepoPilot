"""SQLite resource ownership and data-directory boundary tests."""

import asyncio
from pathlib import Path

import pytest

from repopilot.infrastructure.config import AppSettings
from repopilot.persistence.lifecycle import open_persistence


def test_lifecycle_creates_two_databases_and_closes_them(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = tmp_path / "server-data"
    settings = AppSettings(workspace_path=workspace, data_directory=data, _env_file=None)

    async def scenario() -> None:
        resources = await open_persistence(settings)
        assert (data / "checkpoints.sqlite3").is_file()
        assert (data / "runtime.sqlite3").is_file()
        await resources.close()
        with pytest.raises(ValueError, match="no active connection"):
            await resources.checkpoint_connection.execute("SELECT 1")
        with pytest.raises(ValueError, match="no active connection"):
            await resources.runtime_connection.execute("SELECT 1")

    asyncio.run(scenario())


def test_data_directory_cannot_be_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = AppSettings(
        workspace_path=workspace,
        data_directory=workspace / ".repopilot",
        _env_file=None,
    )
    with pytest.raises(ValueError, match="outside workspace"):
        asyncio.run(open_persistence(settings))


def test_data_directory_cannot_be_inside_demo_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    demo_workspace = tmp_path / "demo_workspace"
    demo_workspace.mkdir()
    settings = AppSettings(
        workspace_path=workspace,
        data_directory=demo_workspace / ".repopilot",
        _env_file=None,
    )
    with pytest.raises(ValueError, match="outside workspace"):
        asyncio.run(open_persistence(settings))
