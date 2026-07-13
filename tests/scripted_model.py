"""Minimal scripted BaseChatModel used only by P1 offline tests."""

from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedToolCallingModel(BaseChatModel):
    """Return scripted messages, record inputs, and accept tool binding without network I/O."""

    responses: list[BaseMessage]
    received_messages: list[list[BaseMessage]] = Field(default_factory=list, exclude=True)
    bound_tool_names: list[str] = Field(default_factory=list, exclude=True)
    raise_on_invoke: bool = False

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-calling-test-model"

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        self.bound_tool_names = [str(getattr(tool, "name", "unknown")) for tool in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.received_messages.append(list(messages))
        if self.raise_on_invoke:
            raise RuntimeError("scripted model failure")
        if not self.responses:
            raise RuntimeError("scripted responses exhausted")
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])
