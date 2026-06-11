from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException, Request

from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.providers import (
    PROVIDER_TYPE_OPTIONS,
    build_provider_rows,
    normalize_provider_name,
    provider_entries_from_flat_config,
    provider_model_options,
)
from agent.modules.providers import load_providers_catalog
from agent.shared.config import (
    BOOTSTRAP_BOOLEAN_CONFIG_KEYS,
    BOOTSTRAP_CONFIG_KEYS,
    PROVIDER_SETTING_FIELD_ORDER,
    ConfigService,
    is_runtime_key,
)
from agent.shared.infrastructure.config_file import coerce_bool


def group_settings_by_category(
    settings: dict[str, dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for key, info in settings.items():
        category = info.get("category", "general")
        grouped.setdefault(category, {})[key] = info
    return grouped


def _is_provider_setting_key(key: str) -> bool:
    if key.startswith("llm.providers."):
        return True
    return key.startswith("llm.")


def _is_channel_setting_key(key: str) -> bool:
    return key.startswith("channels.")


def _is_workspace_setting_key(key: str) -> bool:
    return key == "workspace.root" or key.startswith("workspace.")


def _is_skill_setting_key(key: str) -> bool:
    return key.startswith("skills.")


def _is_mcp_setting_key(key: str) -> bool:
    return key.startswith("mcp.servers.")


def _filter_settings[T](
    settings: dict[str, T],
    *,
    include_provider_settings: bool,
) -> dict[str, T]:
    return {
        key: value
        for key, value in settings.items()
        if _is_provider_setting_key(key) == include_provider_settings
    }


def _filter_config_settings[T](settings: dict[str, T]) -> dict[str, T]:
    return {
        key: value
        for key, value in settings.items()
        if (
            not _is_provider_setting_key(key)
            and not _is_channel_setting_key(key)
            and not _is_workspace_setting_key(key)
            and not _is_skill_setting_key(key)
            and not _is_mcp_setting_key(key)
        )
    }


def _filter_workspace_settings[T](settings: dict[str, T]) -> dict[str, T]:
    return {
        key: value
        for key, value in settings.items()
        if _is_workspace_setting_key(key)
    }


def _split_provider_settings(
    settings: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    global_settings: dict[str, dict[str, Any]] = {}
    provider_settings: dict[str, dict[str, Any]] = {}
    for key, info in settings.items():
        if key.startswith("llm.providers."):
            provider_settings[key] = info
            continue
        global_settings[key] = info
    return global_settings, provider_settings


def update_config_settings(
    service: ConfigService,
    values: dict[str, Any | None],
    *,
    require_writable: bool = False,
) -> None:
    remaining = dict(values)
    updated_keys: set[str] = set()
    for source in sorted(service._sources, key=lambda s: s.priority, reverse=True):
        update_settings = getattr(source, "update_settings", None)
        if not callable(update_settings):
            continue
        can_update_key = getattr(source, "can_update_key", None)
        source_values = {
            key: value
            for key, value in remaining.items()
            if not callable(can_update_key) or can_update_key(key)
        }
        if not source_values:
            continue
        update_settings(source_values)
        updated_keys.update(source_values)
        for key in source_values:
            remaining.pop(key, None)
    if require_writable and remaining:
        raise HTTPException(
            status_code=503,
            detail="No writable config source is available.",
        )
    if require_writable and not updated_keys:
        raise HTTPException(
            status_code=503,
            detail="No writable config source is available.",
        )
    service.reload()


def delete_config_tree(service: ConfigService, key: str) -> bool:
    deleted = False
    for source in service._sources:
        delete_setting_tree = getattr(source, "delete_setting_tree", None)
        if callable(delete_setting_tree):
            deleted = bool(delete_setting_tree(key)) or deleted
    service.reload()
    return deleted


def validate_default_model_update(
    service: ConfigService,
    values: dict[str, Any | None],
) -> None:
    configured_default_model = str(service.get("llm.default_model", "") or "").strip()
    default_model_val = str(values.get("llm.default_model", configured_default_model) or "").strip()
    if not default_model_val:
        return

    if "/" in default_model_val:
        default_provider = default_model_val.split("/", 1)[0].strip()
    else:
        default_provider = default_model_val

    should_validate = "llm.default_model" in values
    if not should_validate and configured_default_model:
        if "/" in configured_default_model:
            conf_provider = configured_default_model.split("/", 1)[0].strip()
        else:
            conf_provider = configured_default_model
        default_prefix = f"llm.providers.{conf_provider}."
        should_validate = any(key.startswith(default_prefix) for key in values)
    if not should_validate:
        return

    flat_config = service.get_all()
    flat_config.update(values)
    providers = provider_entries_from_flat_config(flat_config)
    provider = providers.get(normalize_provider_name(default_provider))
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown default provider: {default_provider}.",
        )

    if not coerce_bool(provider.get("enabled", True)):
        raise HTTPException(
            status_code=400,
            detail="Default provider must be enabled.",
        )
    if not str(provider.get("api_key") or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Default provider must have an API key.",
        )
    if str(provider.get("type") or "").strip() == "openai_compatible" and not str(
        provider.get("base_url") or ""
    ).strip():
        raise HTTPException(
            status_code=400,
            detail="OpenAI-compatible default provider must have a base URL.",
        )
    if not str(provider.get("default_model") or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Default provider must have a default model.",
        )


def ensure_runtime_keys(keys: list[str]) -> None:
    if invalid_keys := sorted(k for k in keys if not is_runtime_key(k)):
        suffix = "" if len(invalid_keys) == 1 else "s"
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported runtime setting{suffix}: {', '.join(invalid_keys)}.",
        )


def _normalize_bootstrap_host(value: Any | None) -> str:
    host = str(value or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="Host cannot be empty.")
    return host


def _normalize_bootstrap_port(value: Any | None) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail="Port must be an integer.")
    if isinstance(value, int):
        port = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise HTTPException(status_code=400, detail="Port must be an integer.")
        port = int(value)
    else:
        text = str(value or "").strip()
        if not re.fullmatch(r"\d+", text):
            raise HTTPException(status_code=400, detail="Port must be an integer.")
        port = int(text)

    if port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="Port must be between 1 and 65535.")
    return port


