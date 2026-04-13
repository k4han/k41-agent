from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from agent.modules.channels.public import (
    ChannelManager,
    get_channel_status,
    list_channel_statuses,
    start_all_channels,
    start_channel,
    stop_all_channels,
    stop_channel,
)
from agent.shared.config import is_runtime_key, ConfigService
from agent.modules.users.public import get_pairing_service
from fastapi import Depends
from agent.modules.admin_auth.public import get_current_admin

router = APIRouter(tags=["dashboard"], dependencies=[Depends(get_current_admin)])
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# --- helpers ----------------------------------------------------------


def _get_channel_manager(request: Request) -> ChannelManager:
    channel_manager = getattr(request.app.state, "channel_manager", None)
    if channel_manager is None:
        raise HTTPException(status_code=503, detail="Channel manager is not available.")
    return channel_manager


def _get_config_service(request: Request) -> ConfigService:
    service = getattr(request.app.state, "config_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Config service is not available.")
    return service


def _collection_payload(
    channel_manager: ChannelManager,
) -> dict[str, list[dict[str, str | None]]]:
    return {"services": list_channel_statuses(channel_manager)}


# --- views -------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def dashboard_index(request: Request) -> HTMLResponse:
    channel_manager = _get_channel_manager(request)
    services = list_channel_statuses(channel_manager)
    return templates.TemplateResponse(
        request=request, name="index.html", context={"services": services}
    )

@router.get("/channels", response_class=HTMLResponse)
async def dashboard_channels(request: Request) -> HTMLResponse:
    pairing_service = get_pairing_service()
    identities = await pairing_service.list_paired_identities()
    return templates.TemplateResponse(
        request=request, name="channels.html", context={"request": request, "identities": identities}
    )

@router.post("/channels/pair")
async def generate_pairing_code(request: Request) -> dict[str, str]:
    pairing_service = get_pairing_service()
    code, user_id = await pairing_service.create_pairing_root_user_and_code()
    return {"code": code, "user_id": str(user_id)}

@router.delete("/channels/identities/{identity_id}")
async def unpair_identity(identity_id: int) -> dict[str, str]:
    pairing_service = get_pairing_service()
    await pairing_service.unpair_identity(identity_id)
    return {"status": "success"}

@router.get("/config", response_class=HTMLResponse)
async def dashboard_config(request: Request) -> HTMLResponse:
    service = _get_config_service(request)
    settings = service.get_settings_overview()
    return templates.TemplateResponse(
        request=request, name="config.html", context={"settings": settings}
    )


@router.get("/services")
async def get_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    channel_manager = _get_channel_manager(request)
    return _collection_payload(channel_manager)

@router.get("/services/{name}")
async def get_service(name: str, request: Request) -> dict[str, str | None]:
    channel_manager = _get_channel_manager(request)
    try:
        return get_channel_status(channel_manager, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/services/{name}/start")
async def start_service(name: str, request: Request) -> dict[str, str | None]:
    channel_manager = _get_channel_manager(request)
    try:
        status = await start_channel(channel_manager, name)
        return {"message": f"'{name}' is starting.", **status}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/services/{name}/stop")
async def stop_service(name: str, request: Request) -> dict[str, str | None]:
    channel_manager = _get_channel_manager(request)
    try:
        status = await stop_channel(channel_manager, name)
        return {"message": f"'{name}' stopped.", **status}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.post("/services/start-all")
async def start_all_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    channel_manager = _get_channel_manager(request)
    services = await start_all_channels(channel_manager)
    return {"services": services}

@router.post("/services/stop-all")
async def stop_all_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    channel_manager = _get_channel_manager(request)
    services = await stop_all_channels(channel_manager)
    return {"services": services}


# --- settings endpoints -----------------------------------------------


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, dict[str, Any]]:
    """Return all effective settings with their source."""
    service = _get_config_service(request)
    return {"settings": service.get_settings_overview()}


@router.get("/settings/sources")
async def get_settings_sources(request: Request) -> dict[str, dict[str, Any]]:
    """Return all values from all sources, grouped by key."""
    service = _get_config_service(request)
    return {"sources": service.get_settings_sources()}


class UpdateSettingBody(BaseModel):
    value: str | None


@router.put("/settings/{key:path}")
async def update_setting(key: str, body: UpdateSettingBody, request: Request) -> dict[str, str | None]:
    """Update a runtime setting and persist it to yaml."""
    service = _get_config_service(request)
    if not is_runtime_key(key):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported runtime setting: '{key}'.",
        )

    service.update_setting(key, body.value)
    return {"status": "success", "key": key, "value": body.value}
