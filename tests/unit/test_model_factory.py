"""Tests for provider validation, construction, and model replacement."""

from unittest.mock import patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from repopilot.infrastructure.config import AppSettings
from repopilot.infrastructure.model_factory import (
    MissingModelConfigurationError,
    UnknownModelProviderError,
    create_chat_model,
)


def test_fake_model_can_be_injected() -> None:
    fake_model = FakeListChatModel(responses=["ready"])
    settings = AppSettings(model_provider="not-a-real-provider", _env_file=None)

    created = create_chat_model(settings, model_override=fake_model)

    assert created is fake_model


def test_unknown_provider_has_clear_error() -> None:
    settings = AppSettings(model_provider="unknown", _env_file=None)

    with pytest.raises(UnknownModelProviderError, match="Unsupported model_provider: 'unknown'"):
        create_chat_model(settings)


def test_missing_provider_api_key_has_clear_error() -> None:
    settings = AppSettings(model_provider="openai", model_api_key=None, _env_file=None)

    with pytest.raises(MissingModelConfigurationError, match="model_api_key is required"):
        create_chat_model(settings)


def test_factory_constructs_base_chat_model_without_network_request() -> None:
    settings = AppSettings(model_api_key="test-only-key", _env_file=None)

    with patch(
        "httpx.Client.send",
        side_effect=AssertionError("network request attempted"),
    ) as send:
        model = create_chat_model(settings)

    assert isinstance(model, BaseChatModel)
    send.assert_not_called()


def test_factory_passes_validated_settings_to_provider() -> None:
    settings = AppSettings(
        model_api_key="test-only-key",
        model_name="test-model",
        model_base_url="https://models.example.test/v1",
        model_temperature=0.3,
        model_timeout_seconds=12,
        _env_file=None,
    )

    with patch("repopilot.infrastructure.model_factory.ChatOpenAI") as provider:
        create_chat_model(settings)

    provider.assert_called_once_with(
        model="test-model",
        api_key=settings.model_api_key,
        base_url="https://models.example.test/v1",
        temperature=0.3,
        timeout=12.0,
    )
