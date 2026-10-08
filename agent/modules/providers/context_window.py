"""Resolve context limits consistently for runtime and model discovery."""

from typing import Any

DEFAULT_CONTEXT_WINDOW = 128_000


def resolve_context_window(
    provider_id: str, model_name: str, profile: dict[str, Any] | None = None,
) -> int:
    from agent.modules.providers.catalog import get_provider_catalog_entry

    catalog = get_provider_catalog_entry(provider_id)
    entry = next((item for item in catalog.models if item.id == model_name), None) if catalog else None
    candidates = [
        entry.context_window if entry else None,
        (profile or {}).get("max_input_tokens"),
        DEFAULT_CONTEXT_WINDOW,
    ]
    return next(value for value in candidates if type(value) is int and value > 0)
