"""HTTP boundary for starting, recovering, querying, and deleting P6 runs."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from langchain_core.language_models.chat_models import BaseChatModel

from repopilot.api.dependencies import get_model_override, get_settings
from repopilot.approval.contracts import (
    ApprovalDecisionRequest,
    ApprovalServiceError,
)
from repopilot.context.contracts import ContextPolicy
from repopilot.context.manager import ContextManager
from repopilot.infrastructure.config import AppSettings
from repopilot.infrastructure.model_factory import ModelFactoryError, create_chat_model
from repopilot.persistence.contracts import (
    RunCleanupError,
    RunNotFoundError,
    RunNotTerminalError,
)
from repopilot.persistence.lifecycle import PersistenceResources
from repopilot.schemas.agent import (
    AgentRunListResponse,
    AgentRunRequest,
    AgentRunResult,
    AgentRunView,
    DeleteRunResponse,
    TraceEventListResponse,
)
from repopilot.services.agent_service import AgentService
from repopilot.testing.contracts import TestRunner
from repopilot.tools.contracts import ToolErrorCode
from repopilot.tracing.contracts import TraceEventType
from repopilot.tracing.recorder import TraceRecorder

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


@router.get("/agent/runs/{run_id}", response_model=AgentRunView)
async def get_agent_run(
    run_id: str,
    request: Request,
    settings: Annotated[AppSettings, Depends(get_settings)],
    model_override: Annotated[BaseChatModel | None, Depends(get_model_override)],
) -> AgentRunView:
    service = await _get_service(request, settings, model_override)
    try:
        return await service.get_run(run_id)
    except RunNotFoundError as exc:
        raise _run_not_found() from exc
    except ApprovalServiceError as exc:
        raise _approval_http_error(exc) from exc


@router.get("/agent/runs", response_model=AgentRunListResponse)
async def list_agent_runs(
    request: Request,
    settings: Annotated[AppSettings, Depends(get_settings)],
    model_override: Annotated[BaseChatModel | None, Depends(get_model_override)],
    run_status: Annotated[str | None, Query(alias="status", max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> AgentRunListResponse:
    service = await _get_service(request, settings, model_override)
    try:
        return await service.list_runs(status=run_status, limit=limit, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_cursor", "message": "Run cursor is invalid"},
        ) from exc


@router.get("/agent/runs/{run_id}/events", response_model=TraceEventListResponse)
async def list_agent_run_events(
    run_id: str,
    request: Request,
    settings: Annotated[AppSettings, Depends(get_settings)],
    model_override: Annotated[BaseChatModel | None, Depends(get_model_override)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after_event_id: Annotated[int, Query(ge=0)] = 0,
    event_type: TraceEventType | None = None,
) -> TraceEventListResponse:
    service = await _get_service(request, settings, model_override)
    try:
        return await service.list_trace_events(
            run_id,
            limit=limit,
            after=after_event_id,
            event_type=event_type,
        )
    except RunNotFoundError as exc:
        raise _run_not_found() from exc


@router.delete("/agent/runs/{run_id}", response_model=DeleteRunResponse)
async def delete_agent_run(
    run_id: str,
    request: Request,
    settings: Annotated[AppSettings, Depends(get_settings)],
    model_override: Annotated[BaseChatModel | None, Depends(get_model_override)],
) -> DeleteRunResponse:
    service = await _get_service(request, settings, model_override)
    try:
        await service.delete_run(run_id)
    except RunNotFoundError as exc:
        raise _run_not_found() from exc
    except RunNotTerminalError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "run_not_terminal",
                "message": "Only terminal runs can be deleted",
            },
        ) from exc
    except RunCleanupError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "run_cleanup_failed", "message": "Run cleanup did not complete"},
        ) from exc
    return DeleteRunResponse(run_id=run_id)


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
            service = compose_agent_service(
                settings,
                model,
                cast(TestRunner | None, request.app.state.runner_override),
                cast(PersistenceResources, request.app.state.persistence),
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


def compose_agent_service(
    settings: AppSettings,
    model: BaseChatModel,
    runner: TestRunner | None,
    resources: PersistenceResources,
) -> AgentService:
    """Compose exactly one graph service from application-owned resources."""

    secrets = (
        (settings.model_api_key.get_secret_value(),) if settings.model_api_key is not None else ()
    )
    return AgentService(
        settings.workspace_path,
        model,
        pytest_target=settings.pytest_target,
        pytest_timeout_seconds=settings.pytest_timeout_seconds,
        pytest_max_output_characters=settings.pytest_max_output_characters,
        max_repair_attempts=settings.max_repair_attempts,
        run_retention_days=settings.run_retention_days,
        trace_retention_days=settings.trace_retention_days,
        known_secrets=secrets,
        runner=runner,
        checkpointer=resources.checkpointer,
        runtime_store=resources.runtime_store,
        trace_recorder=TraceRecorder(
            resources.runtime_store,
            max_events_per_run=settings.max_trace_events_per_run,
        ),
        context_manager=ContextManager(
            ContextPolicy(
                max_characters=settings.model_context_max_characters,
                recent_blocks=settings.model_context_recent_blocks,
                tool_result_max_characters=(settings.model_context_tool_result_max_characters),
                summary_max_characters=settings.model_context_summary_max_characters,
            )
        ),
    )


def _run_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "run_not_found", "message": "Run was not found"},
    )


def _approval_http_error(exc: ApprovalServiceError) -> HTTPException:
    http_status = (
        status.HTTP_404_NOT_FOUND
        if exc.code is ToolErrorCode.RUN_NOT_FOUND
        else status.HTTP_409_CONFLICT
    )
    return HTTPException(
        status_code=http_status,
        detail={"code": exc.code.value, "message": exc.message},
    )
