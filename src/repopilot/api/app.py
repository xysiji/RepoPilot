"""FastAPI application composition root."""

from fastapi import FastAPI
from langchain_core.language_models.chat_models import BaseChatModel

from repopilot.api.routes.agent import router as agent_router
from repopilot.api.routes.health import router as health_router
from repopilot.infrastructure.config import AppSettings, load_settings


def create_app(
    settings: AppSettings | None = None,
    model_override: BaseChatModel | None = None,
) -> FastAPI:
    """Build an independently configurable application without model network calls."""

    resolved_settings = settings or load_settings()
    app = FastAPI(title=resolved_settings.app_name)
    app.state.settings = resolved_settings
    app.state.model_override = model_override
    app.include_router(health_router)
    app.include_router(agent_router)
    return app
