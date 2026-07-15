"""FastAPI application composition root."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langchain_core.language_models.chat_models import BaseChatModel

from repopilot.api.routes.agent import compose_agent_service
from repopilot.api.routes.agent import router as agent_router
from repopilot.api.routes.health import router as health_router
from repopilot.infrastructure.config import AppSettings, load_settings
from repopilot.persistence.contracts import PersistenceError
from repopilot.persistence.lifecycle import open_persistence
from repopilot.testing.contracts import TestRunner
from repopilot.tracing.contracts import TraceRecordingError


def create_app(
    settings: AppSettings | None = None,
    model_override: BaseChatModel | None = None,
    runner_override: TestRunner | None = None,
) -> FastAPI:
    """Build an independently configurable application without model network calls."""

    resolved_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        resources = await open_persistence(resolved_settings)
        application.state.persistence = resources
        if model_override is not None:
            application.state.agent_service = compose_agent_service(
                resolved_settings,
                model_override,
                runner_override,
                resources,
            )
        try:
            yield
        finally:
            application.state.agent_service = None
            await resources.close()

    app = FastAPI(title=resolved_settings.app_name, lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.model_override = model_override
    app.state.runner_override = runner_override
    app.state.agent_service = None
    app.state.agent_service_lock = asyncio.Lock()
    app.state.persistence = None

    @app.exception_handler(PersistenceError)
    async def persistence_error_handler(request: Request, exc: PersistenceError) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "persistence_unavailable",
                    "message": "Persistent run storage is unavailable",
                }
            },
        )

    @app.exception_handler(TraceRecordingError)
    async def trace_error_handler(request: Request, exc: TraceRecordingError) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "trace_write_failed",
                    "message": "Structured trace storage is unavailable",
                }
            },
        )

    @app.exception_handler(aiosqlite.Error)
    async def sqlite_error_handler(request: Request, exc: aiosqlite.Error) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "persistence_unavailable",
                    "message": "Persistent run storage is unavailable",
                }
            },
        )

    app.include_router(health_router)
    app.include_router(agent_router)
    return app
