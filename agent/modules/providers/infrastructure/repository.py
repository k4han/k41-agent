"""Provider repository with config service support.

Reads provider configuration from the centralized config service.
"""

import os

from agent.modules.providers.domain.provider import ProviderConfig, ProviderType
from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value


DEFAULT_MODEL = "devstral-2512"
DEFAULT_BASE_URL = "https://api.mistral.ai/v1"
# Env var name used internally for LangChain compatibility
_LANGCHAIN_API_KEY_ENV_VAR = "_KAKA_LLM_API_KEY"


class EnvProviderRepository:
    """Resolve provider configs from config service."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderConfig] | None = None

    def _load(self) -> dict[str, ProviderConfig]:
        if self._providers is not None:
            return self._providers

        config = get_config_service()

        base_url = config.get_str("llm.base_url", DEFAULT_BASE_URL)
        default_model = config.get_str("llm.model", DEFAULT_MODEL)

        # Get API key from config
        api_key = config.get_str("llm.api_key", "")

        # Validate API key
        if is_placeholder_value(api_key):
            raise RuntimeError(
                "LLM API key not configured. "
                "Please set 'llm.api_key' in ~/.kaka-agent/config.yaml"
            )

        # Store API key in environment variable for LangChain compatibility
        os.environ[_LANGCHAIN_API_KEY_ENV_VAR] = api_key
        api_key_env_var = _LANGCHAIN_API_KEY_ENV_VAR

        default_provider = ProviderConfig(
            name="default",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            base_url=base_url,
            api_key_env_var=api_key_env_var,
            default_model=default_model,
            enabled=True,
        )

        self._providers = {"default": default_provider}
        return self._providers

    def get_provider(self, name: str) -> ProviderConfig:
        providers = self._load()
        config = providers.get(name)
        if config is None:
            raise KeyError(f"Provider not found: {name!r}")
        return config

    def get_default_provider(self) -> ProviderConfig:
        return self.get_provider("default")

    def list_providers(self) -> list[ProviderConfig]:
        return list(self._load().values())
