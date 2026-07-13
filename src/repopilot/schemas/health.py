"""Health endpoint schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Public, non-sensitive application readiness summary."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    app_name: str
    app_env: str
    model_provider: str
    model_name: str
    model_configured: bool
