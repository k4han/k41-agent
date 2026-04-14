"""Provider service — manages provider configurations and factory registry."""

from agent.modules.providers.domain.ports import ChatModelFactory, ProviderRepository
from agent.modules.providers.domain.provider import ProviderConfig, ProviderType


class ProviderService:
    """Central service for managing provider configs and their factories."""

    def __init__(self, repository: ProviderRepository) -> None:
        self._repository = repository
        self._factories: dict[ProviderType, ChatModelFactory] = {}

    # --- Factory registration ---

    def register_factory(
        self, provider_type: ProviderType, factory: ChatModelFactory
    ) -> None:
        self._factories[provider_type] = factory

    def get_factory(self, provider_type: ProviderType) -> ChatModelFactory:
        factory = self._factories.get(provider_type)
        if factory is None:
            raise RuntimeError(
                f"No factory registered for provider type: {provider_type}"
            )
        return factory

    # --- Provider queries ---

    def get_provider(self, name: str) -> ProviderConfig:
        return self._repository.get_provider(name)

    def get_default_provider(self) -> ProviderConfig:
        return self._repository.get_default_provider()

    def list_providers(self) -> list[ProviderConfig]:
        return self._repository.list_providers()

    def reload(self) -> None:
        self._repository.reload()