def normalize_setting_value(key: str, value: Any | None) -> Any | None:
    if key == "skills.repository_dir":
        from agent.modules.skills import normalize_repository_skill_dir

        try:
            return normalize_repository_skill_dir(str(value or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if key in BOOTSTRAP_CONFIG_KEYS:
        if key == "host":
            return _normalize_bootstrap_host(value)
        if key == "port":
            return _normalize_bootstrap_port(value)
        if key in BOOTSTRAP_BOOLEAN_CONFIG_KEYS:
            return coerce_bool(value)
    if value is None or not key.endswith(".models"):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return value


def normalize_setting_updates(values: dict[str, Any | None]) -> dict[str, Any | None]:
    return {key: normalize_setting_value(key, value) for key, value in values.items()}


async def settings_payload(request: Request, *, include_provider_settings: bool) -> dict[str, Any]:
    service = get_request_config_service(request)
    settings_raw, settings_sources_raw = service.get_settings_overview_and_sources()

    if not include_provider_settings:
        settings = _filter_config_settings(settings_raw)
        settings_sources = _filter_config_settings(settings_sources_raw)
        return {
            "active_nav": "config",
            "page_title": "Runtime Configuration",
            "page_subtitle": "Manage database, security, and general runtime settings.",
            "settings": settings,
            "by_category": group_settings_by_category(settings),
            "settings_sources": settings_sources,
        }

    settings = _filter_settings(
        settings_raw,
        include_provider_settings=include_provider_settings,
    )
    settings_sources = _filter_settings(
        settings_sources_raw,
        include_provider_settings=include_provider_settings,
    )

    global_settings, provider_settings = _split_provider_settings(settings)

    # Calculate default_provider dynamically from llm.default_model
    default_model_val = str(global_settings.get("llm.default_model", {}).get("value") or "").strip()
    if "/" in default_model_val:
        default_provider = default_model_val.split("/", 1)[0].strip()
    else:
        default_provider = default_model_val

    # Construct synthetic llm.default_provider setting info for frontend compatibility
    global_settings["llm.default_provider"] = {
        "value": default_provider,
        "source": global_settings.get("llm.default_model", {}).get("source", "default"),
        "input_type": "text",
        "description": "Default provider name derived dynamically from llm.default_model",
        "category": "llm",
        "label": "LLM Default Provider",
    }
    # Also add it to flat settings dict for consistency
    settings["llm.default_provider"] = global_settings["llm.default_provider"]

    provider_rows = build_provider_rows(
        provider_settings,
        PROVIDER_SETTING_FIELD_ORDER,
        default_provider,
    )

    catalog = load_providers_catalog()
    catalog_serialized = {}
    for pid, entry in catalog.items():
        catalog_serialized[pid] = {
            "id": entry.id,
            "name": entry.name,
            "provider_type": entry.provider_type,
            "base_url": entry.base_url,
            "env_vars": list(entry.env_vars),
            "doc_url": entry.doc_url,
            "default_model": entry.default_model,
            "logo_url": entry.logo_url,
            "models": [
                {
                    "id": m.id,
                    "name": m.name,
                    "context_window": m.context_window,
                    "max_output": m.max_output,
                    "input_types": list(m.input_types),
                    "output_types": list(m.output_types),
                    "reasoning": m.reasoning,
                    "tool_call": m.tool_call,
                    "cost_input": m.cost_input,
                    "cost_output": m.cost_output,
                }
                for m in entry.models
            ]
        }

    return {
        "active_nav": "providers",
        "page_title": "Provider Configuration",
        "page_subtitle": "Manage default provider and per-provider LLM credentials/models.",
        "settings": settings,
        "by_category": group_settings_by_category(global_settings),
        "settings_sources": settings_sources,
        "provider_rows": provider_rows,
        "provider_name_options": [row["name"] for row in provider_rows],
        "provider_field_order": PROVIDER_SETTING_FIELD_ORDER,
        "provider_type_options": PROVIDER_TYPE_OPTIONS,
        "providers_catalog": catalog_serialized,
        **await provider_model_options(),
    }


async def backend_settings_payload(request: Request) -> dict[str, Any]:
    service = get_request_config_service(request)
    settings_raw, settings_sources_raw = service.get_settings_overview_and_sources()
    settings = _filter_workspace_settings(settings_raw)
    settings_sources = _filter_workspace_settings(settings_sources_raw)
    return {
        "active_nav": "backends",
        "page_title": "Workspace Backends",
        "page_subtitle": "Manage local, Daytona, and Modal workspace backends.",
        "settings": settings,
        "by_category": group_settings_by_category(settings),
        "settings_sources": settings_sources,
    }
