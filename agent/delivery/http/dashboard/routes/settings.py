from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from agent.delivery.http.dashboard.routes.shared import (
    _ensure_runtime_keys,
    _get_config_service,
    _normalize_setting_updates,
    _normalize_setting_value,
    _update_config_settings,
    _validate_default_model_update,
)


router = APIRouter()


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, dict[str, Any]]:
    service = _get_config_service(request)
    return {"settings": service.get_settings_overview()}


@router.get("/settings/sources")
async def get_settings_sources(request: Request) -> dict[str, dict[str, Any]]:
    service = _get_config_service(request)
    return {"sources": service.get_settings_sources()}


class UpdateSettingBody(BaseModel):
    value: Any | None


class UpdateSettingsBody(BaseModel):
    values: dict[str, Any | None]


@router.put("/settings/{key:path}")
async def update_setting(
    key: str,
    body: UpdateSettingBody,
    request: Request,
) -> dict[str, Any | None]:
    service = _get_config_service(request)

    if key == "llm.default_provider":
        provider_name = str(body.value or "").strip()
        provider_default_model = service.get(f"llm.providers.{provider_name}.default_model") or ""
        key = "llm.default_model"
        value = f"{provider_name}/{provider_default_model}" if provider_default_model else provider_name
    else:
        _ensure_runtime_keys([key])
        value = _normalize_setting_value(key, body.value)

    _validate_default_model_update(service, {key: value})
    service.update_setting(key, value)
    return {"status": "success", "key": key, "value": value}


@router.put("/settings")
async def update_settings(body: UpdateSettingsBody, request: Request) -> dict[str, Any]:
    if not body.values:
        return {"status": "success", "updated": []}

    raw_values = dict(body.values)
    if "llm.default_provider" in raw_values:
        provider_name = str(raw_values.pop("llm.default_provider") or "").strip()
        provider_default_model = raw_values.get(f"llm.providers.{provider_name}.default_model")
        if provider_default_model is None:
            service = _get_config_service(request)
            provider_default_model = service.get(f"llm.providers.{provider_name}.default_model") or ""
        raw_values["llm.default_model"] = f"{provider_name}/{provider_default_model}" if provider_default_model else provider_name

    _ensure_runtime_keys(list(raw_values))

    values = _normalize_setting_updates(raw_values)
    service = _get_config_service(request)
    _validate_default_model_update(service, values)
    _update_config_settings(service, values)

    return {"status": "success", "updated": list(values.keys())}
