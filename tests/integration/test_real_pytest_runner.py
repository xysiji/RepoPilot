"""Real subprocess integration tests for fixed pytest execution."""

import asyncio
from pathlib import Path

from repopilot.testing.contracts import TestOutcome, TestRunResult
from repopilot.testing.pytest_runner import PytestRunner
from repopilot.tools.policy import WorkspaceGuard


def _write_test(workspace: Path, source: str) -> None:
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(source, encoding="utf-8")


def _run(
    workspace: Path,
    *,
    timeout: float = 20.0,
    output_limit: int = 20_000,
) -> TestRunResult:
    runner = PytestRunner(
        WorkspaceGuard(workspace),
        timeout_seconds=timeout,
        max_output_characters=output_limit,
    )
    return asyncio.run(runner.run())


def test_real_pytest_pass_and_assertion_failure(tmp_path: Path) -> None:
    passed_workspace = tmp_path / "passed"
    passed_workspace.mkdir()
    _write_test(passed_workspace, "def test_ok():\n    assert 1 + 1 == 2\n")
    failed_workspace = tmp_path / "failed"
    failed_workspace.mkdir()
    _write_test(failed_workspace, "def test_bad():\n    assert 1 + 1 == 3\n")

    passed = _run(passed_workspace)
    failed = _run(failed_workspace)

    assert passed.outcome is TestOutcome.PASSED and passed.exit_code == 0
    assert failed.outcome is TestOutcome.TEST_FAILURES and failed.exit_code == 1


def test_real_pytest_no_tests_and_collection_error_are_not_code_retry(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "tests").mkdir()
    broken = tmp_path / "broken"
    broken.mkdir()
    _write_test(broken, "def test_broken(:\n    pass\n")

    no_tests = _run(empty)
    collection = _run(broken)

    assert no_tests.outcome is TestOutcome.NO_TESTS_COLLECTED
    assert no_tests.exit_code == 5
    assert collection.outcome is TestOutcome.INTERRUPTED
    assert collection.exit_code == 2


def test_real_pytest_import_error_is_infrastructure_outcome(tmp_path: Path) -> None:
    _write_test(tmp_path, "import package_that_does_not_exist\n")

    result = _run(tmp_path)

    assert result.outcome is TestOutcome.INTERRUPTED
    assert result.exit_code == 2


def test_real_pytest_timeout_terminates_and_reaps_process(tmp_path: Path) -> None:
    _write_test(
        tmp_path,
        "import time\n\ndef test_slow():\n    time.sleep(30)\n",
    )

    result = _run(tmp_path, timeout=0.5)

    assert result.outcome is TestOutcome.TIMEOUT
    assert result.timed_out is True
    assert result.duration_ms < 10_000


def test_real_pytest_output_limit_is_hard_and_sanitized(tmp_path: Path) -> None:
    _write_test(
        tmp_path,
        "def test_loud():\n    print('x' * 100000)\n    assert False\n",
    )

    result = _run(tmp_path, output_limit=512)

    assert result.outcome is TestOutcome.OUTPUT_LIMIT_EXCEEDED
    assert result.output_truncated is True
    assert len(result.safe_output_excerpt) <= 512
    assert str(tmp_path) not in result.safe_output_excerpt


def test_real_pytest_reads_stderr_and_replaces_invalid_utf8(tmp_path: Path) -> None:
    _write_test(
        tmp_path,
        "import sys\n\ndef test_output():\n"
        "    sys.stderr.write('stderr-marker\\n')\n"
        "    sys.stdout.buffer.write(b'\\xff')\n"
        "    assert False\n",
    )

    result = _run(tmp_path)

    assert result.outcome is TestOutcome.TEST_FAILURES
    assert "stderr-marker" in result.safe_output_excerpt
    assert "�" in result.safe_output_excerpt
