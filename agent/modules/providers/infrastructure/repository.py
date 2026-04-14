"""Provider repository with config service support.

Reads provider configuration from the centralized config service.
"""

from agent.modules.providers.domain.provider import ProviderConfig, ProviderType
from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value


DEFAULT_MODEL = "devstral-2512"
DEFAULT_GOOGLE_MODEL = "gemini-2.0-flash"
DEFAULT_BASE_URL = "https://api.mistral.ai/v1"
DEFAULT_PROVIDER = "openai_compatible"

_PROVIDER_ALIASES: dict[str, ProviderType] = {
    "openai_compatible": ProviderType.OPENAI_COMPATIBLE,
    "google": ProviderType.GOOGLE,
}

_DEFAULT_MODEL_BY_PROVIDER: dict[ProviderType, str] = {
    ProviderType.OPENAI_COMPATIBLE: DEFAULT_MODEL,
    ProviderType.GOOGLE: DEFAULT_GOOGLE_MODEL,
}


def _normalize_provider_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _resolve_provider_type(value: str) -> ProviderType:
    normalized = _normalize_provider_name(value)
    provider_type = _PROVIDER_ALIASES.get(normalized)
    if provider_type is None:
        supported = ", ".join(sorted(_PROVIDER_ALIASES))
        raise ValueError(
            f"Unsupported llm.provider value: {value!r}. Supported values: {supported}."
        )
    return provider_type


class ConfigProviderRepository:
    """Resolve provider configs from config service."""

    def _load(self) -> dict[str, ProviderConfig]:
        config = get_config_service()

        provider_type = _resolve_provider_type(
            config.get_str("llm.provider", DEFAULT_PROVIDER)
        )

        base_url = config.get_str("llm.base_url", DEFAULT_BASE_URL)
        default_model = config.get_str(
            "llm.model",
            _DEFAULT_MODEL_BY_PROVIDER.get(provider_type, DEFAULT_MODEL),
        )

        # Get API key from config
        api_key = config.get_str("llm.api_key", "").strip()

        # Validate API key
        if is_placeholder_value(api_key):
            raise RuntimeError(
                "LLM API key not configured. "
                "Please set 'llm.api_key' in ~/.kaka-agent/config.yaml"
            )

        default_provider = ProviderConfig(
            name="default",
            provider_type=provider_type,
            base_url=base_url if provider_type == ProviderType.OPENAI_COMPATIBLE else "",
            api_key=api_key,
            default_model=default_model,
            enabled=True,
        )

        return {"default": default_provider}

    def get_provider(self, name: str) -> ProviderConfig:
        providers = self._load()
        default_provider = providers["default"]

        normalized_name = _normalize_provider_name(name)
        if normalized_name == "default":
            return default_provider

        requested_provider_type = _PROVIDER_ALIASES.get(normalized_name)
        if requested_provider_type == default_provider.provider_type:
            return default_provider

        raise KeyError(f"Provider not found: {name!r}")

    def get_default_provider(self) -> ProviderConfig:
        return self.get_provider("default")

    def list_providers(self) -> list[ProviderConfig]:
        return list(self._load().values())
