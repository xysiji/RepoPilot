"""Fixed command, environment, and launch-boundary tests for PytestRunner."""

import asyncio
import sys
from pathlib import Path

import pytest

from repopilot.testing.contracts import TestOutcome
from repopilot.testing.pytest_runner import PytestRunner
from repopilot.tools.policy import WorkspaceGuard


def _runner(tmp_path: Path, **kwargs: object) -> PytestRunner:
    (tmp_path / "tests").mkdir(exist_ok=True)
    return PytestRunner(WorkspaceGuard(tmp_path), **kwargs)


def test_command_is_fixed_sequence_using_absolute_current_interpreter(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    assert runner.command == (
        str(Path(sys.executable).resolve()),
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "tests",
    )
    assert runner.command_display == "<python> -m pytest -q --tb=short tests"
    assert not any(argument in {".bat", ".cmd"} for argument in runner.command)


def test_environment_is_allowlisted_and_drops_python_model_and_pytest_controls(
    tmp_path: Path,
) -> None:
    runner = _runner(
        tmp_path,
        source_environment={
            "SystemRoot": "C:/Windows",
            "TEMP": "C:/Temp",
            "OPENAI_API_KEY": "never",
            "REPOPILOT_MODEL_API_KEY": "never",
            "OPENAI_BASE_URL": "https://secret.test",
            "PYTEST_ADDOPTS": "--pdb",
            "PYTHONPATH": "C:/private",
            "UNRELATED": "drop",
        },
    )

    environment = runner.build_environment()

    assert environment["SystemRoot"] == "C:/Windows"
    assert environment["TEMP"] == "C:/Temp"
    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert (
        not {
            "OPENAI_API_KEY",
            "REPOPILOT_MODEL_API_KEY",
            "OPENAI_BASE_URL",
            "PYTEST_ADDOPTS",
            "PYTHONPATH",
            "UNRELATED",
        }
        & environment.keys()
    )


def test_empty_source_environment_does_not_fall_back_to_process_environment(tmp_path: Path) -> None:
    environment = _runner(tmp_path, source_environment={}).build_environment()

    assert set(environment) == {
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONNOUSERSITE",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "NO_COLOR",
    }


@pytest.mark.parametrize("target", ["../tests", "C:\\tests", ".env", "/tests"])
def test_unsafe_target_never_launches_subprocess(
    tmp_path: Path,
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def forbidden(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(ValueError, match="safe workspace-relative"):
        PytestRunner(WorkspaceGuard(tmp_path), target=target)
    assert called is False


def test_shell_wrapper_interpreter_is_rejected(tmp_path: Path) -> None:
    wrapper = tmp_path / "python.cmd"
    wrapper.write_text("echo unsafe", encoding="utf-8")

    with pytest.raises(ValueError, match="shell wrapper"):
        _runner(tmp_path, interpreter=wrapper)


def test_launch_exception_becomes_stable_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_launch(*args: object, **kwargs: object) -> None:
        raise OSError("private launch detail")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_launch)

    result = asyncio.run(_runner(tmp_path).run())

    assert result.outcome is TestOutcome.LAUNCH_ERROR
    assert result.exit_code is None
    assert "private launch detail" not in result.safe_output_excerpt


def test_subprocess_receives_fixed_args_cwd_env_and_no_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class CompletedProcess:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_data(b"ok")
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.returncode = 0

        async def wait(self) -> int:
            return self.returncode

    async def fake_spawn(*args: str, **kwargs: object) -> CompletedProcess:
        observed["args"] = args
        observed["kwargs"] = kwargs
        return CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    runner = _runner(tmp_path, source_environment={"TEMP": "C:/Temp"})

    result = asyncio.run(runner.run())
    kwargs = observed["kwargs"]

    assert result.outcome is TestOutcome.PASSED
    assert observed["args"] == runner.command
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == str(tmp_path.resolve())
    assert kwargs["env"] == runner.build_environment()
    assert "shell" not in kwargs
