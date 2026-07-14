"""Strict dispatch, validation, policy, execution, and proposal pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ValidationError

from repopilot.patching.proposal import (
    PatchPreparationError,
    PatchProposal,
    PatchProposalBuilder,
)
from repopilot.tools.contracts import (
    ResourceLimitExceededError,
    ToolEffect,
    ToolErrorCode,
    ToolExecutionPhase,
    ToolExecutionRecord,
    ToolFailure,
    ToolFailureCategory,
    ToolPolicyAction,
    ToolPolicyDecision,
    ToolResultEnvelope,
    failed_result,
)
from repopilot.tools.policy import WorkspacePolicyError


class SafetyPolicy(Protocol):
    def evaluate(self, *, tool_name: str, validated_args: BaseModel) -> ToolPolicyDecision: ...


@dataclass(frozen=True)
class SafeToolExecution:
    tool_message: ToolMessage | None
    record: ToolExecutionRecord | None
    proposal: PatchProposal | None = None


class SafeToolExecutor:
    """Execute each call at most once after validation and fail-closed policy approval."""

    def __init__(
        self,
        tools: Sequence[BaseTool],
        policy: SafetyPolicy,
        proposal_builder: PatchProposalBuilder | None = None,
    ) -> None:
        tool_list = list(tools)
        self._tools = {tool.name: tool for tool in tool_list}
        if len(self._tools) != len(tool_list):
            raise ValueError("tool names must be unique")
        self._policy = policy
        self._proposal_builder = proposal_builder

    def execute(
        self,
        *,
        model_call: int,
        tool_name: str,
        tool_call_id: str,
        tool_input: Mapping[str, Any],
    ) -> SafeToolExecution:
        safe_input: dict[str, Any] = {"field_count": len(tool_input)}
        tool = self._tools.get(tool_name)
        if tool is None:
            envelope = failed_result(
                phase=ToolExecutionPhase.DISPATCH,
                category=ToolFailureCategory.INVALID_REQUEST,
                code=ToolErrorCode.UNKNOWN_TOOL,
                message="The requested tool is not available.",
            )
            return self._finalize(
                model_call=model_call,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                safe_input=safe_input,
                envelope=envelope,
                effect=ToolEffect.UNKNOWN,
                policy_allowed=None,
            )

        known_fields: frozenset[str] = frozenset()
        try:
            schema = tool.get_input_schema()
            known_fields = frozenset(schema.model_fields)
            safe_input = _safe_input_summary(tool_input, known_fields)
            validated_args = schema.model_validate(dict(tool_input))
        except ValidationError as exc:
            locations = _safe_validation_locations(exc, known_fields)
            envelope = failed_result(
                phase=ToolExecutionPhase.VALIDATION,
                category=ToolFailureCategory.INVALID_REQUEST,
                code=ToolErrorCode.INVALID_ARGUMENTS,
                message="Tool arguments failed validation.",
            )
            return self._finalize(
                model_call=model_call,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                safe_input=safe_input,
                envelope=envelope,
                effect=ToolEffect.UNKNOWN,
                policy_allowed=None,
                summary=f"validation failed: {locations}",
            )
        except Exception:
            envelope = failed_result(
                phase=ToolExecutionPhase.VALIDATION,
                category=ToolFailureCategory.INTERNAL_FAILURE,
                code=ToolErrorCode.INVALID_ARGUMENTS,
                message="Tool arguments could not be validated.",
            )
            return self._finalize(
                model_call=model_call,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                safe_input=safe_input,
                envelope=envelope,
                effect=ToolEffect.UNKNOWN,
                policy_allowed=None,
            )

        try:
            decision = self._policy.evaluate(
                tool_name=tool_name,
                validated_args=validated_args,
            )
        except Exception:
            envelope = failed_result(
                phase=ToolExecutionPhase.POLICY,
                category=ToolFailureCategory.INTERNAL_FAILURE,
                code=ToolErrorCode.UNCLASSIFIED_TOOL_EFFECT,
                message="The tool safety policy could not make a trusted decision.",
            )
            return self._finalize(
                model_call=model_call,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                safe_input=safe_input,
                envelope=envelope,
                effect=ToolEffect.UNKNOWN,
                policy_allowed=False,
            )

        if decision.action is ToolPolicyAction.DENY:
            failure = decision.failure or ToolFailure(
                phase=ToolExecutionPhase.POLICY,
                category=ToolFailureCategory.INTERNAL_FAILURE,
                code=ToolErrorCode.UNCLASSIFIED_TOOL_EFFECT,
                message="The tool safety policy denied this call.",
            )
            envelope = ToolResultEnvelope(success=False, data=None, error=failure)
            return self._finalize(
                model_call=model_call,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                safe_input=safe_input,
                envelope=envelope,
                effect=decision.effect,
                policy_allowed=False,
            )

        if decision.action is ToolPolicyAction.REQUIRE_APPROVAL:
            if (
                decision.effect is not ToolEffect.WRITE
                or tool_name != "propose_patch"
                or self._proposal_builder is None
            ):
                return self.failure(
                    model_call=model_call,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    tool_input=tool_input,
                    phase=ToolExecutionPhase.POLICY,
                    category=ToolFailureCategory.POLICY_DENIED,
                    code=ToolErrorCode.SIDE_EFFECT_NOT_SUPPORTED,
                    message="This approval-required tool is not supported.",
                    effect=decision.effect,
                )
            try:
                proposal = self._proposal_builder.build(
                    tool_call_id=tool_call_id,
                    **validated_args.model_dump(),
                )
            except PatchPreparationError as exc:
                return self._finalize(
                    model_call=model_call,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    safe_input=safe_input,
                    envelope=ToolResultEnvelope(success=False, data=None, error=exc.failure),
                    effect=decision.effect,
                    policy_allowed=False,
                )
            except Exception:
                return self.failure(
                    model_call=model_call,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    tool_input=tool_input,
                    phase=ToolExecutionPhase.PREPARATION,
                    category=ToolFailureCategory.INTERNAL_FAILURE,
                    code=ToolErrorCode.PATCH_TARGET_NOT_SUPPORTED,
                    message="The patch proposal could not be prepared safely.",
                    effect=decision.effect,
                )
            return SafeToolExecution(tool_message=None, record=None, proposal=proposal)

        if (
            decision.action is not ToolPolicyAction.ALLOW
            or decision.effect is not ToolEffect.READ_ONLY
        ):
            return self.failure(
                model_call=model_call,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_input=tool_input,
                phase=ToolExecutionPhase.POLICY,
                category=ToolFailureCategory.INTERNAL_FAILURE,
                code=ToolErrorCode.UNCLASSIFIED_TOOL_EFFECT,
                message="The tool safety policy returned an inconsistent action.",
                effect=decision.effect,
            )

        try:
            raw_result = tool.invoke(validated_args.model_dump())
        except WorkspacePolicyError as exc:
            envelope = failed_result(
                phase=ToolExecutionPhase.EXECUTION,
                category=ToolFailureCategory.POLICY_DENIED,
                code=exc.code,
                message="The path no longer satisfies the workspace safety policy.",
            )
        except FileNotFoundError:
            envelope = _execution_failure(
                ToolFailureCategory.FILESYSTEM,
                ToolErrorCode.NOT_FOUND,
                "The requested path was not found.",
            )
        except (IsADirectoryError, NotADirectoryError) as exc:
            code = (
                ToolErrorCode.NOT_A_FILE
                if isinstance(exc, IsADirectoryError)
                else ToolErrorCode.NOT_A_DIRECTORY
            )
            envelope = _execution_failure(
                ToolFailureCategory.FILESYSTEM,
                code,
                "The requested path has the wrong filesystem type.",
            )
        except PermissionError:
            envelope = _execution_failure(
                ToolFailureCategory.FILESYSTEM,
                ToolErrorCode.PERMISSION_DENIED,
                "The requested path cannot be accessed.",
            )
        except UnicodeDecodeError:
            envelope = _execution_failure(
                ToolFailureCategory.UNSUPPORTED_CONTENT,
                ToolErrorCode.INVALID_ENCODING,
                "The requested content is not valid UTF-8 text.",
            )
        except ResourceLimitExceededError:
            envelope = _execution_failure(
                ToolFailureCategory.RESOURCE_LIMIT,
                ToolErrorCode.RESOURCE_LIMIT_EXCEEDED,
                "The tool exceeded a fixed resource limit.",
            )
        except OSError:
            envelope = _execution_failure(
                ToolFailureCategory.FILESYSTEM,
                ToolErrorCode.TOOL_EXECUTION_ERROR,
                "The filesystem operation failed.",
            )
        except Exception:
            envelope = _execution_failure(
                ToolFailureCategory.EXECUTION_FAILURE,
                ToolErrorCode.TOOL_EXECUTION_ERROR,
                "The tool execution failed.",
            )
        else:
            envelope = _normalize_result(raw_result)

        return self._finalize(
            model_call=model_call,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            safe_input=safe_input,
            envelope=envelope,
            effect=decision.effect,
            policy_allowed=True,
        )

    def failure(
        self,
        *,
        model_call: int,
        tool_name: str,
        tool_call_id: str,
        tool_input: Mapping[str, Any],
        phase: ToolExecutionPhase,
        category: ToolFailureCategory,
        code: ToolErrorCode,
        message: str,
        effect: ToolEffect = ToolEffect.UNKNOWN,
    ) -> SafeToolExecution:
        """Build one stable ToolMessage for a graph-level rejection."""

        envelope = failed_result(phase=phase, category=category, code=code, message=message)
        return self._finalize(
            model_call=model_call,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            safe_input={"field_count": len(tool_input)},
            envelope=envelope,
            effect=effect,
            policy_allowed=False,
        )

    @staticmethod
    def _finalize(
        *,
        model_call: int,
        tool_name: str,
        tool_call_id: str,
        safe_input: dict[str, Any],
        envelope: ToolResultEnvelope,
        effect: ToolEffect,
        policy_allowed: bool | None,
        summary: str | None = None,
    ) -> SafeToolExecution:
        content = envelope.stable_json()
        failure = envelope.error
        output_summary = summary or _summarize(envelope)
        return SafeToolExecution(
            tool_message=ToolMessage(
                content=content,
                tool_call_id=tool_call_id,
                name=tool_name,
                status="success" if envelope.success else "error",
            ),
            record=ToolExecutionRecord(
                step=model_call,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                input=safe_input,
                success=envelope.success,
                output_summary=output_summary,
                error_type=failure.code.value if failure else None,
                error_message=failure.message if failure else None,
                phase=failure.phase if failure else ToolExecutionPhase.NORMALIZATION,
                failure_category=failure.category if failure else None,
                error_code=failure.code if failure else None,
                effect=effect,
                policy_allowed=policy_allowed,
            ),
            proposal=None,
        )


def _safe_input_summary(
    tool_input: Mapping[str, Any],
    known_fields: frozenset[str],
) -> dict[str, Any]:
    supplied_fields = {str(key) for key in tool_input}
    summary: dict[str, Any] = {"fields": sorted(supplied_fields & known_fields)}
    unknown_count = len(supplied_fields - known_fields)
    if unknown_count:
        summary["unknown_field_count"] = unknown_count
    return summary


def _safe_validation_locations(
    error: ValidationError,
    known_fields: frozenset[str],
) -> str:
    issues: list[str] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        raw_location = item.get("loc", ())
        safe_parts = [
            str(part) if str(part) in known_fields or isinstance(part, int) else "<unknown_field>"
            for part in raw_location
        ]
        location = ".".join(safe_parts) or "arguments"
        issue_type = str(item.get("type", "invalid"))
        issues.append(f"{location}:{issue_type}")
    return ", ".join(issues[:5]) or "arguments:invalid"


def _execution_failure(
    category: ToolFailureCategory,
    code: ToolErrorCode,
    message: str,
) -> ToolResultEnvelope:
    return failed_result(
        phase=ToolExecutionPhase.EXECUTION,
        category=category,
        code=code,
        message=message,
    )


def _normalize_result(raw_result: Any) -> ToolResultEnvelope:
    try:
        if isinstance(raw_result, ToolResultEnvelope):
            return raw_result
        if isinstance(raw_result, str):
            return ToolResultEnvelope.model_validate_json(raw_result)
        if isinstance(raw_result, Mapping):
            return ToolResultEnvelope.model_validate(dict(raw_result))
    except (ValidationError, ValueError, TypeError):
        pass
    return failed_result(
        phase=ToolExecutionPhase.NORMALIZATION,
        category=ToolFailureCategory.INTERNAL_FAILURE,
        code=ToolErrorCode.INVALID_TOOL_RESULT,
        message="The tool returned an invalid result structure.",
    )


def _summarize(envelope: ToolResultEnvelope) -> str:
    if not envelope.success:
        return envelope.error.message if envelope.error else "tool failed"
    data = envelope.data or {}
    if isinstance(data.get("paths"), list):
        return f"listed {len(data['paths'])} paths"
    if isinstance(data.get("matches"), list):
        return f"found {len(data['matches'])} matches"
    if isinstance(data.get("path"), str):
        return (
            f"read {data['path']} ({data.get('character_count', 0)} characters, "
            f"truncated={data.get('truncated', False)})"
        )
    return "tool completed"
