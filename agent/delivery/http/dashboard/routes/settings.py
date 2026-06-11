from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from agent.delivery.http.dashboard.routes.helpers.deps import get_request_config_service
from agent.delivery.http.dashboard.routes.helpers.settings import (
    ensure_runtime_keys,
    normalize_setting_updates,
    normalize_setting_value,
    update_config_settings,
    validate_default_model_update,
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
    service.update_setting(key, value)
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
    update_config_settings(service, values)

    return {"status": "success", "updated": list(values.keys())}
