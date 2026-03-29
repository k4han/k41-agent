"""Resolve a chat model instance from provider config + model overrides."""

import os
from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from agent.modules.providers.application.provider_service import ProviderService
from agent.modules.providers.domain.model import ModelConfig


DEFAULT_TEMPERATURE = 0.0


def _parse_temperature(value: str | float | int | None, default: float) -> float:
    if value in (None, ""):
        return default

    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Invalid LLM temperature configuration. "
            "Set LLM_TEMPERATURE to a numeric value."
        ) from exc


def resolve_chat_model(
    provider_service: ProviderService,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> BaseChatModel:
    """Resolve and cache a chat model instance.

    Resolution order:
    1. provider_name → explicit provider lookup
    2. Falls back to default provider from repository
    3. model/temperature override per-call or from env
    """
    if provider_name:
        provider_config = provider_service.get_provider(provider_name)
    else:
        provider_config = provider_service.get_default_provider()

    resolved_model = model or os.getenv("LLM_MODEL") or provider_config.default_model
    resolved_temperature = _parse_temperature(
        temperature if temperature is not None else os.getenv("LLM_TEMPERATURE"),
        DEFAULT_TEMPERATURE,
    )

    api_key = os.getenv(provider_config.api_key_env_var, "")
    if not api_key:
        raise RuntimeError(
            f"API key not configured. "
            f"Set {provider_config.api_key_env_var} before starting the app."
        )

    model_config = ModelConfig(
        model_name=resolved_model,
        temperature=resolved_temperature,
    )

    factory = provider_service.get_factory(provider_config.provider_type)

    return _get_cached_model(
        factory=factory,
        provider_type=str(provider_config.provider_type),
        base_url=provider_config.base_url,
        api_key=api_key,
        api_key_env_var=provider_config.api_key_env_var,
        model_name=model_config.model_name,
        temperature=model_config.temperature,
    )


@lru_cache(maxsize=None)
def _get_cached_model(
    *,
    factory: object,
    provider_type: str,
    base_url: str,
    api_key: str,
    api_key_env_var: str,
    model_name: str,
    temperature: float,
) -> BaseChatModel:
    """Cache model instances by their full config fingerprint."""
    from agent.modules.providers.domain.model import ModelConfig
    from agent.modules.providers.domain.provider import ProviderConfig, ProviderType

    provider_config = ProviderConfig(
        name=provider_type,
        provider_type=ProviderType(provider_type),
        base_url=base_url,
        api_key_env_var=api_key_env_var,
        default_model=model_name,
    )
    model_config = ModelConfig(model_name=model_name, temperature=temperature)

    # factory is a ChatModelFactory protocol
    return factory.create(provider_config, model_config)  # type: ignore[union-attr]
