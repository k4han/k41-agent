from fastapi import APIRouter, HTTPException, Request
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
from agent.modules.settings.public import SettingsService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# --- helpers ----------------------------------------------------------


def _get_channel_manager(request: Request) -> ChannelManager:
    channel_manager = getattr(request.app.state, "channel_manager", None)
    if channel_manager is None:
        raise HTTPException(status_code=503, detail="Channel manager is not available.")
    return channel_manager


def _get_settings_service(request: Request) -> SettingsService:
    service = getattr(request.app.state, "settings_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Settings service is not available.")
    return service


def _collection_payload(
    channel_manager: ChannelManager,
) -> dict[str, list[dict[str, str | None]]]:
    return {"services": list_channel_statuses(channel_manager)}


# --- channel management endpoints ------------------------------------


@router.get("/services")
async def list_services(request: Request):
    channel_manager = _get_channel_manager(request)
    return _collection_payload(channel_manager)


@router.get("/services/{name}")
async def get_service(name: str, request: Request):
    channel_manager = _get_channel_manager(request)
    try:
        return get_channel_status(channel_manager, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/services/{name}/start")
async def start_service(name: str, request: Request):
    channel_manager = _get_channel_manager(request)
    try:
        status = await start_channel(channel_manager, name)
        return {"message": f"'{name}' is starting.", **status}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/services/{name}/stop")
async def stop_service(name: str, request: Request):
    channel_manager = _get_channel_manager(request)
    try:
        status = await stop_channel(channel_manager, name)
        return {"message": f"'{name}' stopped.", **status}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/services/start-all")
async def start_all_services(request: Request):
    channel_manager = _get_channel_manager(request)
    services = await start_all_channels(channel_manager)
    return {"services": services}


@router.post("/services/stop-all")
async def stop_all_services(request: Request):
    channel_manager = _get_channel_manager(request)
    services = await stop_all_channels(channel_manager)
    return {"services": services}


# --- settings endpoints -----------------------------------------------


@router.get("/settings")
async def get_settings(request: Request):
    """Return all effective settings with their source."""
    service = _get_settings_service(request)
    return {"settings": service.get_settings_overview()}


@router.get("/settings/sources")
async def get_settings_sources(request: Request):
    """Return all values from all sources, grouped by key."""
    service = _get_settings_service(request)
    return {"sources": service.get_settings_sources()}


class UpdateSettingBody(BaseModel):
    value: str | None


@router.put("/settings/{key:path}")
async def update_setting(key: str, body: UpdateSettingBody, request: Request):
    """Update a desired-state setting (placeholder — DB writer not wired yet)."""
    _get_settings_service(request)  # ensure available
    # TODO: wire SettingsWriter (DB) once async init is in place
    return {
        "message": f"Setting '{key}' update acknowledged.",
        "key": key,
        "value": body.value,
        "note": "DB persistence not yet wired — runtime-only for now.",
    }
