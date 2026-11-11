"""Dashboard API for the desktop of any workspace machine: status, start, human control and the viewer."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from agent.bootstrap.container import get_container
from agent.delivery.http.dashboard.routes.browser import origin_allowed
from agent.modules.admin_auth import get_current_admin
from agent.modules.workspaces import ComputerError, get_computer_control, get_workspace_computer, relay_viewer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard-api/computers/{backend}/{locator}")
viewer_router = APIRouter()
VIEWER_SEND_TIMEOUT = 5.0
HTTP_STATUS = {
    "unsupported": 404,
    "invalid_input": 400,
    "not_found": 404,
    "permission_denied": 403,
    "disabled": 503,
    "human_in_control": 409,
    "controlled_by_other": 409,
    "control_lost": 409,
    "timeout": 504,
    "upstream": 502,
}


class StartBody(BaseModel):
    width: int = Field(default=1280, ge=800, le=2560)
    height: int = Field(default=800, ge=600, le=1600)


def owner(admin: str) -> str:
    return f"admin:{admin}"


async def checked(operation):
    try:
        return await operation
    except ComputerError as exc:
        raise HTTPException(HTTP_STATUS.get(exc.code, 500), {"code": exc.code, "message": str(exc)}) from None


async def computer(backend: str):
    return await checked(get_workspace_computer(backend))


def with_control(backend: str, locator: str, state: dict) -> dict:
    return {**(state or {}), **get_computer_control().state(backend, locator)}


@router.get("")
async def desktop_status(backend: str, locator: str):
    return with_control(backend, locator, await checked((await computer(backend)).status(locator)))


@router.post("/start")
async def desktop_start(backend: str, locator: str, body: StartBody):
    desktop = await computer(backend)
    return with_control(backend, locator, await checked(desktop.start(locator, width=body.width, height=body.height)))


@router.post("/takeover")
async def desktop_takeover(backend: str, locator: str, admin: str = Depends(get_current_admin)):
    desktop = await computer(backend)
    control = get_computer_control()
    await checked(_take(control, backend, locator, owner(admin)))
    try:
        state = await checked(desktop.set_human_control(locator, True))
    except HTTPException:
        control.release(backend, locator, owner(admin))
        raise
    return with_control(backend, locator, state)


async def _take(control, backend: str, locator: str, owner_id: str) -> None:
    # Wait for an agent input in flight so the person starts from a settled screen.
    async with control.lock(backend, locator):
        control.take(backend, locator, owner_id)


@router.post("/heartbeat")
async def desktop_heartbeat(backend: str, locator: str, admin: str = Depends(get_current_admin)):
    desktop = await computer(backend)
    try:
        get_computer_control().renew(backend, locator, owner(admin))
    except ComputerError as exc:
        raise HTTPException(HTTP_STATUS[exc.code], {"code": exc.code, "message": str(exc)}) from None
    # Each call from the holder renews the backend's own lock as well.
    return with_control(backend, locator, await checked(desktop.set_human_control(locator, True)))


@router.post("/handback")
async def desktop_handback(backend: str, locator: str, admin: str = Depends(get_current_admin)):
    desktop = await computer(backend)
    get_computer_control().release(backend, locator, owner(admin))
    return with_control(backend, locator, await checked(desktop.set_human_control(locator, False)))


@router.post("/viewer-ticket")
async def desktop_viewer_ticket(backend: str, locator: str, admin: str = Depends(get_current_admin)):
    desktop = await computer(backend)
    # Fail before the browser opens a socket when the backend is unusable.
    await checked(desktop.viewer_endpoint(locator))
    return {"ticket": get_computer_control().issue_ticket(backend, locator, owner(admin))}


@viewer_router.websocket("/dashboard-api/computers/viewer")
async def desktop_viewer(websocket: WebSocket):
    """Ticket-authenticated RFB relay; the first text message must carry the ticket."""
    container = get_container(websocket)
    if not origin_allowed(websocket, container.config_service):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    control = get_computer_control(container)
    ticket = None
    closer = None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), 8)
        if len(raw) > 1024:
            raise PermissionError("Invalid viewer ticket.")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise PermissionError("Invalid viewer ticket.")
        ticket = control.consume_ticket(payload.get("ticket", ""))
        endpoint = await (await get_workspace_computer(ticket.backend)).viewer_endpoint(ticket.locator)
        closer = control.register_viewer(ticket.backend, ticket.locator)
        await relay_viewer(websocket, endpoint, closer)
    except (WebSocketDisconnect, ConnectionError, TimeoutError):
        pass
    except PermissionError as exc:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.send_json({"type": "error", "code": "ticket_invalid", "message": str(exc)}),
                                   VIEWER_SEND_TIMEOUT)
    except ComputerError as exc:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.send_json({"type": "error", "code": exc.code, "message": str(exc)}),
                                   VIEWER_SEND_TIMEOUT)
    except Exception:
        logger.warning("Desktop viewer ended with an error.", exc_info=True)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.send_json({"type": "error", "code": "viewer_failed",
                                                        "message": "Desktop connection ended. Reconnect to try again."}),
                                   VIEWER_SEND_TIMEOUT)
    finally:
        if ticket is not None and closer is not None:
            control.unregister_viewer(ticket.backend, ticket.locator, closer)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.close(), VIEWER_SEND_TIMEOUT)


__all__ = ["router", "viewer_router"]
