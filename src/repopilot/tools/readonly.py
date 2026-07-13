"""Workspace-bound LangChain tools for the P1 read-only agent."""

from collections.abc import Iterator
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from repopilot.schemas.agent import (
    ListFilesArgs,
    ListFilesResult,
    ReadFileArgs,
    ReadFileResult,
    SearchCodeArgs,
    SearchCodeResult,
    SearchMatch,
)

_EXCLUDED_DIRECTORIES = frozenset({".git", ".venv", "__pycache__"})
_MAX_LIST_PATHS = 200
_MAX_FILE_CHARACTERS = 20_000
_MAX_FILE_BYTES = _MAX_FILE_CHARACTERS * 4 + 4
_MAX_SEARCH_FILE_BYTES = 256 * 1024
_MAX_SEARCH_DEPTH = 8


class WorkspacePathError(PermissionError):
    """Raised when a requested path violates the P1 workspace boundary."""


class WorkspaceGuard:
    """Resolve canonical paths and keep them under one configured workspace root."""

    def __init__(self, workspace_path: str | Path) -> None:
        root = Path(workspace_path).resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError("workspace root must be a directory")
        self.root = root

    def resolve(self, relative_path: str) -> Path:
        requested = Path(relative_path)
        if requested.is_absolute() or requested.drive or requested.root:
            raise WorkspacePathError("absolute paths are not allowed")
        if ".." in requested.parts:
            raise WorkspacePathError("parent path segments are not allowed")
        if any(_is_excluded_name(part) for part in requested.parts):
            raise WorkspacePathError("sensitive paths are not available to tools")

        candidate = (self.root / requested).resolve(strict=True)
        if not self.contains(candidate):
            raise WorkspacePathError("path must stay within the workspace")
        return candidate

    def contains(self, path: Path) -> bool:
        try:
            resolved_relative = path.resolve(strict=True).relative_to(self.root)
        except (OSError, ValueError):
            return False
        return not any(_is_excluded_name(part) for part in resolved_relative.parts)

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def _is_excluded_name(name: str) -> bool:
    lowered = name.casefold()
    return lowered in _EXCLUDED_DIRECTORIES or lowered == ".env" or lowered.startswith(".env.")


def _error_message(error_type: str, message: str) -> tuple[str, str]:
    return error_type, message


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
            if (
                _is_excluded_name(entry.name)
                or entry.is_symlink()
                or entry.is_junction()
                or not guard.contains(entry)
            ):
                continue
            if entry.is_dir() and depth < _MAX_SEARCH_DEPTH:
                pending.append((entry, depth + 1))
            elif entry.is_file():
                files.append(entry)
    yield from sorted(files, key=lambda path: guard.relative(path).casefold())


