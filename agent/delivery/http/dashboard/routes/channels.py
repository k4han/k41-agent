from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from agent.delivery.http.dashboard.routes.helpers.deps import get_channel_manager
from agent.modules.channels import (
    get_channel_status,
    list_channel_statuses,
    start_all_channels,
    start_channel,
    stop_all_channels,
    stop_channel,
    test_channel_connection,
)
from agent.modules.users import get_pairing_service
from agent.shared.integrations import IntegrationUnavailableError


router = APIRouter()


@router.post("/channels/pair")
async def generate_pairing_code() -> dict[str, str]:
    """Generate a one-time pairing code for client authentication."""
    pairing_service = get_pairing_service()
    code, user_id = await pairing_service.create_pairing_root_user_and_code()
    return {"code": code, "user_id": str(user_id)}


@router.delete("/channels/identities/{identity_id}")
async def unpair_identity(identity_id: int) -> dict[str, str]:
    """Unpair a previously paired client identity."""
    pairing_service = get_pairing_service()
    await pairing_service.unpair_identity(identity_id)
    return {"status": "success"}


@router.get("/services")
async def get_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    """List all channel services and their current status."""
    channel_manager = get_channel_manager(request)
    return {"services": list_channel_statuses(channel_manager)}


@router.get("/services/{name}")
async def get_service(name: str, request: Request) -> dict[str, str | None]:
    """Get the status of a specific channel service."""
    channel_manager = get_channel_manager(request)
    try:
        return get_channel_status(channel_manager, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/services/{name}/start")
async def start_service(name: str, request: Request) -> dict[str, str | None]:
    """Start a channel service."""
    channel_manager = get_channel_manager(request)
    try:
        status = await start_channel(channel_manager, name)
        return {"message": f"'{name}' is starting.", **status}
    except IntegrationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict()) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/services/{name}/stop")
async def stop_service(name: str, request: Request) -> dict[str, str | None]:
    """Stop a running channel service."""
    channel_manager = get_channel_manager(request)
    try:
        status = await stop_channel(channel_manager, name)
        return {"message": f"'{name}' stopped.", **status}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/services/{name}/test")
async def test_service(name: str) -> dict[str, object]:
    """Test the connection for a channel service without starting it."""
    result = await test_channel_connection(name)
    return result.to_dict()


@router.post("/services/start-all")
async def start_all_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    """Start all configured channel services."""
    channel_manager = get_channel_manager(request)
    try:
        services = await start_all_channels(channel_manager)
    except IntegrationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict()) from exc
    return {"services": services}


@router.post("/services/stop-all")
async def stop_all_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    """Stop all running channel services."""
    channel_manager = get_channel_manager(request)
    services = await stop_all_channels(channel_manager)
    return {"services": services}
