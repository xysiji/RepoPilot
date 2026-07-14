"""The sole fixed-command pytest subprocess boundary."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from repopilot.testing.contracts import TestOutcome, TestRunResult, classify_pytest_exit_code
from repopilot.testing.feedback import sanitize_test_output
from repopilot.tools.policy import (
    WorkspaceGuard,
    WorkspacePolicyError,
    workspace_relative_parts,
)

_CHUNK_SIZE = 4096
_PROCESS_STOP_TIMEOUT_SECONDS = 2.0
_SAFE_ENVIRONMENT_KEYS = frozenset({"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP", "TMPDIR"})


class _OutputBudget:
    def __init__(self, limit: int, limit_event: asyncio.Event) -> None:
        self._remaining = limit
        self._limit_event = limit_event
        self.truncated = False

    def take(self, chunk: bytes) -> bytes:
        if self._remaining <= 0:
            self.truncated = True
            self._limit_event.set()
            return b""
        accepted = chunk[: self._remaining]
        self._remaining -= len(accepted)
        if len(accepted) < len(chunk):
            self.truncated = True
            self._limit_event.set()
        return accepted


class _BoundedBytes:
    def __init__(self, budget: _OutputBudget) -> None:
        self._budget = budget
        self._value = bytearray()

    def append(self, chunk: bytes) -> None:
        self._value.extend(self._budget.take(chunk))

    def decode(self) -> str:
        return bytes(self._value).decode("utf-8", errors="replace")


class PytestRunner:
    """Run one internal pytest command with fixed args, cwd, env, time, and output bounds."""

    def __init__(
        self,
        workspace_guard: WorkspaceGuard,
        *,
        target: str = "tests",
        timeout_seconds: float = 60.0,
        max_output_characters: int = 20_000,
        interpreter: str | Path | None = None,
        known_secrets: Iterable[str] = (),
        source_environment: Mapping[str, str] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_output_characters < 1:
            raise ValueError("max_output_characters must be positive")
        self._guard = workspace_guard
        try:
            target_parts = workspace_relative_parts(target)
        except WorkspacePolicyError as exc:
            raise ValueError("target must be a safe workspace-relative path") from exc
        if not target_parts:
            raise ValueError("target must identify a path below the workspace root")
        self._target = "/".join(target_parts)
        self._timeout_seconds = timeout_seconds
        self._max_output_characters = max_output_characters
        self._interpreter = Path(interpreter or sys.executable).resolve(strict=True)
        if self._interpreter.suffix.casefold() in {".bat", ".cmd"}:
            raise ValueError("the pytest interpreter cannot be a shell wrapper")
        self._known_secrets = tuple(secret for secret in known_secrets if secret)
        self._source_environment = dict(
            os.environ if source_environment is None else source_environment
        )

    @property
    def target(self) -> str:
        return self._target

    @property
    def command(self) -> tuple[str, ...]:
        return (
            str(self._interpreter),
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            self._target,
        )

    @property
    def command_display(self) -> str:
        return f"<python> -m pytest -q --tb=short {self._target}"

    def build_environment(self) -> dict[str, str]:
        """Return only explicit OS essentials plus fixed Python/pytest controls."""

        environment = {
            key: value
            for key, value in self._source_environment.items()
            if key.upper() in _SAFE_ENVIRONMENT_KEYS
        }
        environment.update(
            {
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "NO_COLOR": "1",
            }
        )
        return environment

    async def run(self) -> TestRunResult:
        """Start exactly one pytest process; no automatic rerun is performed."""

        started = datetime.now(UTC)
        started_clock = monotonic()
        try:
            target_path = self._guard.resolve_existing(self._target)
            if not (target_path.is_dir() or target_path.is_file()):
                raise FileNotFoundError
        except (FileNotFoundError, OSError, WorkspacePolicyError):
            return self._result(
                outcome=TestOutcome.LAUNCH_ERROR,
                exit_code=None,
                started=started,
                started_clock=started_clock,
                output="The configured pytest target is unavailable.",
            )

        limit_event = asyncio.Event()
        output_budget = _OutputBudget(self._max_output_characters, limit_event)
        stdout_buffer = _BoundedBytes(output_budget)
        stderr_buffer = _BoundedBytes(output_budget)
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=str(self._guard.root),
                env=self.build_environment(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, RuntimeError, ValueError):
            return self._result(
                outcome=TestOutcome.LAUNCH_ERROR,
                exit_code=None,
                started=started,
                started_clock=started_clock,
                output="The fixed pytest process could not be started.",
            )

        assert process.stdout is not None and process.stderr is not None
        stdout_task = asyncio.create_task(_drain(process.stdout, stdout_buffer, limit_event))
        stderr_task = asyncio.create_task(_drain(process.stderr, stderr_buffer, limit_event))
        wait_task = asyncio.create_task(process.wait())
        limit_task = asyncio.create_task(limit_event.wait())
        timed_out = False
        output_exceeded = False
        try:
            done, _ = await asyncio.wait(
                {wait_task, limit_task},
                timeout=self._timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                timed_out = True
                await _stop_process(process, wait_task)
            elif limit_task in done and limit_event.is_set():
                output_exceeded = True
                await _stop_process(process, wait_task)
            else:
                await wait_task
        finally:
            if not limit_task.done():
                limit_task.cancel()
            with suppress(asyncio.CancelledError):
                await limit_task
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

        output = _join_output(stdout_buffer.decode(), stderr_buffer.decode())
        if timed_out:
            outcome = TestOutcome.TIMEOUT
        elif output_exceeded:
            outcome = TestOutcome.OUTPUT_LIMIT_EXCEEDED
        else:
            outcome = classify_pytest_exit_code(process.returncode)
        return self._result(
            outcome=outcome,
            exit_code=process.returncode,
            started=started,
            started_clock=started_clock,
            output=output,
            timed_out=timed_out,
            output_truncated=(output_exceeded or output_budget.truncated),
        )

    def _result(
        self,
        *,
        outcome: TestOutcome,
        exit_code: int | None,
        started: datetime,
        started_clock: float,
        output: str,
        timed_out: bool = False,
        output_truncated: bool = False,
    ) -> TestRunResult:
        finished = datetime.now(UTC)
        safe_output = sanitize_test_output(
            output,
            workspace=self._guard.root,
            interpreter=self._interpreter,
            known_secrets=self._known_secrets,
            max_characters=self._max_output_characters,
        )
        return TestRunResult(
            outcome=outcome,
            exit_code=exit_code,
            duration_ms=max(0, round((monotonic() - started_clock) * 1000)),
            timed_out=timed_out,
            output_truncated=output_truncated,
            safe_output_excerpt=safe_output,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
        )


async def _drain(
    stream: asyncio.StreamReader,
    buffer: _BoundedBytes,
    limit_event: asyncio.Event,
) -> None:
    while not limit_event.is_set():
        chunk = await stream.read(_CHUNK_SIZE)
        if not chunk:
            return
        buffer.append(chunk)


async def _stop_process(
    process: asyncio.subprocess.Process,
    wait_task: asyncio.Task[int],
) -> None:
    if process.returncode is None:
        with suppress(OSError):
            process.terminate()
    try:
        await asyncio.wait_for(asyncio.shield(wait_task), _PROCESS_STOP_TIMEOUT_SECONDS)
    except TimeoutError:
        if process.returncode is None:
            with suppress(OSError):
                process.kill()
        await wait_task


def _join_output(stdout: str, stderr: str) -> str:
    sections: list[str] = []
    if stdout:
        sections.append(f"[stdout]\n{stdout}")
    if stderr:
        sections.append(f"[stderr]\n{stderr}")
    return "\n".join(sections)
