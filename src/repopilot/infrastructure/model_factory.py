"""LangChain chat model construction and test-time replacement."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from repopilot.infrastructure.config import AppSettings


class ModelFactoryError(ValueError):
    """Base exception for invalid model factory configuration."""


class UnknownModelProviderError(ModelFactoryError):
    """Raised when a configured provider is unsupported."""


class MissingModelConfigurationError(ModelFactoryError):
    """Raised when a provider's required setting is absent."""


def create_chat_model(
    settings: AppSettings,
    model_override: BaseChatModel | None = None,
) -> BaseChatModel:
    """Return an injected model or construct the configured provider without invoking it."""

    if model_override is not None:
        return model_override

    if settings.model_provider != "openai":
        raise UnknownModelProviderError(
            f"Unsupported model_provider: {settings.model_provider!r}. Supported providers: openai"
        )

    if settings.model_api_key is None:
        raise MissingModelConfigurationError(
            "model_api_key is required when model_provider='openai'"
        )

    return ChatOpenAI(
        model=settings.model_name,
        api_key=settings.model_api_key,
        base_url=str(settings.model_base_url) if settings.model_base_url else None,
        temperature=settings.model_temperature,
        timeout=settings.model_timeout_seconds,
    )


def is_model_configured(
    settings: AppSettings,
    model_override: BaseChatModel | None = None,
) -> bool:
    """Report whether a usable model source is present without creating or calling a model."""

    if model_override is not None:
        return True
    return settings.model_provider == "openai" and settings.model_api_key is not None
