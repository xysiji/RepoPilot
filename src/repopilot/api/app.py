"""FastAPI application composition root."""

import asyncio

from fastapi import FastAPI
from langchain_core.language_models.chat_models import BaseChatModel

from repopilot.api.routes.agent import router as agent_router
from repopilot.api.routes.health import router as health_router
from repopilot.infrastructure.config import AppSettings, load_settings
from repopilot.testing.contracts import TestRunner


def create_app(
    settings: AppSettings | None = None,
    model_override: BaseChatModel | None = None,
    runner_override: TestRunner | None = None,
) -> FastAPI:
    """Build an independently configurable application without model network calls."""

    resolved_settings = settings or load_settings()
    app = FastAPI(title=resolved_settings.app_name)
    app.state.settings = resolved_settings
    app.state.model_override = model_override
    app.state.runner_override = runner_override
    app.state.agent_service = None
    app.state.agent_service_lock = asyncio.Lock()
    app.include_router(health_router)
    app.include_router(agent_router)
    return app
