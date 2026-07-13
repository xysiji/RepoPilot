"""Tests for P1 workspace containment and structured read-only tools."""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from repopilot.schemas.agent import ListFilesResult, ReadFileResult, SearchCodeResult
from repopilot.tools.readonly import build_readonly_tools


def _tools(workspace: Path) -> dict[str, object]:
    return {tool.name: tool for tool in build_readonly_tools(workspace)}


def test_list_files_returns_sorted_relative_paths_and_excludes_sensitive_dirs(
    tmp_path: Path,
) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.py").write_text("b = 2\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    for hidden in (".git", ".venv", "__pycache__"):
        directory = tmp_path / hidden
        directory.mkdir()
        (directory / "secret.txt").write_text("hidden", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / ".env.local").write_text("TOKEN=local-secret", encoding="utf-8")
    (tmp_path / "nested" / ".env").write_text("TOKEN=nested-secret", encoding="utf-8")

    raw = _tools(tmp_path)["list_files"].invoke(
        {"directory": ".", "recursive": True, "max_depth": 3}
    )
    result = ListFilesResult.model_validate_json(raw)

    assert result.success is True
    assert result.paths == ["a.py", "nested/", "nested/b.py"]
    assert all(".git" not in path and ".venv" not in path for path in result.paths)
    assert all("__pycache__" not in path and ".env" not in path for path in result.paths)


def test_list_files_is_non_recursive_by_default(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "module.py").write_text("pass\n", encoding="utf-8")

    raw = _tools(tmp_path)["list_files"].invoke({"directory": "."})
    result = ListFilesResult.model_validate_json(raw)

    assert result.paths == ["pkg/"]


def test_read_file_returns_utf8_content_and_metadata(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("你好，RepoPilot", encoding="utf-8")

    raw = _tools(tmp_path)["read_file"].invoke({"path": "README.md"})
    result = ReadFileResult.model_validate_json(raw)

    assert result.success is True
    assert result.path == "README.md"
    assert result.content == "你好，RepoPilot"
    assert result.character_count == len(result.content)
    assert result.truncated is False


def test_read_file_missing_returns_structured_error(tmp_path: Path) -> None:
    raw = _tools(tmp_path)["read_file"].invoke({"path": "missing.py"})
    result = ReadFileResult.model_validate_json(raw)

    assert result.success is False
    assert result.error_type == "not_found"
    assert result.content == ""


def test_read_file_rejects_binary_content(tmp_path: Path) -> None:
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")

    raw = _tools(tmp_path)["read_file"].invoke({"path": "data.bin"})
    result = ReadFileResult.model_validate_json(raw)

    assert result.success is False
    assert result.error_type == "binary_file"


def test_read_file_truncates_long_content(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("x" * 25_000, encoding="utf-8")

    raw = _tools(tmp_path)["read_file"].invoke({"path": "large.txt"})
    result = ReadFileResult.model_validate_json(raw)

    assert result.success is True
    assert result.truncated is True
    assert result.character_count == 20_000
    assert len(result.content) == 20_000


@pytest.mark.parametrize(
    "path",
    [".env", ".env.local", ".ENV.LOCAL", "nested/.env", ".git/config", ".venv/token"],
)
def test_read_file_rejects_sensitive_paths(tmp_path: Path, path: str) -> None:
    raw = _tools(tmp_path)["read_file"].invoke({"path": path})
    result = ReadFileResult.model_validate_json(raw)

    assert result.success is False
    assert result.error_type == "permission_denied"


def test_read_file_rejects_parent_traversal(tmp_path: Path) -> None:
    raw = _tools(tmp_path)["read_file"].invoke({"path": "../outside.txt"})
    result = ReadFileResult.model_validate_json(raw)

    assert result.success is False
    assert result.error_type == "permission_denied"


def test_read_file_rejects_absolute_path(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    raw = _tools(tmp_path)["read_file"].invoke({"path": str(outside.resolve())})
    result = ReadFileResult.model_validate_json(raw)

    assert result.success is False
    assert result.error_type == "permission_denied"


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS ADS syntax is Windows-specific")
def test_read_file_rejects_env_through_ntfs_data_stream_alias(tmp_path: Path) -> None:
    secret = "ADS_SECRET_MUST_NOT_BE_RETURNED"
    (tmp_path / ".env").write_text(secret, encoding="utf-8")

    raw = _tools(tmp_path)["read_file"].invoke({"path": ".env::$DATA"})
    result = ReadFileResult.model_validate_json(raw)

    assert result.success is False
    assert result.error_type == "permission_denied"
    assert secret not in raw


def test_read_file_rejects_path_resolving_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside secret", encoding="utf-8")
    link = tmp_path / "outside-link.txt"
    read_tool = _tools(tmp_path)["read_file"]
    original_resolve = Path.resolve

    def resolve_alias(path: Path, strict: bool = False) -> Path:
        if path == link:
            return original_resolve(outside, strict=True)
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_alias)

    raw = read_tool.invoke({"path": link.name})
    result = ReadFileResult.model_validate_json(raw)

    assert result.success is False
    assert result.error_type == "permission_denied"
    assert "outside secret" not in raw
    assert str(outside) not in raw


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("list_files", {"directory": ".."}),
        ("read_file", {"path": "../outside.txt"}),
        ("search_code", {"query": "needle", "directory": ".."}),
    ],
)
def test_all_readonly_tools_share_parent_escape_boundary(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    raw = _tools(tmp_path)[tool_name].invoke(arguments)
    result = json.loads(raw)

    assert result["success"] is False
    assert result["error_type"] == "permission_denied"
    assert str(tmp_path.parent) not in raw


def test_search_code_returns_stable_path_line_and_text(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("needle = 2\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("first\nneedle = 1\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("needle", encoding="utf-8")

    raw = _tools(tmp_path)["search_code"].invoke(
        {"query": "needle", "file_suffix": ".py", "max_results": 10}
    )
    result = SearchCodeResult.model_validate_json(raw)

    assert result.success is True
    assert [(item.path, item.line_number, item.line) for item in result.matches] == [
        ("a.py", 2, "needle = 1"),
        ("b.py", 1, "needle = 2"),
    ]


def test_search_code_enforces_max_results(tmp_path: Path) -> None:
    (tmp_path / "many.py").write_text("needle\nneedle\nneedle\n", encoding="utf-8")

    raw = _tools(tmp_path)["search_code"].invoke({"query": "needle", "max_results": 2})
    result = SearchCodeResult.model_validate_json(raw)

    assert len(result.matches) == 2
    assert result.truncated is True


def test_search_code_empty_query_returns_argument_error(tmp_path: Path) -> None:
    raw = _tools(tmp_path)["search_code"].invoke({"query": "   "})
    result = SearchCodeResult.model_validate_json(raw)

    assert result.success is False
    assert result.error_type == "invalid_arguments"


def test_search_code_excludes_sensitive_paths(tmp_path: Path) -> None:
    (tmp_path / "public.py").write_text("needle = 'public'\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("needle=secret\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "secret.py").write_text("needle = 'secret'\n", encoding="utf-8")

    raw = _tools(tmp_path)["search_code"].invoke({"query": "needle"})
    result = SearchCodeResult.model_validate_json(raw)

    assert result.success is True
    assert [match.path for match in result.matches] == ["public.py"]


def test_tool_argument_schema_forbids_extra_fields(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _tools(tmp_path)["read_file"].invoke({"path": "README.md", "unexpected": True})
