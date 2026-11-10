"""Dashboard API for Instabox machines, preview ports, snapshots, operations and the desktop."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from agent.bootstrap.container import get_container
from agent.delivery.http.dashboard.routes.browser import origin_allowed
from agent.modules.admin_auth import get_current_admin
from agent.modules.instabox import (
    InstaboxApiError,
    InstaboxConfirmationError,
    InstaboxConflictError,
    InstaboxDisabledError,
    get_instabox_service,
    redact,
    relay_vnc,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard-api/instabox")
viewer_router = APIRouter()
VIEWER_SEND_TIMEOUT = 5.0


def service(request: Request):
    return get_instabox_service(get_container(request))


def owner(admin: str) -> str:
    return f"admin:{admin}"


def instabox_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, InstaboxDisabledError):
        return HTTPException(503, {"code": "instabox_disabled", "message": str(exc)})
    if isinstance(exc, InstaboxConfirmationError):
        return HTTPException(400, {"code": "confirmation_required", "message": str(exc)})
    if isinstance(exc, InstaboxConflictError):
        return HTTPException(409, {"code": exc.code, "message": str(exc)})
    if isinstance(exc, InstaboxApiError):
        status = exc.status if exc.status in {400, 401, 403, 404, 409, 422, 429} else 502
        return HTTPException(status, redact(exc.to_payload()))
    if isinstance(exc, httpx.TransportError):
        return HTTPException(502, {"code": "instabox_unreachable", "message": "Instabox could not be reached."})
    if isinstance(exc, LookupError):
        return HTTPException(404, {"code": "not_found", "message": str(exc)})
    if isinstance(exc, (ValueError, TypeError)):
        return HTTPException(400, {"code": "invalid_request", "message": str(exc)})
    if isinstance(exc, TimeoutError):
        return HTTPException(504, {"code": "timeout", "message": str(exc) or "Instabox did not answer in time."})
    logger.exception("Unexpected Instabox operation failure.")
    return HTTPException(500, {"code": "internal_error", "message": redact(str(exc))})


async def checked(operation):
    try:
        return await operation
    except Exception as exc:
        raise instabox_http_error(exc) from None


class CreateMachineBody(BaseModel):
    name: str | None = Field(default=None, max_length=63)
    template: str | None = Field(default=None, max_length=200)
    snapshot_id: str | None = Field(default=None, max_length=200)


class ForkBody(BaseModel):
    name: str | None = Field(default=None, max_length=63)


class ConfirmBody(BaseModel):
    confirm: str = Field(min_length=1, max_length=200)


class PortBody(BaseModel):
    port: int = Field(ge=1024, le=65535)
    access: Literal["public", "private"] = "private"
    name: str | None = Field(default=None, max_length=100)


class PortAccessBody(BaseModel):
    access: Literal["public", "private"]


class SnapshotBody(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    description: str | None = Field(default=None, max_length=500)
    root_only: bool = False


class SnapshotMachineBody(BaseModel):
    name: str | None = Field(default=None, max_length=63)


class RestoreBody(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=200)
    confirm_machine: str = Field(min_length=1, max_length=200)
    confirm_snapshot: str = Field(min_length=1, max_length=200)


class DesktopStartBody(BaseModel):
    width: int = Field(default=1280, ge=800, le=2560)
    height: int = Field(default=800, ge=600, le=1600)


# Machines ---------------------------------------------------------------- #


@router.get("/machines")
async def list_machines(ibx=Depends(service)):
    return {"machines": await checked(ibx.list_machines())}


@router.get("/templates")
async def list_templates(ibx=Depends(service)):
    return {"templates": await checked(ibx.templates())}


@router.post("/machines")
async def create_machine(body: CreateMachineBody, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    return await checked(ibx.create_machine(
        name=body.name, template=body.template, snapshot_id=body.snapshot_id,
        metadata={"k41_created_by": "dashboard"}, created_by=owner(admin),
    ))


@router.get("/machines/{machine_id}")
async def get_machine(machine_id: str, ibx=Depends(service)):
    return await checked(ibx.get_machine(machine_id))


async def _machine_action(machine_id: str, action: str, admin: str, ibx) -> dict[str, Any]:
    return await checked(ibx.machine_action(machine_id, action, created_by=owner(admin)))


@router.post("/machines/{machine_id}/start")
async def start_machine(machine_id: str, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    return await _machine_action(machine_id, "start", admin, ibx)


@router.post("/machines/{machine_id}/stop")
async def stop_machine(machine_id: str, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    return await _machine_action(machine_id, "stop", admin, ibx)


@router.post("/machines/{machine_id}/suspend")
async def suspend_machine(machine_id: str, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    return await _machine_action(machine_id, "suspend", admin, ibx)


@router.post("/machines/{machine_id}/resume")
async def resume_machine(machine_id: str, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    return await _machine_action(machine_id, "resume", admin, ibx)


@router.post("/machines/{machine_id}/fork")
async def fork_machine(machine_id: str, body: ForkBody, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    return await checked(ibx.fork_machine(machine_id, name=body.name, created_by=owner(admin)))


@router.delete("/machines/{machine_id}")
async def delete_machine(machine_id: str, body: ConfirmBody, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    result = await checked(ibx.delete_machine(machine_id, confirm=body.confirm, created_by=owner(admin)))
    ibx.desktop.invalidate(machine_id)
    return result


# Operations -------------------------------------------------------------- #


@router.get("/operations")
async def list_operations(
    machine_id: str | None = Query(default=None, max_length=200),
    active: bool = Query(default=False),
    ibx=Depends(service),
):
    return {"operations": await checked(ibx.operations(machine_id=machine_id, active_only=active))}


@router.get("/operations/{record_id}")
async def get_operation(record_id: int, ibx=Depends(service)):
    record = await checked(ibx.operation(record_id))
    if record is None:
        raise HTTPException(404, {"code": "not_found", "message": "Operation not found."})
    return record


# Ports ------------------------------------------------------------------- #


@router.get("/machines/{machine_id}/ports")
async def list_ports(machine_id: str, ibx=Depends(service)):
    return await checked(ibx.list_ports(machine_id))


@router.post("/machines/{machine_id}/ports")
async def publish_port(machine_id: str, body: PortBody, ibx=Depends(service)):
    return await checked(ibx.publish_port(machine_id, body.port, access=body.access, name=body.name))


@router.patch("/machines/{machine_id}/ports/{port}")
async def set_port_access(machine_id: str, port: int, body: PortAccessBody, ibx=Depends(service)):
    return await checked(ibx.set_port_access(machine_id, port, body.access))


@router.post("/machines/{machine_id}/ports/{port}/rotate")
async def rotate_port_token(machine_id: str, port: int, ibx=Depends(service)):
    return await checked(ibx.rotate_port_token(machine_id, port))


@router.get("/machines/{machine_id}/ports/{port}/link")
async def port_link(machine_id: str, port: int, ibx=Depends(service)):
    return await checked(ibx.port_link(machine_id, port))


@router.delete("/machines/{machine_id}/ports/{port}")
async def close_port(machine_id: str, port: int, ibx=Depends(service)):
    await checked(ibx.close_port(machine_id, port))
    return {"closed": True, "port": port}


# Snapshots --------------------------------------------------------------- #


@router.get("/snapshots")
async def list_snapshots(ibx=Depends(service)):
    return {"snapshots": await checked(ibx.list_snapshots())}


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str, ibx=Depends(service)):
    return await checked(ibx.get_snapshot(snapshot_id))


@router.post("/machines/{machine_id}/snapshots")
async def create_snapshot(machine_id: str, body: SnapshotBody, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    return await checked(ibx.create_snapshot(
        machine_id, name=body.name, description=body.description, root_only=body.root_only, created_by=owner(admin),
    ))


@router.delete("/snapshots/{snapshot_id}")
async def delete_snapshot(snapshot_id: str, body: ConfirmBody, ibx=Depends(service)):
    await checked(ibx.delete_snapshot(snapshot_id, confirm=body.confirm))
    return {"deleted": True, "snapshot_id": snapshot_id}


@router.post("/snapshots/{snapshot_id}/machines")
async def machine_from_snapshot(
    snapshot_id: str, body: SnapshotMachineBody, admin: str = Depends(get_current_admin), ibx=Depends(service)
):
    return await checked(ibx.create_machine_from_snapshot(snapshot_id, name=body.name, created_by=owner(admin)))


@router.post("/machines/{machine_id}/restore")
async def restore_machine(machine_id: str, body: RestoreBody, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    return await checked(ibx.restore_machine(
        machine_id, body.snapshot_id,
        confirm_machine=body.confirm_machine, confirm_snapshot=body.confirm_snapshot, created_by=owner(admin),
    ))


# Desktop ----------------------------------------------------------------- #


@router.get("/machines/{machine_id}/desktop")
async def desktop_status(machine_id: str, ibx=Depends(service)):
    return await checked(ibx.desktop.status(machine_id))


@router.post("/machines/{machine_id}/desktop/start")
async def desktop_start(machine_id: str, body: DesktopStartBody, ibx=Depends(service)):
    return await checked(ibx.desktop.ensure_started(machine_id, width=body.width, height=body.height))


@router.post("/machines/{machine_id}/desktop/takeover")
async def desktop_takeover(machine_id: str, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    return await checked(ibx.desktop.take_control(machine_id, owner(admin)))


@router.post("/machines/{machine_id}/desktop/heartbeat")
async def desktop_heartbeat(machine_id: str, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    return await checked(ibx.desktop.heartbeat(machine_id, owner(admin)))


@router.post("/machines/{machine_id}/desktop/handback")
async def desktop_handback(machine_id: str, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    return await checked(ibx.desktop.release_control(machine_id, owner(admin)))


@router.post("/machines/{machine_id}/desktop/viewer-ticket")
async def desktop_viewer_ticket(machine_id: str, admin: str = Depends(get_current_admin), ibx=Depends(service)):
    await checked(ibx.client())
    return {"ticket": ibx.desktop.issue_ticket(machine_id, owner(admin))}


@viewer_router.websocket("/dashboard-api/instabox/desktop/viewer")
async def desktop_viewer(websocket: WebSocket):
    """Ticket-authenticated VNC relay; the first text message must carry the ticket."""
    container = get_container(websocket)
    if not origin_allowed(websocket, container.config_service):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    ibx = get_instabox_service(container)
    machine_id = None
    closer = None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), 8)
        if len(raw) > 1024:
            raise PermissionError("Invalid viewer ticket.")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise PermissionError("Invalid viewer ticket.")
        ticket = ibx.desktop.consume_ticket(payload.get("ticket", ""))
        machine_id = ticket.machine_id
        _, closer = ibx.desktop.register_viewer(machine_id)
        await relay_vnc(websocket, ibx, machine_id, closer)
    except (WebSocketDisconnect, ConnectionError, TimeoutError):
        pass
    except PermissionError as exc:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.send_json({"type": "error", "code": "ticket_invalid", "message": str(exc)}), VIEWER_SEND_TIMEOUT)
    except Exception:
        logger.warning("Instabox desktop viewer ended with an error.", exc_info=True)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.send_json({"type": "error", "code": "viewer_failed",
                                                        "message": "Desktop connection ended. Reconnect to try again."}), VIEWER_SEND_TIMEOUT)
    finally:
        if machine_id and closer is not None:
            ibx.desktop.unregister_viewer(machine_id, closer)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(websocket.close(), VIEWER_SEND_TIMEOUT)


__all__ = ["router", "viewer_router"]
