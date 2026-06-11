from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import HTTPException

from agent.modules.providers import (
    list_provider_model_catalog,
    list_provider_model_catalogs,
)
from agent.shared.config import (
    ConfigService,
    get_config_service,
    get_setting_metadata,
    parse_provider_key,
)
from agent.shared.infrastructure.config_file import coerce_bool

logger = logging.getLogger(__name__)

PROVIDER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
PROVIDER_TYPE_OPTIONS: list[dict[str, Any]] = [
    {
        "value": "google",
        "label": "Google",
        "description": "Google Gemini provider",
        "requires_base_url": False,
    },
    {
        "value": "anthropic",
        "label": "Anthropic",
        "description": "Anthropic Claude provider",
        "requires_base_url": False,
    },
    {
        "value": "openai_compatible",
        "label": "OpenAI-compatible",
        "description": "Custom endpoint that implements the OpenAI chat API",
        "requires_base_url": True,
    },
]
SUPPORTED_PROVIDER_TYPES = {option["value"] for option in PROVIDER_TYPE_OPTIONS}


def normalize_provider_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _provider_type_label(provider_type: str) -> str:
    for option in PROVIDER_TYPE_OPTIONS:
        if option["value"] == provider_type:
            return str(option["label"])
    return provider_type


def provider_type_requires_base_url(provider_type: str) -> bool:
    for option in PROVIDER_TYPE_OPTIONS:
        if option["value"] == provider_type:
            return bool(option["requires_base_url"])
    return provider_type == "openai_compatible"


def _field_value(
    fields: dict[str, dict[str, Any]],
    field_name: str,
    default: Any = None,
) -> Any:
    entry = fields.get(field_name)
    if not entry:
        return default
    value = entry.get("info", {}).get("value", default)
    return default if value is None else value


def _field_text(fields: dict[str, dict[str, Any]], field_name: str) -> str:
    value = _field_value(fields, field_name, "")
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def provider_entries_from_flat_config(
    flat_config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for key, value in flat_config.items():
        parsed = parse_provider_key(key)
        if parsed is None:
            continue

        provider_name, field_name = parsed
        normalized_name = normalize_provider_name(provider_name)
        if not normalized_name:
            continue

        entry = providers.setdefault(
            normalized_name,
            {"name": provider_name},
        )
        canonical_field_name = "type" if field_name == "provider" else field_name
        if field_name == "provider" and "type" in entry:
            continue
        entry[canonical_field_name] = value

    return providers


def provider_type_from_body(provider_type: str) -> str:
    normalized = provider_type.strip().lower().replace("-", "_")
    if normalized not in SUPPORTED_PROVIDER_TYPES:
        supported = ", ".join(sorted(SUPPORTED_PROVIDER_TYPES))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported provider type: {provider_type!r}. Supported values: {supported}.",
        )
    return normalized


def validate_provider_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Provider name is required.")
    if normalized.lower() == "default":
        raise HTTPException(
            status_code=400,
            detail="'default' is reserved and cannot be used as a provider name.",
        )
    if not PROVIDER_NAME_PATTERN.match(normalized):
        raise HTTPException(
            status_code=400,
            detail="Provider name can only contain letters, numbers, underscores, and hyphens.",
        )
    return normalized


def provider_config_name(
    service: ConfigService,
    provider_name: str,
) -> str | None:
    normalized_name = normalize_provider_name(provider_name)
    providers = provider_entries_from_flat_config(service.get_all())
    entry = providers.get(normalized_name)
    if entry is None:
        return None
    return str(entry["name"])


