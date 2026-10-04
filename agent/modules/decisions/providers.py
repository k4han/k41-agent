"""Decision client factory registry."""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from agent.modules.decisions.cloudflare import CloudflareClefClient
from agent.modules.decisions.ports import DecisionClient

if TYPE_CHECKING:
    from agent.modules.decisions.settings import DecisionSettings

ClientFactory = Callable[["DecisionSettings"], DecisionClient | None]


def _cloudflare_client(settings: DecisionSettings) -> DecisionClient | None:
    if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
        return None
    return CloudflareClefClient(
        account_id=settings.cloudflare_account_id, api_token=settings.cloudflare_api_token,
        default_model=settings.model, base_url=settings.cloudflare_base_url,
        timeout=settings.timeout, max_retries=settings.max_retries,
    )


CLIENT_FACTORIES: dict[str, ClientFactory] = {"cloudflare": _cloudflare_client}


def register_decision_provider(kind: str, definition: dict[str, Any], factory: ClientFactory) -> None:
    """Register both the settings catalog entry and its client factory."""
    from agent.shared.config.decision_providers import DECISION_PROVIDERS
    DECISION_PROVIDERS[kind] = definition
    CLIENT_FACTORIES[kind] = factory


def create_decision_client(settings: DecisionSettings) -> DecisionClient | None:
    factory = CLIENT_FACTORIES.get(settings.provider_type)
    if factory is None:
        raise ValueError(f"Unsupported decision provider type: {settings.provider_type}.")
    return factory(settings)
