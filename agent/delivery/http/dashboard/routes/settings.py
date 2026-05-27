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
    _validate_default_provider_update,
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
    _ensure_runtime_keys([key])

    value = _normalize_setting_value(key, body.value)
    _validate_default_provider_update(service, {key: value})
    service.update_setting(key, value)
    return {"status": "success", "key": key, "value": value}


@router.put("/settings")
async def update_settings(body: UpdateSettingsBody, request: Request) -> dict[str, Any]:
    if not body.values:
        return {"status": "success", "updated": []}

    _ensure_runtime_keys(list(body.values))

    values = _normalize_setting_updates(body.values)
    service = _get_config_service(request)
    _validate_default_provider_update(service, values)
    _update_config_settings(service, values)

    return {"status": "success", "updated": list(values.keys())}
