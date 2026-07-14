"""Custom model and tool nodes for the P2 graph."""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from repopilot.agent.state import AgentState
from repopilot.schemas.agent import AgentRunError, ToolExecutionRecord

_MAX_FINAL_ANSWER_CHARACTERS = 4000


class ModelNode:
    """Invoke one already-bound model and emit only a partial state update."""

    def __init__(self, bound_model: Runnable[Any, BaseMessage]) -> None:
        self._bound_model = bound_model

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        model_calls = state["model_calls"] + 1
        try:
            response = await self._bound_model.ainvoke(list(state["messages"]))
        except Exception as exc:
            return _terminal_update(
                "model_error",
                f"Model invocation failed: {type(exc).__name__}",
                model_calls=model_calls,
            )

        if not isinstance(response, AIMessage):
            return _terminal_update(
                "invalid_model_response",
                "Model must return an AIMessage",
                model_calls=model_calls,
            )

        if response.tool_calls:
            for tool_call in response.tool_calls:
                call_id = tool_call.get("id")
                if not isinstance(call_id, str) or not call_id:
                    update = _terminal_update(
                        "invalid_model_response",
                        "Every tool call must have a non-empty ID",
                        model_calls=model_calls,
                    )
                    update["messages"] = [response]
                    return update
            return {
                "messages": [response],
                "model_calls": model_calls,
                "status": "running",
                "error": None,
            }

        final_answer = response.text.strip()
        if not final_answer:
            update = _terminal_update(
                "invalid_model_response",
                "Model returned empty final content",
                model_calls=model_calls,
            )
            update["messages"] = [response]
            return update
        if len(final_answer) > _MAX_FINAL_ANSWER_CHARACTERS:
            final_answer = final_answer[:_MAX_FINAL_ANSWER_CHARACTERS] + "\n[truncated]"
        return {
            "messages": [response],
            "model_calls": model_calls,
            "status": "success",
            "final_answer": final_answer,
            "error": None,
        }


class ToolNode:
    """Execute every tool call from the latest AI message in model order."""

    def __init__(self, tools: Sequence[BaseTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def __call__(self, state: AgentState) -> dict[str, Any]:
        latest = state["messages"][-1] if state["messages"] else None
        if not isinstance(latest, AIMessage) or not latest.tool_calls:
            return _terminal_update(
                "invalid_model_response",
                "Tool node requires an AIMessage with tool calls",
                model_calls=state["model_calls"],
            )

        messages: list[ToolMessage] = []
        executions: list[ToolExecutionRecord] = []
        for tool_call in latest.tool_calls:
            tool_name = tool_call["name"]
            tool_call_id = tool_call["id"]
            tool_input = dict(tool_call["args"])
            tool_message, execution = self._execute_tool(
                model_call=state["model_calls"],
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_input=tool_input,
                tool=self._tools.get(tool_name),
            )
            messages.append(tool_message)
            executions.append(execution)

        update: dict[str, Any] = {
            "messages": messages,
            "tool_executions": executions,
        }
        if state["model_calls"] >= state["max_steps"]:
            update.update(
                _terminal_update(
                    "max_steps_exceeded",
                    f"Maximum model steps exceeded: {state['max_steps']}",
                    model_calls=state["model_calls"],
                )
            )
        else:
            update.update({"status": "running", "error": None})
        return update

    def _execute_tool(
        self,
        *,
        model_call: int,
        tool_name: str,
        tool_call_id: str,
        tool_input: dict[str, Any],
        tool: BaseTool | None,
    ) -> tuple[ToolMessage, ToolExecutionRecord]:
        if tool is None:
            content = _failure_json("unknown_tool", "Requested tool is not available")
        else:
            try:
                raw_output = tool.invoke(tool_input)
                content = (
                    raw_output
                    if isinstance(raw_output, str)
                    else json.dumps(raw_output, ensure_ascii=False, sort_keys=True)
                )
            except ValidationError:
                content = _failure_json(
                    "invalid_tool_arguments",
                    "Tool arguments failed validation",
                )
            except Exception:
                content = _failure_json("tool_execution_error", "Tool execution failed")

        success, summary, error_type, error_message = _summarize_tool_output(content)
        return (
            ToolMessage(
                content=content,
                tool_call_id=tool_call_id,
                name=tool_name,
                status="success" if success else "error",
            ),
            ToolExecutionRecord(
                step=model_call,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                input=tool_input,
                success=success,
                output_summary=summary,
                error_type=error_type,
                error_message=error_message,
            ),
        )


def _terminal_update(code: str, message: str, *, model_calls: int) -> dict[str, Any]:
    return {
        "model_calls": model_calls,
        "status": code,
        "final_answer": None,
        "error": AgentRunError(code=code, message=message),
    }


def _failure_json(error_type: str, message: str) -> str:
    return json.dumps(
        {"success": False, "error_type": error_type, "error_message": message},
        ensure_ascii=False,
        sort_keys=True,
    )


def _summarize_tool_output(content: str) -> tuple[bool, str, str | None, str | None]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return True, content[:240], None, None
    if not isinstance(payload, Mapping):
        return True, str(payload)[:240], None, None

    success = payload.get("success") is True
    error_type = payload.get("error_type") if isinstance(payload.get("error_type"), str) else None
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
