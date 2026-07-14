"""Custom model and P3 safe-tool nodes for the explicit graph."""

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import Runnable

from repopilot.agent.state import AgentState
from repopilot.schemas.agent import AgentRunError
from repopilot.tools.contracts import ToolExecutionRecord
from repopilot.tools.executor import SafeToolExecutor

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
    """Delegate every model-ordered call to the P3 safety executor."""

    def __init__(self, executor: SafeToolExecutor) -> None:
        self._executor = executor

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
            result = self._executor.execute(
                model_call=state["model_calls"],
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_input=tool_input,
            )
            messages.append(result.tool_message)
            executions.append(result.record)

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


def _terminal_update(code: str, message: str, *, model_calls: int) -> dict[str, Any]:
    return {
        "model_calls": model_calls,
        "status": code,
        "final_answer": None,
        "error": AgentRunError(code=code, message=message),
    }
