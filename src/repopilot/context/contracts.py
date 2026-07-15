"""Contracts for P6 model-context governance."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field


class ContextProtocolError(ValueError):
    """Raised when persisted messages violate the tool-call protocol."""


class ContextBudgetExceededError(ValueError):
    """Raised when protocol-safe required context cannot fit the configured budget."""


class ContextPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_characters: int = Field(ge=2_000)
    recent_blocks: int = Field(ge=1)
    tool_result_max_characters: int = Field(ge=128)
    summary_max_characters: int = Field(ge=128)


class ContextStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    original_message_count: int
    model_message_count: int
    original_characters: int
    model_characters: int
    compacted_block_count: int
    dropped_block_count: int
    tool_results_compacted: int


class ContextWindowResult:
    """Transient messages plus safe aggregate statistics only."""

    def __init__(self, messages: Sequence[BaseMessage], stats: ContextStats) -> None:
        self.messages = list(messages)
        self.stats = stats
