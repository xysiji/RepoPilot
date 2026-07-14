"""Exit-code mapping and bounded test-contract tests."""

import pytest

from repopilot.testing.contracts import TestOutcome, classify_pytest_exit_code


@pytest.mark.parametrize(
    ("exit_code", "outcome"),
    [
        (0, TestOutcome.PASSED),
        (1, TestOutcome.TEST_FAILURES),
        (2, TestOutcome.INTERRUPTED),
        (3, TestOutcome.PYTEST_INTERNAL_ERROR),
        (4, TestOutcome.PYTEST_USAGE_ERROR),
        (5, TestOutcome.NO_TESTS_COLLECTED),
        (6, TestOutcome.WARNINGS_EXCEEDED),
        (7, TestOutcome.UNKNOWN_EXIT_CODE),
        (-9, TestOutcome.UNKNOWN_EXIT_CODE),
    ],
)
def test_every_pytest_exit_code_has_a_deterministic_outcome(
    exit_code: int,
    outcome: TestOutcome,
) -> None:
    assert classify_pytest_exit_code(exit_code) is outcome


def test_only_exit_zero_passes_and_only_exit_one_is_recoverable() -> None:
    outcomes = {code: classify_pytest_exit_code(code) for code in range(7)}

    assert [code for code, outcome in outcomes.items() if outcome is TestOutcome.PASSED] == [0]
    assert [code for code, outcome in outcomes.items() if outcome is TestOutcome.TEST_FAILURES] == [
        1
    ]
