"""Small deterministic pytest runner double used only by graph tests."""

from collections.abc import Iterable

from repopilot.testing.contracts import TestOutcome, TestRunResult


def make_test_result(
    outcome: TestOutcome,
    *,
    exit_code: int | None,
    output: str = "",
    timed_out: bool = False,
    output_truncated: bool = False,
) -> TestRunResult:
    return TestRunResult(
        outcome=outcome,
        exit_code=exit_code,
        duration_ms=10,
        timed_out=timed_out,
        output_truncated=output_truncated,
        safe_output_excerpt=output,
        started_at="2026-07-14T00:00:00+00:00",
        finished_at="2026-07-14T00:00:00.010000+00:00",
    )


class ScriptedPytestRunner:
    def __init__(
        self,
        responses: Iterable[TestRunResult],
        *,
        target: str = "tests",
    ) -> None:
        self._responses = list(responses)
        self.target = target
        self.command_display = f"<python> -m pytest -q --tb=short {target}"
        self.run_calls = 0

    async def run(self) -> TestRunResult:
        if self.run_calls >= len(self._responses):
            raise AssertionError("scripted pytest runner exhausted")
        response = self._responses[self.run_calls]
        self.run_calls += 1
        return response
