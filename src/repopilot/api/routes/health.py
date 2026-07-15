"""Application health route."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from langchain_core.language_models.chat_models import BaseChatModel

from repopilot.api.dependencies import get_model_override, get_settings
from repopilot.infrastructure.config import AppSettings
from repopilot.infrastructure.model_factory import is_model_configured
from repopilot.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(
    request: Request,
    settings: Annotated[AppSettings, Depends(get_settings)],
    model_override: Annotated[BaseChatModel | None, Depends(get_model_override)],
) -> HealthResponse:
    """Confirm that the API and validated configuration are available."""

    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        app_env=settings.app_env,
        model_provider=settings.model_provider,
        model_name=settings.model_name,
        model_configured=is_model_configured(settings, model_override),
        persistence_ready=request.app.state.persistence is not None,
    )
