from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from agent.delivery.http.dashboard.routes.helpers.agents import (
    invalidate_agent_provider_options_cache,
)
from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.settings import (
    delete_config_tree,
    ensure_runtime_keys,
    group_settings_by_category,
    is_channel_context_trim_setting,
    normalize_setting_updates,
    normalize_setting_value,
    update_config_settings,
    validate_default_model_update,
)
from agent.modules.tools import (
    ToolSource,
    find_descriptors,
    resolve_global_tool_config_schemas,
    seed_tool_runtime_defaults,
    serialize_tool_config_schemas,
)
from agent.shared.config.web_connections import (
    connection_payload, is_legacy_web_key, translate_legacy_updates, validate_tool_references, web_tool_schemas, web_tool_sources,
)


router = APIRouter()


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, dict[str, Any]]:
    """Get all runtime settings as key-value pairs."""
    service = get_request_config_service(request)
    return {"settings": service.get_settings_overview()}


@router.get("/settings/sources")
async def get_settings_sources(request: Request) -> dict[str, dict[str, Any]]:
    """Get the source (config file, environment, etc.) for each setting."""
    service = get_request_config_service(request)
    return {"sources": service.get_settings_sources()}


@router.get("/dashboard-api/tools")
async def get_dashboard_tools_settings(request: Request) -> dict[str, Any]:
    """Global tool configuration page: schemas, effective values, and settings."""
    service = get_request_config_service(request)
    seed_tool_runtime_defaults(service)
    settings_raw, settings_sources_raw = service.get_settings_overview_and_sources()
    settings = {key: info for key, info in settings_raw.items() if key.startswith("tools.") and not is_legacy_web_key(key)}
    settings_sources = {
        key: info for key, info in settings_sources_raw.items() if key.startswith("tools.") and not is_legacy_web_key(key)
    }
    descriptors = find_descriptors(source=ToolSource.BUILTIN)
    schemas = web_tool_schemas(serialize_tool_config_schemas(descriptors), service, global_view=True)
    connections = connection_payload(service)
    for tool, schema in schemas.items():
        for field in schema["fields"]:
            key = f"tools.{tool}.{field['name']}"
            if key in settings and field["name"].endswith("_connection"):
                kind = field["name"].removesuffix("_connection")
                settings[key] = {**settings[key], "input_type": "select", "options": field["options"],
                                 "description": f"Shared default: {connections['defaults'].get(kind) or 'environment credentials'}. Select a named connection to use separate credentials."}
    return {
        "active_nav": "tools",
        "page_title": "Tool Configuration",
        "page_subtitle": "Manage global configuration and credentials for built-in tools.",
        "tool_config_schemas": schemas,
        "web_connections": connections,
        "tool_config_sources": web_tool_sources(service),
        "tool_config_effective": resolve_global_tool_config_schemas(service),
        "settings": settings,
        "by_category": group_settings_by_category(settings),
        "settings_sources": settings_sources,
    }


@router.get("/dashboard-api/decisions")
async def get_dashboard_decisions_settings(request: Request) -> dict[str, Any]:
    """Decision model and routing strategy settings."""
    service = get_request_config_service(request)
    settings_raw, settings_sources_raw = service.get_settings_overview_and_sources()
    settings = {key: info for key, info in settings_raw.items() if key.startswith("decision.")}
    settings_sources = {
        key: info for key, info in settings_sources_raw.items() if key.startswith("decision.")
    }
    return {
        "active_nav": "decisions",
        "page_title": "Decisions & Routing",
        "page_subtitle": "Configure Cloudflare Clef-flash decision model and agent routing strategies.",
        "settings": settings,
        "by_category": group_settings_by_category(settings),
        "settings_sources": settings_sources,
    }


class UpdateSettingBody(BaseModel):
    """Request body for updating a single setting."""

    value: Any | None = Field(..., description="New value for the setting. Use null to reset to default.")


