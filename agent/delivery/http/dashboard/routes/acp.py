"""Dashboard routes to install and sign in to ACP agents."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from agent.delivery.http.dashboard.routes.helpers.deps import get_app_container
from agent.modules.acp import (
    AGENT_DEFINITIONS,
    AcpAuthError,
    AcpError,
    CallbackRejected,
    InstallBusyError,
    get_acp_auth_manager,
    get_acp_installer,
    get_acp_service,
    installed_binary,
)
from agent.modules.admin_auth import get_current_admin

router = APIRouter(prefix="/dashboard-api/acp", tags=["acp"])


class InstallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accept_license: bool = False


class SignInBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_id: str = Field(..., min_length=1, max_length=128)


class CompleteSignInBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callback_url: str = Field(..., min_length=1, max_length=16384, repr=False)


class ApiKeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(..., min_length=1, max_length=4096, repr=False)


def _require_agent(agent_id: str):
    definition = AGENT_DEFINITIONS.get(agent_id)
    if definition is None:
        raise HTTPException(404, "Unknown ACP agent.")
    return definition


def _container(request: Request):
    return get_app_container(request)


async def _agent_payload(container: Any, agent_id: str, *, refresh: bool = False) -> dict[str, Any]:
    definition = AGENT_DEFINITIONS[agent_id]
    installer = get_acp_installer(container)
    auth = get_acp_auth_manager(container)
    installed = await asyncio.to_thread(installed_binary, agent_id, installer.data_root)
    job = installer.status(agent_id)
    # Probing starts the agent, so it waits until an install has finished.
    busy_installing = job is not None and job["state"] == "running"
    auth_status = (
        {"signed_in": False, "state": "installing", "methods": [], "config_options": []}
        if busy_installing else await auth.status(agent_id, refresh=refresh)
    )
    return {
        "id": agent_id,
        "display_name": definition.display_name,
        "terms_url": definition.terms_url,
        "installed": installed is not None,
        "version": installed[0].get("version") if installed else None,
        "license": installed[0].get("license") if installed else "proprietary",
        "job": job,
        "auth": auth_status,
        "sign_in": auth.attempt_status(agent_id),
    }


@router.get("/agents")
async def list_acp_agents(request: Request, response: Response, refresh: bool = False,
                          admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    container = _container(request)
    return {"agents": [await _agent_payload(container, agent_id, refresh=refresh) for agent_id in AGENT_DEFINITIONS]}


@router.get("/agents/{agent_id}")
async def get_acp_agent(agent_id: str, request: Request, response: Response, refresh: bool = False,
                        admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    _require_agent(agent_id)
    response.headers["Cache-Control"] = "no-store"
    return await _agent_payload(_container(request), agent_id, refresh=refresh)


@router.post("/agents/{agent_id}/install", status_code=202)
async def install_acp_agent(agent_id: str, body: InstallBody, request: Request,
                            admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    _require_agent(agent_id)
    if not body.accept_license:
        raise HTTPException(400, "Accept the license terms to install this agent.")
    try:
        return get_acp_installer(_container(request)).start(agent_id)
    except InstallBusyError as exc:
        raise HTTPException(409, str(exc)) from None


@router.delete("/agents/{agent_id}")
async def uninstall_acp_agent(agent_id: str, request: Request, admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    _require_agent(agent_id)
    container = _container(request)
    service = get_acp_service(container)
    if service.agent_is_busy(agent_id):
        raise HTTPException(409, "The agent is busy. Wait for running turns to finish.")
    await service.restart_client(agent_id)
    installer = get_acp_installer(container)
    try:
        await asyncio.to_thread(installer.uninstall, agent_id)
    except InstallBusyError as exc:
        raise HTTPException(409, str(exc)) from None
    except OSError as exc:
        raise HTTPException(409, f"The agent files could not be removed: {exc}") from None
    get_acp_auth_manager(container).invalidate(agent_id)
    return await _agent_payload(container, agent_id)


@router.post("/agents/{agent_id}/sign-in")
async def start_acp_sign_in(agent_id: str, body: SignInBody, request: Request, response: Response,
                            admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    _require_agent(agent_id)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await get_acp_auth_manager(_container(request)).start_sign_in(agent_id, body.method_id)
    except AcpError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/agents/{agent_id}/sign-in/{attempt_id}/complete")
async def complete_acp_sign_in(agent_id: str, attempt_id: str, body: CompleteSignInBody, request: Request, response: Response,
                               admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    _require_agent(agent_id)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await get_acp_auth_manager(_container(request)).complete_sign_in(agent_id, attempt_id, body.callback_url)
    except CallbackRejected as exc:
        raise HTTPException(400, str(exc)) from None
    except AcpAuthError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/agents/{agent_id}/sign-in/{attempt_id}/cancel")
async def cancel_acp_sign_in(agent_id: str, attempt_id: str, request: Request,
                             admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    _require_agent(agent_id)
    try:
        return await get_acp_auth_manager(_container(request)).cancel_sign_in(agent_id, attempt_id)
    except AcpAuthError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/agents/{agent_id}/api-key")
async def set_acp_api_key(agent_id: str, body: ApiKeyBody, request: Request, response: Response,
                          admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    _require_agent(agent_id)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await get_acp_auth_manager(_container(request)).set_api_key(agent_id, body.api_key)
    except AcpError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/agents/{agent_id}/sign-out")
async def sign_out_acp_agent(agent_id: str, request: Request, admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    _require_agent(agent_id)
    try:
        return await get_acp_auth_manager(_container(request)).sign_out(agent_id)
    except AcpError as exc:
        raise HTTPException(409, str(exc)) from None
