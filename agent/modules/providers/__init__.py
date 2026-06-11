"""Public interface for the providers module.

Other modules should import from here, not from internal packages.
"""

from langchain_core.language_models import BaseChatModel

from agent.modules.providers.models import ProviderModelCatalog, ResolvedChatModel
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.modules.providers.service import ProviderService
from agent.modules.providers.resolve_chat_model import (
    get_default_llm_settings,
    resolve_chat_model,
    resolve_chat_model_info,
)

# --- Module-level singleton ---

_provider_service: ProviderService | None = None


def _get_provider_service() -> ProviderService:
    global _provider_service
    if _provider_service is None:
        from agent.modules.providers.anthropic.factory import AnthropicFactory
        from agent.modules.providers.google.factory import GoogleFactory
        from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory
        from agent.modules.providers.provider import ProviderType
        from agent.modules.providers.repository import ConfigProviderRepository

        repo = ConfigProviderRepository()
        service = ProviderService(repository=repo)
        service.register_factory(
            ProviderType.OPENAI_COMPATIBLE, OpenAICompatibleFactory()
        )
        service.register_factory(ProviderType.GOOGLE, GoogleFactory())
        service.register_factory(ProviderType.ANTHROPIC, AnthropicFactory())
        _provider_service = service
    return _provider_service


def reload_provider_service() -> None:
    """Reload provider configs (e.g. after config service reload)."""
    service = _get_provider_service()
    service.reload()
    from agent.modules.providers.resolve_chat_model import _get_cached_model
    _get_cached_model.cache_clear()


def get_chat_model(
    model: str | None = None,
    temperature: float | None = None,
    *,
    provider_name: str | None = None,
) -> BaseChatModel:
    """Get a cached chat model instance.

    Drop-in replacement for the old ``get_llm()``.
    """
    service = _get_provider_service()
    return resolve_chat_model(
        service,
        provider_name=provider_name,
        model=model,
        temperature=temperature,
    )


def get_resolved_chat_model(
    model: str | None = None,
    temperature: float | None = None,
    *,
    provider_name: str | None = None,
) -> ResolvedChatModel:
    """Get a cached chat model plus resolved provider/model metadata."""
    service = _get_provider_service()
    return resolve_chat_model_info(
        service,
        provider_name=provider_name,
        model=model,
        temperature=temperature,
    )


def list_providers() -> list[ProviderConfig]:
    service = _get_provider_service()
    return service.list_providers()


async def list_provider_model_catalog(
    provider_name: str | None = None,
    *,
    include_remote: bool = False,
) -> ProviderModelCatalog:
    service = _get_provider_service()
    return await service.list_model_catalog(
        provider_name,
        include_remote=include_remote,
    )


async def list_provider_model_catalogs(
    *,
    include_remote: bool = False,
) -> list[ProviderModelCatalog]:
    service = _get_provider_service()
    return await service.list_model_catalogs(include_remote=include_remote)


from agent.modules.providers.catalog import (
    ModelCatalogEntry,
    ProviderCatalogEntry,
    ensure_catalog_available,
    get_provider_catalog_entry,
    load_providers_catalog,
    update_catalog_from_url,
)

__all__ = [
    "ProviderService",
    "ProviderType",
    "ResolvedChatModel",
    "get_chat_model",
    "get_default_llm_settings",
    "get_resolved_chat_model",
    "list_provider_model_catalog",
    "list_provider_model_catalogs",
    "list_providers",
    "resolve_chat_model",
    "resolve_chat_model_info",
    "ensure_catalog_available",
    "load_providers_catalog",
    "get_provider_catalog_entry",
    "update_catalog_from_url",
    "ModelCatalogEntry",
    "ProviderCatalogEntry",
]
