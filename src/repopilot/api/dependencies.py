"""FastAPI request-scoped access to application-owned dependencies."""

from typing import cast

from fastapi import Request
from langchain_core.language_models.chat_models import BaseChatModel

from repopilot.infrastructure.config import AppSettings


def get_settings(request: Request) -> AppSettings:
    """Read the settings instance installed by the application factory."""

    return cast(AppSettings, request.app.state.settings)


def get_model_override(request: Request) -> BaseChatModel | None:
    """Read an optional test-supplied model without constructing a provider client."""

    return cast(BaseChatModel | None, request.app.state.model_override)
