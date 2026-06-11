"""Provider service — manages provider configurations and factory registry."""

import inspect

from agent.modules.providers.models import ModelOption, ProviderModelCatalog
from agent.modules.providers.ports import ChatModelFactory, ProviderRepository
from agent.modules.providers.provider import ProviderConfig, ProviderType


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

    async def list_model_catalog(
        self,
        provider_name: str | None = None,
        *,
        include_remote: bool = False,
    ) -> ProviderModelCatalog:
        provider = (
            self.get_provider(provider_name)
            if provider_name
            else self.get_default_provider()
        )
        factory = self.get_factory(provider.provider_type)
        list_models = getattr(factory, "list_models", None)
        can_list_models = callable(list_models)

        remote_models: list[str] = []
        error: str | None = None
        if include_remote and can_list_models:
            try:
                result = list_models(provider, provider.api_key)
                if inspect.isawaitable(result):
                    result = await result
                remote_models = [
                    str(model).strip()
                    for model in result
                    if str(model).strip()
                ]
            except Exception as exc:
                error = str(exc)

        return ProviderModelCatalog(
            provider=provider.name,
            provider_type=str(provider.provider_type),
            default_model=provider.default_model,
            can_list_models=can_list_models,
            models=_merge_model_options(
                provider_name=provider.name,
                remote_models=remote_models,
                configured_models=list(provider.models),
                default_model=provider.default_model,
            ),
            error=error,
        )

    async def list_model_catalogs(
        self,
        *,
        include_remote: bool = False,
    ) -> list[ProviderModelCatalog]:
        catalogs = []
        for provider in self.list_providers():
            catalogs.append(
                await self.list_model_catalog(
                    provider.name,
                    include_remote=include_remote,
                )
            )
        return catalogs

    def reload(self) -> None:
        self._repository.reload()


def _merge_model_options(
    *,
    provider_name: str,
    remote_models: list[str],
    configured_models: list[str],
    default_model: str,
) -> tuple[ModelOption, ...]:
    from agent.modules.providers.catalog import get_provider_catalog_entry

    catalog_entry = get_provider_catalog_entry(provider_name)
    model_entries = {m.id: m for m in catalog_entry.models} if catalog_entry else {}

    options: dict[str, ModelOption] = {}

    def add(model_id: str, source: str) -> None:
        normalized = model_id.strip()
        if normalized and normalized not in options:
            entry = model_entries.get(normalized)
            context_window = entry.context_window if entry else None
            input_types = entry.input_types if entry else None
            output_types = entry.output_types if entry else None
            options[normalized] = ModelOption(
                id=normalized,
                label=normalized,
                source=source,
                context_window=context_window,
                input_types=input_types,
                output_types=output_types,
            )

    for model_id in remote_models:
        add(model_id, "live")
    for model_id in configured_models:
        add(model_id, "config")
    add(default_model, "default")

    return tuple(options.values())
