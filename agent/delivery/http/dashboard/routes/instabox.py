"""Dashboard API for Instabox machines, preview ports, snapshots and operations.

The desktop is served by the backend-neutral ``computers`` routes.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from agent.bootstrap.container import get_container
from agent.modules.admin_auth import get_current_admin
from agent.modules.instabox import (
    InstaboxApiError,
    InstaboxConfirmationError,
    InstaboxConflictError,
    InstaboxDisabledError,
    get_instabox_service,
    redact,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard-api/instabox")


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
    return await checked(ibx.delete_machine(machine_id, confirm=body.confirm, created_by=owner(admin)))


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


__all__ = ["router"]
