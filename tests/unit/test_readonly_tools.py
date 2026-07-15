"""Regression tests for bounded P3 read-only operations."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from repopilot.tools.contracts import ToolResultEnvelope
from repopilot.tools.readonly import build_readonly_tools


def _tools(workspace: Path) -> dict[str, object]:
    return {tool.name: tool for tool in build_readonly_tools(workspace)}


def _result(raw: str) -> ToolResultEnvelope:
    return ToolResultEnvelope.model_validate_json(raw)


def test_list_files_returns_sorted_paths_and_excludes_sensitive_names(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.py").write_text("b = 2\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    for hidden in (".git", ".venv", "__pycache__"):
        directory = tmp_path / hidden
        directory.mkdir()
        (directory / "secret.txt").write_text("hidden", encoding="utf-8")
    for secret in (".env", ".env.local", "id_rsa", "client.pem", "client.key"):
        (tmp_path / secret).write_text("secret", encoding="utf-8")
    (tmp_path / "nested" / ".env").write_text("nested", encoding="utf-8")

    result = _result(
        _tools(tmp_path)["list_files"].invoke({"directory": ".", "recursive": True, "max_depth": 3})
    )

    assert result.success is True
    assert result.data == {"paths": ["a.py", "nested/", "nested/b.py"], "truncated": False}


def test_list_files_is_non_recursive_by_default(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "module.py").write_text("pass\n", encoding="utf-8")

    result = _result(_tools(tmp_path)["list_files"].invoke({"directory": "."}))

    assert result.data == {"paths": ["pkg/"], "truncated": False}


def test_read_file_returns_utf8_content_and_metadata(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("你好，RepoPilot", encoding="utf-8")

    result = _result(_tools(tmp_path)["read_file"].invoke({"path": "README.md"}))

    assert result.success is True
    assert result.data == {
        "path": "README.md",
        "content": "你好，RepoPilot",
        "character_count": len("你好，RepoPilot"),
        "truncated": False,
    }


def test_read_file_classifies_missing_binary_and_invalid_encoding(tmp_path: Path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01")
    (tmp_path / "invalid.txt").write_bytes(b"\xff\xfe")

    missing = _result(_tools(tmp_path)["read_file"].invoke({"path": "missing.py"}))
    binary = _result(_tools(tmp_path)["read_file"].invoke({"path": "binary.bin"}))
    invalid = _result(_tools(tmp_path)["read_file"].invoke({"path": "invalid.txt"}))

    assert missing.error and missing.error.code == "not_found"
    assert binary.error and binary.error.code == "binary_file"
    assert invalid.error and invalid.error.code == "invalid_encoding"


def test_read_file_truncates_long_content_as_success(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("x" * 25_000, encoding="utf-8")

    result = _result(_tools(tmp_path)["read_file"].invoke({"path": "large.txt"}))

    assert result.success is True
    assert result.data and result.data["truncated"] is True
    assert result.data["character_count"] == 20_000
    assert len(result.data["content"]) == 20_000


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".ENV.LOCAL",
        "nested/.env",
        ".git/config",
        ".repopilot/runtime.sqlite3",
        ".venv/token",
        "__pycache__/x.pyc",
        "id_rsa",
        "id_ed25519.pub",
        "client.pem",
        "client.key",
    ],
)
def test_direct_tools_recheck_sensitive_paths(tmp_path: Path, path: str) -> None:
    raw = _tools(tmp_path)["read_file"].invoke({"path": path})
    result = _result(raw)

    assert result.error and result.error.category == "policy_denied"
    assert result.error.code == "sensitive_path_denied"
    assert path not in raw


@pytest.mark.parametrize("path", ["../outside.txt", "C:/secret.txt", "//server/share/x"])
def test_direct_tools_recheck_escape_paths(tmp_path: Path, path: str) -> None:
    raw = _tools(tmp_path)["read_file"].invoke({"path": path})
    result = _result(raw)

    assert result.success is False
    assert result.error and result.error.category == "policy_denied"
    assert path not in raw


def test_read_file_rejects_env_ntfs_stream_alias(tmp_path: Path) -> None:
    secret = "ADS_SECRET_MUST_NOT_BE_RETURNED"
    (tmp_path / ".env").write_text(secret, encoding="utf-8")

    raw = _tools(tmp_path)["read_file"].invoke({"path": ".env::$DATA"})
    result = _result(raw)

    assert result.error and result.error.code == "sensitive_path_denied"
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
    result = _result(raw)

    assert result.error and result.error.code == "outside_workspace_denied"
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
def test_all_tools_share_parent_escape_boundary(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    raw = _tools(tmp_path)[tool_name].invoke(arguments)
    result = _result(raw)

    assert result.error and result.error.code == "path_traversal_denied"
    assert str(tmp_path.parent) not in raw


def test_search_code_returns_stable_matches_and_honors_limit(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("needle = 2\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("first\nneedle = 1\nneedle = 3\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("needle", encoding="utf-8")

    result = _result(
        _tools(tmp_path)["search_code"].invoke(
            {"query": "needle", "file_suffix": ".py", "max_results": 2}
        )
    )

    assert result.data and result.data["truncated"] is True
    assert [(item["path"], item["line_number"]) for item in result.data["matches"]] == [
        ("a.py", 2),
        ("a.py", 3),
    ]


def test_search_skips_binary_invalid_utf8_and_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / "public.py").write_text("needle = 'public'\n", encoding="utf-8")
    (tmp_path / "binary.py").write_bytes(b"needle\x00")
    (tmp_path / "invalid.py").write_bytes(b"needle\xff")
    (tmp_path / ".env.local").write_text("needle=secret\n", encoding="utf-8")

    result = _result(_tools(tmp_path)["search_code"].invoke({"query": "needle"}))

    assert result.data and [match["path"] for match in result.data["matches"]] == ["public.py"]


def test_tool_argument_schema_rejects_blank_query_extra_fields_and_coercion(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)
    with pytest.raises(ValidationError):
        tools["search_code"].invoke({"query": "   "})
    with pytest.raises(ValidationError):
        tools["read_file"].invoke({"path": "README.md", "unexpected": True})
    with pytest.raises(ValidationError):
        tools["list_files"].invoke({"recursive": "false"})
    with pytest.raises(ValidationError):
        tools["search_code"].invoke({"query": "x", "max_results": True})


def test_direct_tool_output_is_stable_json_envelope(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    raw = _tools(tmp_path)["read_file"].invoke({"path": "a.txt"})

    assert list(json.loads(raw)) == ["success", "data", "error"]
    assert "PosixPath" not in raw and "WindowsPath" not in raw
