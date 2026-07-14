"""Three bounded, workspace-only, read-only LangChain tools for P3."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from repopilot.tools.contracts import (
    TOOL_LIMITS,
    ListFilesArgs,
    ReadFileArgs,
    SearchCodeArgs,
    SearchMatch,
    ToolErrorCode,
    ToolExecutionPhase,
    ToolFailureCategory,
    failed_result,
    successful_result,
)
from repopilot.tools.policy import WorkspaceGuard, WorkspacePolicyError

_MAX_READ_BYTES = TOOL_LIMITS.max_read_characters * 4 + 4


def _failure_json(
    category: ToolFailureCategory,
    code: ToolErrorCode,
    message: str,
) -> str:
    return failed_result(
        phase=ToolExecutionPhase.EXECUTION,
        category=category,
        code=code,
        message=message,
    ).stable_json()


def _workspace_failure(error: WorkspacePolicyError) -> str:
    return _failure_json(
        ToolFailureCategory.POLICY_DENIED,
        error.code,
        "The path no longer satisfies the workspace safety policy.",
    )


def _iter_search_files(guard: WorkspaceGuard, directory: Path) -> Iterator[Path]:
    pending: list[tuple[Path, int]] = [(directory, 0)]
    files: list[Path] = []
    while pending:
        current, depth = pending.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            continue
        for entry in entries:
            if not guard.is_safe_discovered_path(entry):
                continue
            if entry.is_dir() and depth < TOOL_LIMITS.max_search_depth:
                pending.append((entry, depth + 1))
            elif entry.is_file():
                files.append(entry)
    yield from sorted(files, key=lambda path: guard.relative(path).casefold())


def build_readonly_tools(workspace: str | Path | WorkspaceGuard) -> list[BaseTool]:
    """Create the fixed P3 production tool set bound to one workspace policy."""

    guard = workspace if isinstance(workspace, WorkspaceGuard) else WorkspaceGuard(workspace)

    def list_files(directory: str = ".", recursive: bool = False, max_depth: int = 2) -> str:
        """List bounded safe paths without reading file contents."""

        try:
            start = guard.resolve_existing(directory)
            if not start.is_dir():
                return _failure_json(
                    ToolFailureCategory.FILESYSTEM,
                    ToolErrorCode.NOT_A_DIRECTORY,
                    "The requested path is not a directory.",
                )
            paths: list[str] = []
            truncated = False

            def visit(current: Path, depth: int) -> None:
                nonlocal truncated
                try:
                    entries = sorted(current.iterdir(), key=lambda path: path.name.casefold())
                except PermissionError:
                    return
                for entry in entries:
                    if not guard.is_safe_discovered_path(entry):
                        continue
                    if len(paths) >= TOOL_LIMITS.max_list_paths:
                        truncated = True
                        return
                    relative = guard.relative(entry)
                    if entry.is_dir():
                        paths.append(f"{relative}/")
                        if recursive and depth < max_depth:
                            visit(entry, depth + 1)
                            if truncated:
                                return
                    elif entry.is_file():
                        paths.append(relative)

            visit(start, 0)
            return successful_result({"paths": paths, "truncated": truncated}).stable_json()
        except WorkspacePolicyError as exc:
            return _workspace_failure(exc)
        except FileNotFoundError:
            return _failure_json(
                ToolFailureCategory.FILESYSTEM,
                ToolErrorCode.NOT_FOUND,
                "The requested directory was not found.",
            )
        except PermissionError:
            return _failure_json(
                ToolFailureCategory.FILESYSTEM,
                ToolErrorCode.PERMISSION_DENIED,
                "The requested directory cannot be read.",
            )
        except OSError:
            return _failure_json(
                ToolFailureCategory.EXECUTION_FAILURE,
                ToolErrorCode.TOOL_EXECUTION_ERROR,
                "The directory listing failed.",
            )

    def read_file(path: str) -> str:
        """Read bounded UTF-8 text after rechecking the workspace boundary."""

        try:
            file_path = guard.resolve_existing(path)
            if not file_path.is_file():
                return _failure_json(
                    ToolFailureCategory.FILESYSTEM,
                    ToolErrorCode.NOT_A_FILE,
                    "The requested path is not a file.",
                )
            with file_path.open("rb") as handle:
                raw = handle.read(_MAX_READ_BYTES + 1)
            if b"\x00" in raw:
                return _failure_json(
                    ToolFailureCategory.UNSUPPORTED_CONTENT,
                    ToolErrorCode.BINARY_FILE,
                    "Binary files are not supported.",
                )

            byte_truncated = len(raw) > _MAX_READ_BYTES
            data = raw[:_MAX_READ_BYTES]
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                if byte_truncated and exc.start >= len(data) - 4:
                    text = data[: exc.start].decode("utf-8")
                else:
                    return _failure_json(
                        ToolFailureCategory.UNSUPPORTED_CONTENT,
                        ToolErrorCode.INVALID_ENCODING,
                        "The file is not valid UTF-8 text.",
                    )
            content = text[: TOOL_LIMITS.max_read_characters]
            truncated = byte_truncated or len(text) > TOOL_LIMITS.max_read_characters
            return successful_result(
                {
                    "path": guard.relative(file_path),
                    "content": content,
                    "character_count": len(content),
                    "truncated": truncated,
                }
            ).stable_json()
        except WorkspacePolicyError as exc:
            return _workspace_failure(exc)
        except FileNotFoundError:
            return _failure_json(
                ToolFailureCategory.FILESYSTEM,
                ToolErrorCode.NOT_FOUND,
                "The requested file was not found.",
            )
        except PermissionError:
            return _failure_json(
                ToolFailureCategory.FILESYSTEM,
                ToolErrorCode.PERMISSION_DENIED,
                "The requested file cannot be read.",
            )
        except OSError:
            return _failure_json(
                ToolFailureCategory.EXECUTION_FAILURE,
                ToolErrorCode.TOOL_EXECUTION_ERROR,
                "The file read failed.",
            )

    def search_code(
        query: str,
        directory: str = ".",
        file_suffix: str | None = None,
        max_results: int = 20,
    ) -> str:
        """Search literal UTF-8 text within bounded workspace files."""

        try:
            start = guard.resolve_existing(directory)
            if not start.is_dir():
                return _failure_json(
                    ToolFailureCategory.FILESYSTEM,
                    ToolErrorCode.NOT_A_DIRECTORY,
                    "The requested path is not a directory.",
                )
            matches: list[SearchMatch] = []
            for file_path in _iter_search_files(guard, start):
                if file_suffix and file_path.suffix.casefold() != file_suffix.casefold():
                    continue
                try:
                    if file_path.stat().st_size > TOOL_LIMITS.max_search_file_bytes:
                        continue
                    raw = file_path.read_bytes()
                    if b"\x00" in raw:
                        continue
                    text = raw.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if query in line:
                        matches.append(
                            SearchMatch(
                                path=guard.relative(file_path),
                                line_number=line_number,
                                line=line[: TOOL_LIMITS.max_search_line_characters],
                            )
                        )
                        if len(matches) > max_results:
                            return successful_result(
                                {
                                    "matches": [
                                        match.model_dump(mode="json")
                                        for match in matches[:max_results]
                                    ],
                                    "truncated": True,
                                }
                            ).stable_json()
            return successful_result(
                {
                    "matches": [match.model_dump(mode="json") for match in matches],
                    "truncated": False,
                }
            ).stable_json()
        except WorkspacePolicyError as exc:
            return _workspace_failure(exc)
        except FileNotFoundError:
            return _failure_json(
                ToolFailureCategory.FILESYSTEM,
                ToolErrorCode.NOT_FOUND,
                "The requested directory was not found.",
            )
        except PermissionError:
            return _failure_json(
                ToolFailureCategory.FILESYSTEM,
                ToolErrorCode.PERMISSION_DENIED,
                "The requested directory cannot be searched.",
            )
        except OSError:
            return _failure_json(
                ToolFailureCategory.EXECUTION_FAILURE,
                ToolErrorCode.TOOL_EXECUTION_ERROR,
                "The code search failed.",
            )

    return [
        StructuredTool.from_function(
            func=list_files,
            name="list_files",
            description=(
                "List safe relative workspace paths with bounded recursion. "
                "Sensitive and linked paths are excluded; no file content is read."
            ),
            args_schema=ListFilesArgs,
        ),
        StructuredTool.from_function(
            func=read_file,
            name="read_file",
            description=(
                "Read bounded UTF-8 text from one safe relative workspace file. "
                "Sensitive, linked, binary, and outside paths are rejected."
            ),
            args_schema=ReadFileArgs,
        ),
        StructuredTool.from_function(
            func=search_code,
            name="search_code",
            description=(
                "Find literal text in bounded safe workspace files and return relative paths "
                "and line numbers. This tool never writes files."
            ),
            args_schema=SearchCodeArgs,
        ),
    ]