class UpdateSettingsBody(BaseModel):
    """Request body for batch-updating multiple settings."""

    values: dict[str, Any | None] = Field(..., description="Mapping of setting keys to new values.")


@router.put("/settings/{key:path}")
async def update_setting(
    key: str,
    body: UpdateSettingBody,
    request: Request,
) -> dict[str, Any | None]:
    """Update a single runtime setting by key."""
    service = get_request_config_service(request)

    if key == "llm.default_provider":
        provider_name = str(body.value or "").strip()
        provider_default_model = service.get(f"llm.providers.{provider_name}.default_model") or ""
        key = "llm.default_model"
        value = f"{provider_name}/{provider_default_model}" if provider_default_model else provider_name
    else:
        ensure_runtime_keys([key])
        value = normalize_setting_value(key, body.value)

    validate_default_model_update(service, {key: value})
    if key.startswith("decision.providers.") or key in {"decision.default_provider", "decision.migration_version"}:
        from fastapi import HTTPException
        raise HTTPException(400, "Manage decision providers through the decision-providers API.")
    if key.startswith("web."):
        from fastapi import HTTPException
        raise HTTPException(400, "Manage web connections through the web-connections API.")
    if is_legacy_web_key(key):
        update_config_settings(service, translate_legacy_updates(service, {key: value}), require_writable=True)
        return {"status": "success", "key": key, "value": value}
    if key.startswith("tools.") and key.endswith("_connection"):
        try:
            validate_tool_references(service, key.split(".")[1], {key.split(".")[2]: value})
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(400, str(exc)) from exc
    if (key.startswith("tools.") or is_channel_context_trim_setting(key)) and value is None:
        # Reset: remove the stored override so the schema default applies.
        delete_config_tree(service, key)
    else:
        service.update_setting(key, value)
    invalidate_agent_provider_options_cache()
    return {"status": "success", "key": key, "value": value}


@router.put("/settings")
async def update_settings(body: UpdateSettingsBody, request: Request) -> dict[str, Any]:
    """Batch update multiple runtime settings at once."""
    if not body.values:
        return {"status": "success", "updated": []}

    raw_values = dict(body.values)
    if "llm.default_provider" in raw_values:
        provider_name = str(raw_values.pop("llm.default_provider") or "").strip()
        provider_default_model = raw_values.get(f"llm.providers.{provider_name}.default_model")
        if provider_default_model is None:
            service = get_request_config_service(request)
            provider_default_model = service.get(f"llm.providers.{provider_name}.default_model") or ""
        raw_values["llm.default_model"] = f"{provider_name}/{provider_default_model}" if provider_default_model else provider_name

    ensure_runtime_keys(list(raw_values))

    values = normalize_setting_updates(raw_values)
    service = get_request_config_service(request)
    validate_default_model_update(service, values)
    if any(key.startswith("decision.providers.") or key in {"decision.default_provider", "decision.migration_version"} for key in values):
        from fastapi import HTTPException
        raise HTTPException(400, "Manage decision providers through the decision-providers API.")
    if any(key.startswith("web.") for key in values):
        from fastapi import HTTPException
        raise HTTPException(400, "Manage web connections through the web-connections API.")
    for key, value in values.items():
        if key.startswith("tools.") and key.endswith("_connection"):
            try:
                validate_tool_references(service, key.split(".")[1], {key.split(".")[2]: value})
            except ValueError as exc:
                from fastapi import HTTPException
                raise HTTPException(400, str(exc)) from exc
    values = translate_legacy_updates(service, values)
    reset_keys = {
        key for key, value in values.items()
        if (key.startswith("tools.") or is_channel_context_trim_setting(key)) and value is None
    }
    update_values = {key: value for key, value in values.items() if key not in reset_keys}
    for key in sorted(reset_keys):
        delete_config_tree(service, key)
    update_config_settings(service, update_values)
    invalidate_agent_provider_options_cache()

    return {"status": "success", "updated": list(raw_values.keys())}