def build_provider_rows(
    settings: dict[str, dict[str, Any]],
    expected_fields: list[str],
    default_provider: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for key, info in settings.items():
        parsed = parse_provider_key(key)
        if parsed is None:
            continue

        provider_name, field_name = parsed
        grouped.setdefault(provider_name, {})[field_name] = {
            "key": key,
            "info": info,
        }

    provider_rows: list[dict[str, Any]] = []
    normalized_default_provider = normalize_provider_name(default_provider)
    for provider_name in sorted(grouped):
        fields = grouped[provider_name]

        if "provider" not in fields and "type" in fields:
            fields["provider"] = fields["type"]

        for field_name in expected_fields:
            if field_name in fields:
                continue

            synthetic_key = f"llm.providers.{provider_name}.{field_name}"
            metadata = get_setting_metadata(synthetic_key)
            synthetic_info: dict[str, Any] = {
                "value": None,
                "source": "default",
                "input_type": metadata["type"],
                "description": metadata["description"],
                "category": metadata["category"],
                "label": metadata["label"],
            }
            for optional_key in ("min", "max", "step"):
                if optional_key in metadata:
                    synthetic_info[optional_key] = metadata[optional_key]

            fields[field_name] = {
                "key": synthetic_key,
                "info": synthetic_info,
            }

        provider_type = _field_text(fields, "type") or _field_text(fields, "provider")
        enabled = coerce_bool(_field_value(fields, "enabled", True))
        default_model = _field_text(fields, "default_model")
        api_key_configured = bool(_field_text(fields, "api_key"))
        base_url_configured = bool(_field_text(fields, "base_url"))
        requires_base_url = provider_type_requires_base_url(provider_type)
        is_default = (
            bool(normalized_default_provider)
            and normalize_provider_name(provider_name) == normalized_default_provider
        )
        ready = (
            enabled
            and api_key_configured
            and bool(default_model)
            and (not requires_base_url or base_url_configured)
        )
        default_block_reason = ""
        if is_default:
            default_block_reason = "Provider is already default."
        elif not enabled:
            default_block_reason = "Enable the provider before making it default."
        elif not default_model:
            default_block_reason = "Set a default model before making this provider default."
        elif requires_base_url and not base_url_configured:
            default_block_reason = "Set a base URL before making this provider default."
        elif not api_key_configured:
            default_block_reason = "Set an API key before making this provider default."

        provider_rows.append({
            "name": provider_name,
            "fields": fields,
            "type": provider_type,
            "type_label": _provider_type_label(provider_type),
            "requires_base_url": requires_base_url,
            "enabled": enabled,
            "is_default": is_default,
            "ready": ready,
            "can_delete": not is_default,
            "delete_block_reason": "Default provider cannot be deleted." if is_default else "",
            "can_set_default": ready and not is_default,
            "default_block_reason": default_block_reason,
        })

    return provider_rows


def serialize_model_catalog(catalog: Any) -> dict[str, Any]:
    return {
        "provider": catalog.provider,
        "provider_type": catalog.provider_type,
        "default_model": catalog.default_model,
        "can_list_models": catalog.can_list_models,
        "models": [
            {
                "id": option.id,
                "label": option.label,
                "source": option.source,
                "context_window": option.context_window,
                "input_types": list(option.input_types)
                if option.input_types is not None
                else None,
            }
            for option in catalog.models
        ],
        "error": catalog.error,
    }


async def provider_model_options() -> dict[str, Any]:
    try:
        catalogs = await list_provider_model_catalogs()
        config = get_config_service()
        default_model_setting = config.get_str("llm.default_model", "").strip()
        default_provider = ""
        default_model = ""
        if default_model_setting:
            if "/" in default_model_setting:
                default_provider, default_model = (
                    x.strip() for x in default_model_setting.split("/", 1)
                )
            else:
                default_provider = default_model_setting

        if not default_provider:
            default_catalog = await list_provider_model_catalog()
            default_provider = default_catalog.provider

        if not default_model:
            matching_catalog = next(
                (c for c in catalogs if c.provider == default_provider), None
            )
            if matching_catalog:
                default_model = matching_catalog.default_model
    except Exception as exc:
        logger.warning("Failed to load provider model options: %s", exc)
        return {
            "provider_names": [],
            "default_provider": "",
            "default_model": "",
            "model_catalogs": [],
            "model_catalog_error": str(exc),
        }

    return {
        "provider_names": sorted(catalog.provider for catalog in catalogs),
        "default_provider": default_provider,
        "default_model": default_model,
        "model_catalogs": [serialize_model_catalog(catalog) for catalog in catalogs],
        "model_catalog_error": "",
    }
