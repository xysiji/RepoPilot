"""Explicit in-memory checkpoint injection for pre-P6 unit-style tests."""

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from repopilot.services.agent_service import AgentService as DurableAgentService


class AgentService(DurableAgentService):
    """Keep legacy tests isolated without a production in-memory fallback."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("checkpointer", InMemorySaver())
        super().__init__(*args, **kwargs)