def build_readonly_tools(workspace_path: str | Path) -> list[BaseTool]:
    """Create the stable P1 tool set bound to a validated workspace."""

    guard = WorkspaceGuard(workspace_path)

    def list_files(directory: str = ".", recursive: bool = False, max_depth: int = 2) -> str:
        """List safe relative workspace paths; this tool never reads file contents."""

        try:
            start = guard.resolve(directory)
            if not start.is_dir():
                raise NotADirectoryError
            paths: list[str] = []
            truncated = False

            def visit(current: Path, depth: int) -> None:
                nonlocal truncated
                for entry in sorted(current.iterdir(), key=lambda path: path.name.casefold()):
                    if (
                        _is_excluded_name(entry.name)
                        or entry.is_symlink()
                        or entry.is_junction()
                        or not guard.contains(entry)
                    ):
                        continue
                    if len(paths) >= _MAX_LIST_PATHS:
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
            return ListFilesResult(
                success=True,
                paths=paths,
                truncated=truncated,
            ).model_dump_json()
        except WorkspacePathError:
            error_type, message = _error_message(
                "permission_denied", "directory is outside the readable workspace"
            )
        except FileNotFoundError:
            error_type, message = _error_message("not_found", "directory was not found")
        except NotADirectoryError:
            error_type, message = _error_message("invalid_path", "path is not a directory")
        except OSError:
            error_type, message = _error_message("filesystem_error", "directory could not be read")
        return ListFilesResult(
            success=False,
            error_type=error_type,
            error_message=message,
        ).model_dump_json()

    def read_file(path: str) -> str:
        """Read bounded UTF-8 text only; sensitive, binary, and outside paths are rejected."""

        try:
            file_path = guard.resolve(path)
            if not file_path.is_file():
                raise IsADirectoryError
            with file_path.open("rb") as handle:
                raw = handle.read(_MAX_FILE_BYTES + 1)
            if b"\x00" in raw:
                raise UnicodeDecodeError("utf-8", raw, 0, 1, "NUL byte")

            byte_truncated = len(raw) > _MAX_FILE_BYTES
            data = raw[:_MAX_FILE_BYTES]
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                if byte_truncated and exc.start >= len(data) - 4:
                    text = data[: exc.start].decode("utf-8")
                else:
                    raise
            content = text[:_MAX_FILE_CHARACTERS]
            truncated = byte_truncated or len(text) > _MAX_FILE_CHARACTERS
            return ReadFileResult(
                success=True,
                path=guard.relative(file_path),
                content=content,
                character_count=len(content),
                truncated=truncated,
            ).model_dump_json()
        except WorkspacePathError:
            error_type, message = _error_message(
                "permission_denied", "file is outside the readable workspace"
            )
        except FileNotFoundError:
            error_type, message = _error_message("not_found", "file was not found")
        except IsADirectoryError:
            error_type, message = _error_message("invalid_path", "path is not a file")
        except UnicodeDecodeError:
            error_type, message = _error_message(
                "binary_file", "file is binary or is not valid UTF-8 text"
            )
        except OSError:
            error_type, message = _error_message("filesystem_error", "file could not be read")
        return ReadFileResult(
            success=False,
            error_type=error_type,
            error_message=message,
        ).model_dump_json()

    def search_code(
        query: str,
        directory: str = ".",
        file_suffix: str | None = None,
        max_results: int = 20,
    ) -> str:
        """Search plain UTF-8 text; this tool does not use embeddings or leave the workspace."""

        if not query.strip():
            return SearchCodeResult(
                success=False,
                error_type="invalid_arguments",
                error_message="query must not be empty",
            ).model_dump_json()
        suffix = file_suffix.casefold() if file_suffix else None
        if suffix and not suffix.startswith("."):
            suffix = f".{suffix}"
        try:
            start = guard.resolve(directory)
            if not start.is_dir():
                raise NotADirectoryError
            matches: list[SearchMatch] = []
            for file_path in _iter_search_files(guard, start):
                if suffix and file_path.suffix.casefold() != suffix:
                    continue
                try:
                    if file_path.stat().st_size > _MAX_SEARCH_FILE_BYTES:
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
                                line=line[:500],
                            )
                        )
                        if len(matches) > max_results:
                            return SearchCodeResult(
                                success=True,
                                matches=matches[:max_results],
                                truncated=True,
                            ).model_dump_json()
            return SearchCodeResult(success=True, matches=matches).model_dump_json()
        except WorkspacePathError:
            error_type, message = _error_message(
                "permission_denied", "directory is outside the searchable workspace"
            )
        except FileNotFoundError:
            error_type, message = _error_message("not_found", "directory was not found")
        except NotADirectoryError:
            error_type, message = _error_message("invalid_path", "path is not a directory")
        except OSError:
            error_type, message = _error_message(
                "filesystem_error", "directory could not be searched"
            )
        return SearchCodeResult(
            success=False,
            error_type=error_type,
            error_message=message,
        ).model_dump_json()

    return [
        StructuredTool.from_function(
            func=list_files,
            name="list_files",
            description=(
                "Call to discover relative files and directories under the configured workspace. "
                "Supports bounded recursion; excludes sensitive folders and cannot read content."
            ),
            args_schema=ListFilesArgs,
        ),
        StructuredTool.from_function(
            func=read_file,
            name="read_file",
            description=(
                "Call to read bounded UTF-8 text from one relative workspace file. "
                "Returns stable JSON and cannot read secrets, binary files, or outside paths."
            ),
            args_schema=ReadFileArgs,
        ),
        StructuredTool.from_function(
            func=search_code,
            name="search_code",
            description=(
                "Call to find literal text in workspace files with optional directory, suffix, "
                "and result limit. Returns relative paths and line numbers; never writes files."
            ),
            args_schema=SearchCodeArgs,
        ),
    ]
