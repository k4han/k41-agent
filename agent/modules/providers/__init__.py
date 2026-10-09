"""Public interface for the providers module.

Other modules should import from here, not from internal packages.
"""

from langchain_core.language_models import BaseChatModel

from agent.modules.providers.anthropic.factory import AnthropicFactory
from agent.modules.providers.google.factory import GoogleFactory
from agent.modules.providers.models import ProviderModelCatalog, ResolvedChatModel
from agent.modules.providers.models import get_reasoning_effort_kwargs
from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.modules.providers.profiles import EFFORT_PATTERN, parse_model_profiles
from agent.modules.providers.context_window import DEFAULT_CONTEXT_WINDOW
from agent.modules.providers.context_budget import model_input_budget
from agent.modules.providers.repository import ConfigProviderRepository
from agent.modules.providers.service import ProviderService
from agent.modules.providers.internal_loader import load_internal_providers
from agent.modules.providers.resolve_chat_model import (
    get_default_llm_settings,
    resolve_chat_model,
    resolve_chat_model_info,
    resolve_chat_model_selection,
)


def _get_provider_service(container=None) -> ProviderService:
    """Return container-scoped provider service."""
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).provider_service


def reload_provider_service(container=None) -> None:
    """Reload provider configs (e.g. after config service reload)."""
    from agent.bootstrap.container import require_active_container

    active_container = require_active_container(container)
    service = active_container.provider_service
    service.reload()
    from agent.modules.providers.internal_loader import load_internal_providers

    load_internal_providers(service, container=active_container)
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


def get_chat_model_selection(*, provider_name: str | None = None, model: str | None = None) -> tuple[str, str]:
    """Return the configured provider/model identity without API clients or fallback."""
    provider, model_name = resolve_chat_model_selection(
        _get_provider_service(), provider_name=provider_name, model=model,
    )
    return provider.name, model_name


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
    normalize_provider_key,
    register_provider_catalog_entry,
    update_catalog_from_url,
)

from agent.modules.providers.verification import (
    ProviderVerificationResult,
    resolve_suggested_default_model,
    verify_provider_connection,
)

__all__ = [
    "EFFORT_PATTERN",
    "DEFAULT_CONTEXT_WINDOW",
    "model_input_budget",
    "parse_model_profiles",
    "AnthropicFactory",
    "ConfigProviderRepository",
    "GoogleFactory",
    "OpenAICompatibleFactory",
    "ProviderService",
    "ProviderType",
    "ProviderVerificationResult",
    "ResolvedChatModel",
    "get_chat_model",
    "get_chat_model_selection",
    "get_default_llm_settings",
    "get_resolved_chat_model",
    "list_provider_model_catalog",
    "list_provider_model_catalogs",
    "list_providers",
    "resolve_chat_model",
    "resolve_chat_model_info",
    "get_reasoning_effort_kwargs",
    "resolve_suggested_default_model",
    "verify_provider_connection",
    "ensure_catalog_available",
    "load_providers_catalog",
    "load_internal_providers",
    "get_provider_catalog_entry",
    "register_provider_catalog_entry",
    "update_catalog_from_url",
    "ModelCatalogEntry",
    "ProviderCatalogEntry",
]
