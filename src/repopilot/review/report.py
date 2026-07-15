"""Deterministic, bounded final-report construction."""

from __future__ import annotations

from collections.abc import Mapping

from repopilot.review.contracts import (
    FinalReport,
    FinalReportOutcome,
    ReviewResult,
    ReviewStatus,
)
from repopilot.testing.contracts import AppliedPatchContext, TestOutcome, TestRunResult

_API_TEST_SUMMARY_CHARACTERS = 1000


class FinalReportBuilder:
    """Project graph evidence into a safe terminal report and user summary."""

    def build(self, state: Mapping[str, object]) -> FinalReport:
        latest_test = _latest_test(state.get("latest_test_result"))
        review = _review(state.get("review_result"))
        patches = _patches(state.get("applied_patches"))
        outcome = _select_outcome(state, latest_test, review, bool(patches))
        summary = _summary(outcome)
        modified_files = list(dict.fromkeys(patch.relative_path for patch in patches))
        errors = _errors(state, latest_test, review)
        return FinalReport(
            run_id=str(state.get("run_id", "")),
            outcome=outcome,
            summary=summary,
            modified_files=modified_files,
            repair_attempts=int(state.get("repair_attempts", 0)),
            max_repair_attempts=int(state.get("max_repair_attempts", 1)),
            model_calls=int(state.get("model_calls", 0)),
            approval_count=int(state.get("approval_count", 0)),
            patches_applied=len(patches),
            latest_test_outcome=latest_test.outcome if latest_test else None,
            latest_test_exit_code=latest_test.exit_code if latest_test else None,
            safe_test_summary=(
                latest_test.safe_output_excerpt[:_API_TEST_SUMMARY_CHARACTERS]
                if latest_test
                else ""
            ),
            review_status=review.status if review else None,
            review_findings=list(review.findings) if review else [],
            errors=errors,
            model_final_text=str(state.get("model_final_text") or "")[:4000],
        )


def _select_outcome(
    state: Mapping[str, object],
    latest_test: TestRunResult | None,
    review: ReviewResult | None,
    has_patches: bool,
) -> FinalReportOutcome:
    status = str(state.get("status", "invalid_model_response"))
    if review and review.status is ReviewStatus.PASSED:
        return FinalReportOutcome.REPAIRED
    direct = {
        "repair_attempts_exhausted": FinalReportOutcome.REPAIR_ATTEMPTS_EXHAUSTED,
        "repair_abandoned": FinalReportOutcome.REPAIR_ABANDONED,
        "test_timeout": FinalReportOutcome.TEST_TIMEOUT,
        "test_infrastructure_error": FinalReportOutcome.TEST_INFRASTRUCTURE_ERROR,
        "patch_apply_failed": FinalReportOutcome.PATCH_APPLY_FAILED,
        "approval_rejected": FinalReportOutcome.APPROVAL_REJECTED,
        "model_error": FinalReportOutcome.MODEL_ERROR,
        "max_steps_exceeded": FinalReportOutcome.MAX_STEPS_EXCEEDED,
        "invalid_model_response": FinalReportOutcome.INVALID_MODEL_RESPONSE,
        "context_budget_exceeded": FinalReportOutcome.CONTEXT_BUDGET_EXCEEDED,
        "context_protocol_error": FinalReportOutcome.CONTEXT_PROTOCOL_ERROR,
    }
    if status in direct:
        return direct[status]
    last_patch_error = str(state.get("last_patch_error_code") or "")
    if last_patch_error in {
        "approval_rejected",
        "invalid_approval_decision",
        "proposal_mismatch",
    }:
        return FinalReportOutcome.APPROVAL_REJECTED
    if last_patch_error in {"patch_apply_failed", "patch_verification_failed", "stale_patch"}:
        return FinalReportOutcome.PATCH_APPLY_FAILED
    if latest_test is not None:
        if latest_test.outcome is TestOutcome.TIMEOUT:
            return FinalReportOutcome.TEST_TIMEOUT
        if latest_test.outcome is TestOutcome.TEST_FAILURES:
            return FinalReportOutcome.TESTS_FAILED
        if latest_test.outcome is not TestOutcome.PASSED:
            return FinalReportOutcome.TEST_INFRASTRUCTURE_ERROR
    if not has_patches:
        return FinalReportOutcome.NO_CHANGE
    return FinalReportOutcome.TESTS_FAILED


def _summary(outcome: FinalReportOutcome) -> str:
    return {
        FinalReportOutcome.REPAIRED: "The approved patch passed the fixed pytest verification.",
        FinalReportOutcome.NO_CHANGE: "The run completed without applying a patch.",
        FinalReportOutcome.TESTS_FAILED: "The applied patch did not pass the fixed pytest suite.",
        FinalReportOutcome.REPAIR_ATTEMPTS_EXHAUSTED: (
            "The repair-attempt limit was reached while tests were still failing."
        ),
        FinalReportOutcome.REPAIR_ABANDONED: (
            "The model stopped proposing patches while tests were still failing."
        ),
        FinalReportOutcome.TEST_TIMEOUT: "The fixed pytest verification timed out.",
        FinalReportOutcome.TEST_INFRASTRUCTURE_ERROR: (
            "The fixed pytest verification ended with an infrastructure result."
        ),
        FinalReportOutcome.PATCH_APPLY_FAILED: (
            "The approved patch could not be verified as applied."
        ),
        FinalReportOutcome.APPROVAL_REJECTED: "The patch proposal was rejected and not applied.",
        FinalReportOutcome.MODEL_ERROR: (
            "The model invocation failed before a verified repair completed."
        ),
        FinalReportOutcome.MAX_STEPS_EXCEEDED: (
            "The model-call limit was reached before a verified repair completed."
        ),
        FinalReportOutcome.INVALID_MODEL_RESPONSE: (
            "The model returned an invalid response before a verified repair completed."
        ),
        FinalReportOutcome.CONTEXT_BUDGET_EXCEEDED: (
            "Required protocol-safe context exceeded the configured model budget."
        ),
        FinalReportOutcome.CONTEXT_PROTOCOL_ERROR: (
            "Persisted messages did not form a valid tool-call protocol sequence."
        ),
    }[outcome]


def _errors(
    state: Mapping[str, object],
    latest_test: TestRunResult | None,
    review: ReviewResult | None,
) -> list[str]:
    errors: list[str] = []
    error = state.get("error")
    if error is not None:
        code = getattr(error, "code", None)
        if code is None and isinstance(error, Mapping):
            code = error.get("code")
        if code:
            errors.append(str(code))
    if latest_test and latest_test.outcome is not TestOutcome.PASSED:
        errors.append(latest_test.outcome.value)
    if review and review.status is not ReviewStatus.PASSED:
        errors.extend(review.findings)
    return list(dict.fromkeys(errors))


def _latest_test(raw: object) -> TestRunResult | None:
    try:
        return TestRunResult.model_validate(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _review(raw: object) -> ReviewResult | None:
    try:
        return ReviewResult.model_validate(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _patches(raw: object) -> list[AppliedPatchContext]:
    if not isinstance(raw, list):
        return []
    result: list[AppliedPatchContext] = []
    for value in raw:
        try:
            result.append(AppliedPatchContext.model_validate(value))
        except (TypeError, ValueError):
            continue
    return result
