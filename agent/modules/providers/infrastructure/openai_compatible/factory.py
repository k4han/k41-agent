"""Factory for creating ChatOpenAI instances.

Covers OpenAI native, Mistral, and any OpenAI-compatible API.
"""

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from agent.modules.providers.domain.model import ModelConfig
from agent.modules.providers.domain.provider import ProviderConfig


class OpenAICompatibleFactory:
    """Create ChatOpenAI client from provider + model config."""

    def create(
        self, provider_config: ProviderConfig, model_config: ModelConfig, api_key: str
    ) -> BaseChatModel:
        return ChatOpenAI(
            model=model_config.model_name,
            base_url=provider_config.base_url,
            api_key=api_key,
            temperature=model_config.temperature,
        )
