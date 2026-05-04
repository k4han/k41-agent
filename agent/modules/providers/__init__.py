"""Public interface for the providers module.

Other modules should import from here, not from internal packages.
"""

from langchain_core.language_models import BaseChatModel

from agent.modules.providers.service import ProviderService
from agent.modules.providers.resolve_chat_model import resolve_chat_model

# --- Module-level singleton ---

_provider_service: ProviderService | None = None


def _get_provider_service() -> ProviderService:
    global _provider_service
    if _provider_service is None:
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


__all__ = [
    "ProviderService",
    "get_chat_model",
    "resolve_chat_model",
]
