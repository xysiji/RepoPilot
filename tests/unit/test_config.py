"""Tests for explicit, validated, and safe configuration loading."""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from repopilot.infrastructure.config import AppSettings, load_settings


@pytest.fixture(autouse=True)
def clear_repopilot_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every settings test independent from the developer machine environment."""

    for name in tuple(os.environ):
        if name.startswith("REPOPILOT_"):
            monkeypatch.delenv(name)


def test_non_sensitive_defaults_are_available() -> None:
    settings = AppSettings(_env_file=None)

    assert settings.app_name == "RepoPilot"
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.model_api_key is None
    assert settings.pytest_target == "tests"
    assert settings.max_repair_attempts == 3
    assert settings.data_directory == Path(".repopilot")
    assert settings.checkpoint_database_name == "checkpoints.sqlite3"
    assert settings.runtime_database_name == "runtime.sqlite3"
    assert settings.model_context_max_characters == 60_000


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOPILOT_APP_NAME", "RepoPilot Test")
    monkeypatch.setenv("REPOPILOT_MODEL_TEMPERATURE", "0.25")

    settings = AppSettings(_env_file=None)

    assert settings.app_name == "RepoPilot Test"
    assert settings.model_temperature == 0.25


def test_direct_values_override_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOPILOT_APP_ENV", "from-environment")

    settings = AppSettings(app_env="from-constructor", _env_file=None)

    assert settings.app_env == "from-constructor"


def test_explicit_env_file_loading_is_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_path = tmp_path / "p0.env"
    env_path.write_text("REPOPILOT_APP_ENV=from-file\n", encoding="utf-8")
    monkeypatch.delenv("REPOPILOT_APP_ENV", raising=False)

    settings = load_settings(env_path)

    assert settings.app_env == "from-file"


def test_safe_dump_and_json_do_not_expose_sensitive_values() -> None:
    secret = "p0-secret-value"
    settings = AppSettings(
        model_api_key=secret,
        model_base_url="https://example.com/v1?token=also-secret",
        _env_file=None,
    )

    settings_repr = repr(settings)
    default_export = json.dumps(settings.model_dump(mode="json"))
    safe_export = json.dumps(settings.safe_dump())

    assert secret not in settings_repr
    assert "also-secret" not in settings_repr
    assert secret not in default_export
    assert "also-secret" not in default_export
    assert secret not in safe_export
    assert "also-secret" not in safe_export
    assert "model_api_key" not in settings.safe_dump()
    assert "model_base_url" not in settings.safe_dump()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_temperature", -0.01),
        ("model_temperature", 2.01),
        ("model_timeout_seconds", 0),
        ("model_timeout_seconds", 301),
    ],
)
def test_invalid_model_limits_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**{field: value}, _env_file=None)


@pytest.mark.parametrize("target", ["", ".", "../tests", "C:\\tests", ".env", "/tests"])
def test_pytest_target_must_be_a_safe_relative_workspace_path(target: str) -> None:
    with pytest.raises(ValidationError):
        AppSettings(pytest_target=target, _env_file=None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pytest_timeout_seconds", 0),
        ("pytest_timeout_seconds", 601),
        ("pytest_max_output_characters", 255),
        ("pytest_max_output_characters", 200_001),
        ("max_repair_attempts", 0),
        ("max_repair_attempts", 6),
    ],
)
def test_invalid_test_runner_limits_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**{field: value}, _env_file=None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("checkpoint_database_name", "../checkpoint.sqlite3"),
        ("runtime_database_name", "runtime.db"),
        ("run_retention_days", 0),
        ("trace_retention_days", 0),
        ("max_trace_events_per_run", 9),
        ("model_context_max_characters", 1999),
        ("model_context_recent_blocks", 0),
    ],
)
def test_invalid_p6_storage_and_context_limits_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**{field: value}, _env_file=None)
