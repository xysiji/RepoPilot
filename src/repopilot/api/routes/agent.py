"""HTTP boundary for starting and resuming one resumable P4 graph run."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from langchain_core.language_models.chat_models import BaseChatModel

from repopilot.api.dependencies import get_model_override, get_settings
from repopilot.approval.contracts import (
    ApprovalDecisionRequest,
    ApprovalServiceError,
)
from repopilot.infrastructure.config import AppSettings
from repopilot.infrastructure.model_factory import ModelFactoryError, create_chat_model
from repopilot.schemas.agent import AgentRunRequest, AgentRunResult
from repopilot.services.agent_service import AgentService
from repopilot.testing.contracts import TestRunner
from repopilot.tools.contracts import ToolErrorCode

router = APIRouter(tags=["agent"])


@router.post("/agent/run", response_model=AgentRunResult)
async def run_agent(
    request_body: AgentRunRequest,
    response: Response,
    request: Request,
    settings: Annotated[AppSettings, Depends(get_settings)],
    model_override: Annotated[BaseChatModel | None, Depends(get_model_override)],
) -> AgentRunResult:
    """Start a server-identified run and return 202 only when it is interrupted."""

    service = await _get_service(request, settings, model_override)
    if (
        request_body.max_repair_attempts is not None
        and request_body.max_repair_attempts > settings.max_repair_attempts
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "repair_limit_exceeded",
                "message": "Requested repair attempts exceed the configured system limit",
            },
        )
    result = await service.start_run(
        request_body.goal,
        max_steps=request_body.max_steps,
        max_repair_attempts=request_body.max_repair_attempts,
    )
    if result.status == "awaiting_approval":
        response.status_code = status.HTTP_202_ACCEPTED
    return result


@router.post("/agent/runs/{run_id}/decision", response_model=AgentRunResult)
async def decide_agent_run(
    run_id: str,
    decision: ApprovalDecisionRequest,
    response: Response,
    request: Request,
    settings: Annotated[AppSettings, Depends(get_settings)],
    model_override: Annotated[BaseChatModel | None, Depends(get_model_override)],
) -> AgentRunResult:
    """Resume exactly one pending proposal using its server-owned thread ID."""

    service = await _get_service(request, settings, model_override)
    try:
        result = await service.resume_run(run_id, decision)
    except ApprovalServiceError as exc:
        http_status = (
            status.HTTP_404_NOT_FOUND
            if exc.code is ToolErrorCode.RUN_NOT_FOUND
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": exc.code.value, "message": exc.message},
        ) from exc
    if result.status == "awaiting_approval":
        response.status_code = status.HTTP_202_ACCEPTED
    return result


async def _get_service(
    request: Request,
    settings: AppSettings,
    model_override: BaseChatModel | None,
) -> AgentService:
    existing = cast(AgentService | None, request.app.state.agent_service)
    if existing is not None:
        return existing
    async with request.app.state.agent_service_lock:
        existing = cast(AgentService | None, request.app.state.agent_service)
        if existing is not None:
            return existing
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
            secrets = (
                (settings.model_api_key.get_secret_value(),)
                if settings.model_api_key is not None
                else ()
            )
            service = AgentService(
                settings.workspace_path,
                model,
                pytest_target=settings.pytest_target,
                pytest_timeout_seconds=settings.pytest_timeout_seconds,
                pytest_max_output_characters=settings.pytest_max_output_characters,
                max_repair_attempts=settings.max_repair_attempts,
                known_secrets=secrets,
                runner=cast(TestRunner | None, request.app.state.runner_override),
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "workspace_unavailable",
                    "message": "Configured workspace is unavailable",
                },
            ) from exc
        request.app.state.agent_service = service
        return service
