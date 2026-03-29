"""Environment-based provider repository.

Reads provider configuration from environment variables,
maintaining backward compatibility with LLM_API_KEY, LLM_MODEL, etc.
"""

import os

from agent.modules.providers.domain.provider import ProviderConfig, ProviderType


DEFAULT_MODEL = "devstral-2512"
DEFAULT_BASE_URL = "https://api.mistral.ai/v1"


class EnvProviderRepository:
    """Resolve provider configs from environment variables."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderConfig] | None = None

    def _load(self) -> dict[str, ProviderConfig]:
        if self._providers is not None:
            return self._providers

        base_url = os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL
        default_model = os.getenv("LLM_MODEL") or DEFAULT_MODEL

        # Determine API key env var name
        api_key_env_var = "LLM_API_KEY"
        if not os.getenv("LLM_API_KEY") and os.getenv("OPENAI_API_KEY"):
            api_key_env_var = "OPENAI_API_KEY"

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
