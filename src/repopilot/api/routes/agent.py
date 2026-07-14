"""Minimal HTTP boundary for one in-memory P2 graph run."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.language_models.chat_models import BaseChatModel

from repopilot.api.dependencies import get_model_override, get_settings
from repopilot.infrastructure.config import AppSettings
from repopilot.infrastructure.model_factory import ModelFactoryError, create_chat_model
from repopilot.schemas.agent import AgentRunRequest, AgentRunResult
from repopilot.services.agent_service import AgentService

router = APIRouter(tags=["agent"])


@router.post("/agent/run", response_model=AgentRunResult)
async def run_agent(
    request: AgentRunRequest,
    settings: Annotated[AppSettings, Depends(get_settings)],
    model_override: Annotated[BaseChatModel | None, Depends(get_model_override)],
) -> AgentRunResult:
    """Run the bounded read-only graph; state is not persisted between requests."""

    model = model_override
    if model is None:
        try:
            model = create_chat_model(settings)
        except ModelFactoryError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "model_not_configured",
                    "message": "A configured model or injected test model is required",
                },
            ) from exc

    try:
        return await AgentService(settings.workspace_path, model).run(
            request.goal,
            max_steps=request.max_steps,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "workspace_unavailable",
                "message": "Configured workspace is unavailable",
            },
        ) from exc
