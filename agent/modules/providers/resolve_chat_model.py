"""Resolve a chat model instance from provider config + model overrides."""

from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from agent.modules.providers.service import ProviderService
from agent.modules.providers.models import ModelConfig, ResolvedChatModel
from agent.shared.config import get_config_service


DEFAULT_TEMPERATURE = 0.0


def _parse_temperature(value: str | float | int | None, default: float) -> float:
    if value in (None, ""):
        return default

    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Invalid LLM temperature configuration. "
            "Set llm.providers.<provider>.temperature in config.yaml to a numeric value."
        ) from exc


def get_default_llm_settings() -> tuple[str, str]:
    """Parse llm.default_model into (provider_name, model_name)."""
    from agent.shared.config import get_config_service
    config = get_config_service()
    default_model_setting = config.get_str("llm.default_model", "").strip()
    if "/" in default_model_setting:
        provider, model = default_model_setting.split("/", 1)
        return provider.strip(), model.strip()

    # Fallback for old configs where llm.default_provider is still present
    old_default_provider = config.get_str("llm.default_provider", "").strip()
    if old_default_provider and default_model_setting:
        return old_default_provider, default_model_setting

    return default_model_setting, ""


def resolve_chat_model(
    provider_service: ProviderService,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    api_key: str | None = None,
) -> BaseChatModel:
    return resolve_chat_model_info(
        provider_service,
        provider_name=provider_name,
        model=model,
        temperature=temperature,
        api_key=api_key,
    ).model


def resolve_chat_model_info(
    provider_service: ProviderService,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    api_key: str | None = None,
) -> ResolvedChatModel:
    """Resolve and cache a chat model instance.

    Resolution order:
    1. provider_name → explicit provider lookup
    2. Falls back to default provider from repository
    3. model/temperature override per-call or from config
    """
    config = get_config_service()

    default_provider_name, default_model_name = get_default_llm_settings()

    target_provider = provider_name
    if not target_provider or target_provider.strip().lower() == "default":
        target_provider = default_provider_name

    if target_provider:
        provider_config = provider_service.get_provider(target_provider)
    else:
        provider_config = provider_service.get_default_provider()

    target_model = model
    if not target_model or target_model.strip().lower() == "default":
        target_model = default_model_name

    resolved_model = (target_model or provider_config.default_model).strip()
    provider_temperature_key = f"llm.providers.{provider_config.name}.temperature"
    configured_temperature = config.get(provider_temperature_key)
    resolved_temperature = _parse_temperature(
        temperature if temperature is not None else configured_temperature,
        DEFAULT_TEMPERATURE,
    )

    resolved_api_key = api_key or provider_config.api_key
    if not resolved_api_key:
        raise RuntimeError(
            "API key not configured. "
            f"Set 'llm.providers.{provider_config.name}.api_key' "
            "in ~/.kaka-agent/config.yaml before starting the app."
        )

    if not resolved_model:
        raise RuntimeError(
            "Model not configured. "
            f"Set 'llm.providers.{provider_config.name}.default_model' "
            "in ~/.kaka-agent/config.yaml before starting the app."
        )

    model_config = ModelConfig(
        model_name=resolved_model,
        temperature=resolved_temperature,
    )

    factory = provider_service.get_factory(provider_config.provider_type)

    chat_model = _get_cached_model(
        factory=factory,
        provider_type=str(provider_config.provider_type),
        base_url=provider_config.base_url,
        api_key=resolved_api_key,
        model_name=model_config.model_name,
        temperature=model_config.temperature,
    )
    return ResolvedChatModel(
        model=chat_model,
        provider_name=provider_config.name,
        provider_type=str(provider_config.provider_type),
        model_name=model_config.model_name,
    )


@lru_cache(maxsize=128)
def _get_cached_model(
    *,
    factory: object,
    provider_type: str,
    base_url: str,
    api_key: str,
    model_name: str,
    temperature: float,
) -> BaseChatModel:
    """Cache model instances by their full config fingerprint."""
    from agent.modules.providers.models import ModelConfig
    from agent.modules.providers.provider import ProviderConfig, ProviderType

    provider_config = ProviderConfig(
        name=provider_type,
        provider_type=ProviderType(provider_type),
        base_url=base_url,
        api_key=api_key,
        default_model=model_name,
        models=(),
    )
    model_config = ModelConfig(model_name=model_name, temperature=temperature)

    # factory is a ChatModelFactory protocol
    return factory.create(provider_config, model_config, api_key)  # type: ignore[arg-type,union-attr]
