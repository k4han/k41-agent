"""Ports (interfaces) for the providers module."""

from typing import Protocol

from langchain_core.language_models import BaseChatModel

from agent.modules.providers.domain.model import ModelConfig
from agent.modules.providers.domain.provider import ProviderConfig


class ChatModelFactory(Protocol):
    """Factory protocol for creating chat model instances."""

    def create(
        self, provider_config: ProviderConfig, model_config: ModelConfig, api_key: str
    ) -> BaseChatModel: ...


class ProviderRepository(Protocol):
    """Repository protocol for accessing provider configurations."""

    def get_provider(self, name: str) -> ProviderConfig: ...

    def list_providers(self) -> list[ProviderConfig]: ...

    def get_default_provider(self) -> ProviderConfig: ...
