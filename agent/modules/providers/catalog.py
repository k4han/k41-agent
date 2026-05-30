"""Provider catalog management loaded from api.json."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    id: str
    name: str
    context_window: int | None
    max_output: int | None
    input_types: tuple[str, ...]
    output_types: tuple[str, ...]
    reasoning: bool
    tool_call: bool
    cost_input: float | None
    cost_output: float | None

@dataclass(frozen=True, slots=True)
class ProviderCatalogEntry:
    id: str
    name: str
    provider_type: str  # google, anthropic, openai_compatible
    base_url: str
    env_vars: tuple[str, ...]
    doc_url: str | None
    models: tuple[ModelCatalogEntry, ...]
    default_model: str


# --- In-memory Cache ---
_catalog_cache: dict[str, ProviderCatalogEntry] | None = None


def get_api_json_path() -> str:
    """Get absolute path to api.json in the providers module."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(current_dir, "api.json")


def _resolve_provider_type_and_base_url(
    provider_id: str, npm: str, api_url: str | None
) -> tuple[str, str]:
    npm_lower = npm.lower() if npm else ""
    provider_id_lower = provider_id.lower() if provider_id else ""

    # 1. Determine provider type (driver)
    if provider_id_lower == "google" or "google" in npm_lower:
        provider_type = "google"
    elif provider_id_lower == "anthropic" or "anthropic" in npm_lower:
        provider_type = "anthropic"
    else:
        provider_type = "openai_compatible"

    # 2. Determine base_url
    base_url = ""
    if provider_type == "openai_compatible":
        if api_url:
            base_url = api_url
        else:
            # Standard public API fallbacks
            if provider_id_lower == "openai":
                base_url = "https://api.openai.com/v1"
            elif provider_id_lower == "groq":
                base_url = "https://api.groq.com/openai/v1"
            elif provider_id_lower in ("togetherai", "together"):
                base_url = "https://api.together.xyz/v1"
            elif provider_id_lower == "perplexity":
                base_url = "https://api.perplexity.ai"
            elif provider_id_lower == "mistral":
                base_url = "https://api.mistral.ai/v1"
            elif provider_id_lower == "deepinfra":
                base_url = "https://api.deepinfra.com/v1/openai"
            elif provider_id_lower == "xai":
                base_url = "https://api.x.ai/v1"
            elif provider_id_lower == "cohere":
                base_url = "https://api.cohere.com/v1"
            elif provider_id_lower == "cerebras":
                base_url = "https://api.cerebras.ai/v1"
            elif provider_id_lower == "openrouter":
                base_url = "https://openrouter.ai/api/v1"
            else:
                base_url = ""

    return provider_type, base_url


def _parse_model_entry(model_id: str, info: dict[str, Any]) -> ModelCatalogEntry:
    # Extract limits
    limit = info.get("limit") or {}
    context_window = limit.get("context")
    max_output = limit.get("output")

    # Extract modalities (input/output types)
    modalities = info.get("modalities") or {}
    input_types = tuple(modalities.get("input") or ["text"])
    output_types = tuple(modalities.get("output") or ["text"])

    # Extract costs
    cost = info.get("cost") or {}
    cost_input = cost.get("input")
    cost_output = cost.get("output")

    return ModelCatalogEntry(
        id=model_id,
        name=info.get("name") or model_id,
        context_window=context_window,
        max_output=max_output,
        input_types=input_types,
        output_types=output_types,
        reasoning=bool(info.get("reasoning", False)),
        tool_call=bool(info.get("tool_call", False)),
        cost_input=cost_input,
        cost_output=cost_output,
    )


def load_providers_catalog(force_reload: bool = False) -> dict[str, ProviderCatalogEntry]:
    """Load and parse the providers catalog from api.json with caching."""
    global _catalog_cache
    if _catalog_cache is not None and not force_reload:
        return _catalog_cache

    json_path = get_api_json_path()
    if not os.path.exists(json_path):
        logger.error("Provider api.json not found at: %s", json_path)
        return {}

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception as exc:
        logger.error("Failed to load provider api.json: %s", exc)
        return {}

    catalog: dict[str, ProviderCatalogEntry] = {}
    for provider_id, pinfo in raw_data.items():
        npm = pinfo.get("npm") or ""
        api_url = pinfo.get("api")
        name = pinfo.get("name") or provider_id

        provider_type, base_url = _resolve_provider_type_and_base_url(provider_id, npm, api_url)

        # Parse models
        models_dict = pinfo.get("models") or {}
        models_list = []
        for model_id, minfo in models_dict.items():
            models_list.append(_parse_model_entry(model_id, minfo))

        # Default model is usually the first model in the list, or one with 'flash' / 'mini'
        default_model = ""
        if models_list:
            # Look for a common fast/cheap model to use as default
            for m in models_list:
                m_id_lower = m.id.lower()
                if "flash" in m_id_lower or "mini" in m_id_lower or "3.5-sonnet" in m_id_lower or "gpt-4o-mini" in m_id_lower:
                    default_model = m.id
                    break
            if not default_model:
                default_model = models_list[0].id

        catalog[provider_id] = ProviderCatalogEntry(
            id=provider_id,
            name=name,
            provider_type=provider_type,
            base_url=base_url,
            env_vars=tuple(pinfo.get("env") or []),
            doc_url=pinfo.get("doc"),
            models=tuple(models_list),
            default_model=default_model,
        )

    _catalog_cache = catalog
    return catalog


def get_provider_catalog_entry(provider_id: str) -> ProviderCatalogEntry | None:
    """Look up a provider in the loaded catalog."""
    catalog = load_providers_catalog()
    # Support lookup by normalized key
    normalized = provider_id.strip().lower().replace("-", "_")
    
    # Try direct match
    entry = catalog.get(provider_id)
    if entry:
        return entry
        
    # Try normalized match
    for k, val in catalog.items():
        if k.strip().lower().replace("-", "_") == normalized:
            return val
            
    return None


async def update_catalog_from_url() -> tuple[bool, str]:
    """Fetch the latest api.json from models.dev and save it."""
    import httpx
    
    url = "https://models.dev/api.json"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.json()
    except Exception as exc:
        return False, f"Failed to download catalog: {exc}"

    json_path = get_api_json_path()
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
        
        # Clear cache and reload
        load_providers_catalog(force_reload=True)
        return True, "Catalog updated successfully."
    except Exception as exc:
        return False, f"Failed to write catalog file: {exc}"
