"""Application configuration loaded at an explicit composition boundary."""

from pathlib import Path
from typing import Any

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from repopilot.tools.policy import WorkspacePolicyError, workspace_relative_parts


class AppSettings(BaseSettings):
    """Validated RepoPilot settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_prefix="REPOPILOT_",
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    app_name: str = Field(default="RepoPilot", min_length=1)
    app_env: str = Field(default="development", min_length=1)
    log_level: str = "INFO"
    workspace_path: Path = Path("demo_workspace")
    model_provider: str = "openai"
    model_name: str = Field(default="gpt-4.1-mini", min_length=1)
    model_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    model_base_url: AnyHttpUrl | None = Field(default=None, exclude=True, repr=False)
    model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    model_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    pytest_target: str = "tests"
    pytest_timeout_seconds: float = Field(default=60.0, ge=0.1, le=600.0)
    pytest_max_output_characters: int = Field(default=20_000, ge=256, le=200_000)
    max_repair_attempts: int = Field(default=3, ge=1, le=5)
    data_directory: Path = Path(".repopilot")
    checkpoint_database_name: str = "checkpoints.sqlite3"
    runtime_database_name: str = "runtime.sqlite3"
    run_retention_days: int = Field(default=30, ge=1, le=3650)
    trace_retention_days: int = Field(default=30, ge=1, le=3650)
    max_trace_events_per_run: int = Field(default=500, ge=10, le=10_000)
    model_context_max_characters: int = Field(default=60_000, ge=2_000, le=500_000)
    model_context_recent_blocks: int = Field(default=8, ge=1, le=100)
    model_context_tool_result_max_characters: int = Field(default=4_000, ge=128, le=100_000)
    model_context_summary_max_characters: int = Field(default=2_000, ge=128, le=20_000)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            msg = f"log_level must be one of: {', '.join(sorted(allowed))}"
            raise ValueError(msg)
        return normalized

    @field_validator("model_provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("model_provider must not be empty")
        return normalized

    @field_validator("model_api_key", mode="before")
    @classmethod
    def empty_api_key_is_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("model_base_url", mode="before")
    @classmethod
    def empty_base_url_is_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("pytest_target")
    @classmethod
    def validate_pytest_target(cls, value: str) -> str:
        try:
            parts = workspace_relative_parts(value.strip())
        except WorkspacePolicyError as exc:
            raise ValueError("pytest_target must be a safe workspace-relative path") from exc
        if not parts:
            raise ValueError("pytest_target must identify a path below the workspace root")
        return "/".join(parts)

    @field_validator("checkpoint_database_name", "runtime_database_name")
    @classmethod
    def validate_database_name(cls, value: str) -> str:
        name = value.strip()
        if not name or Path(name).name != name or not name.endswith(".sqlite3"):
            raise ValueError("database name must be a local .sqlite3 file name")
        return name

    def safe_dump(self) -> dict[str, Any]:
        """Export non-sensitive settings for diagnostics."""

        return self.model_dump(
            mode="json",
            exclude={"model_api_key", "model_base_url"},
        )


def load_settings(env_file: str | Path | None = ".env") -> AppSettings:
    """Load settings when the application factory is called, never at import time."""

    return AppSettings(_env_file=env_file)
