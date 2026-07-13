"""Explicit P1 Tool Calling loop implemented with LangChain message primitives."""

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from repopilot.schemas.agent import (
    AgentRunError,
    AgentRunResult,
    ToolExecutionRecord,
)

_MAX_FINAL_ANSWER_CHARACTERS = 4000


class ToolCallingLoop:
    """Drive model -> tools -> ToolMessage until a deterministic stop condition."""

    def run(
        self,
        goal: str,
        *,
        model: BaseChatModel,
        tools: Sequence[BaseTool],
        max_steps: int,
    ) -> AgentRunResult:
        if not goal.strip():
            raise ValueError("goal must not be empty")
        if not 1 <= max_steps <= 10:
            raise ValueError("max_steps must be between 1 and 10")

        tool_map = {tool.name: tool for tool in tools}
        if len(tool_map) != len(tools):
            raise ValueError("tool names must be unique")

        messages: list[BaseMessage] = [HumanMessage(content=goal)]
        executions: list[ToolExecutionRecord] = []
        try:
            bound_model = model.bind_tools(list(tools))
        except Exception as exc:
            return self._terminal_error(
                "model_error",
                f"Model tool binding failed: {type(exc).__name__}",
                steps=0,
                messages=messages,
                executions=executions,
            )

        for step in range(1, max_steps + 1):
            try:
                response = bound_model.invoke(messages)
            except Exception as exc:
                return self._terminal_error(
                    "model_error",
                    f"Model invocation failed: {type(exc).__name__}",
                    steps=step,
                    messages=messages,
                    executions=executions,
                )
            if not isinstance(response, AIMessage):
                return self._terminal_error(
                    "invalid_model_response",
                    "Model must return an AIMessage",
                    steps=step,
                    messages=messages,
                    executions=executions,
                )

            messages.append(response)
            if not response.tool_calls:
                final_answer = response.text.strip()
                if not final_answer:
                    return self._terminal_error(
                        "invalid_model_response",
                        "Model returned empty final content",
                        steps=step,
                        messages=messages,
                        executions=executions,
                    )
                if len(final_answer) > _MAX_FINAL_ANSWER_CHARACTERS:
                    final_answer = final_answer[:_MAX_FINAL_ANSWER_CHARACTERS] + "\n[truncated]"
                return AgentRunResult(
                    status="success",
                    final_answer=final_answer,
                    steps=step,
                    tool_executions=executions,
                    message_count=len(messages),
                )

            for tool_call in response.tool_calls:
                call_id = tool_call.get("id")
                if not isinstance(call_id, str) or not call_id:
                    return self._terminal_error(
                        "invalid_model_response",
                        "Every tool call must have a non-empty ID",
                        steps=step,
                        messages=messages,
                        executions=executions,
                    )
                tool_message, execution = self._execute_tool(
                    step=step,
                    tool_name=tool_call["name"],
                    tool_call_id=call_id,
                    tool_input=dict(tool_call["args"]),
                    tool=tool_map.get(tool_call["name"]),
                )
                messages.append(tool_message)
                executions.append(execution)

        return self._terminal_error(
            "max_steps_exceeded",
            f"Maximum model steps exceeded: {max_steps}",
            steps=max_steps,
            messages=messages,
            executions=executions,
        )

    def _execute_tool(
        self,
        *,
        step: int,
        tool_name: str,
        tool_call_id: str,
        tool_input: dict[str, Any],
        tool: BaseTool | None,
    ) -> tuple[ToolMessage, ToolExecutionRecord]:
        if tool is None:
            content = self._failure_json("unknown_tool", f"Unknown tool: {tool_name}")
        else:
            try:
                raw_output = tool.invoke(tool_input)
                content = raw_output if isinstance(raw_output, str) else json.dumps(raw_output)
            except ValidationError as exc:
                content = self._failure_json(
                    "invalid_arguments",
                    f"Invalid tool arguments: {str(exc)[:1000]}",
                )
            except Exception as exc:
                content = self._failure_json(
                    "tool_exception",
                    f"Tool raised {type(exc).__name__}",
                )

        success, summary, error_type, error_message = self._summarize_tool_output(content)
        tool_message = ToolMessage(
            content=content,
            tool_call_id=tool_call_id,
            name=tool_name,
            status="success" if success else "error",
        )
        execution = ToolExecutionRecord(
            step=step,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            input=tool_input,
            success=success,
            output_summary=summary,
            error_type=error_type,
            error_message=error_message,
        )
        return tool_message, execution

    @staticmethod
    def _failure_json(error_type: str, message: str) -> str:
        return json.dumps(
            {"success": False, "error_type": error_type, "error_message": message},
            ensure_ascii=False,
        )

    @staticmethod
    def _summarize_tool_output(content: str) -> tuple[bool, str, str | None, str | None]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return True, content[:240], None, None
        if not isinstance(payload, dict):
            return True, str(payload)[:240], None, None

        success = payload.get("success") is True
        error_type = (
            payload.get("error_type") if isinstance(payload.get("error_type"), str) else None
        )
        error_message = (
            payload.get("error_message") if isinstance(payload.get("error_message"), str) else None
        )
        if not success:
            return False, (error_message or "tool failed")[:240], error_type, error_message
        if "paths" in payload:
            summary = f"listed {len(payload.get('paths', []))} paths"
        elif "matches" in payload:
            summary = f"found {len(payload.get('matches', []))} matches"
        elif "path" in payload:
            summary = (
                f"read {payload.get('path')} ({payload.get('character_count', 0)} characters, "
                f"truncated={payload.get('truncated', False)})"
            )
        else:
            summary = "tool completed"
        return True, summary, None, None

    @staticmethod
    def _terminal_error(
        code: str,
        message: str,
        *,
        steps: int,
        messages: Sequence[BaseMessage],
        executions: list[ToolExecutionRecord],
    ) -> AgentRunResult:
        return AgentRunResult(
            status=code,
            steps=steps,
            tool_executions=executions,
            message_count=len(messages),
            error=AgentRunError(code=code, message=message),
        )
