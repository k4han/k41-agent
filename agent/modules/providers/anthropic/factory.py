"""Factory for creating ChatAnthropic instances."""

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel

from agent.modules.providers.models import ModelConfig
from agent.modules.providers.provider import ProviderConfig


class AnthropicFactory:
    """Create ChatAnthropic client from provider + model config."""

    def create(
        self, provider_config: ProviderConfig, model_config: ModelConfig, api_key: str
    ) -> BaseChatModel:
        _ = provider_config  # Anthropic client does not use provider base_url.
        return ChatAnthropic(
            model_name=model_config.model_name,
            api_key=api_key,
            temperature=model_config.temperature,
        )

    async def list_models(
        self,
        provider_config: ProviderConfig,
        api_key: str,
    ) -> list[str]:
        try:
            import anthropic
        except ImportError:
            return []

        _ = provider_config
        client = anthropic.AsyncAnthropic(api_key=api_key)
        model_names: list[str] = []
        try:
            async for model in client.models.list(limit=100):
                model_id = str(getattr(model, "id", "")).strip()
                if model_id:
                    model_names.append(model_id)
        finally:
            await client.close()
        return sorted(set(model_names))
