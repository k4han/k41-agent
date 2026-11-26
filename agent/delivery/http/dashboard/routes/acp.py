"""Administrator controls for the ACP CLIs running on the K41 host."""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from agent.delivery.http.dashboard.routes.helpers.deps import get_app_container
from agent.modules.acp import (
    AGENT_DEFINITIONS,
    AcpConflict,
    AcpError,
    auth_status,
    get_acp_install_manager,
    installation_actions,
    launcher,
    normalize_agent_name,
)
from agent.modules.admin_auth import get_current_admin


def _require_agent(agent_id: str) -> str:
    if agent_id not in AGENT_DEFINITIONS:
        raise HTTPException(404, "Unknown ACP agent.")
    return agent_id


AgentId = Annotated[str, Depends(_require_agent)]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/dashboard-api/acp",
    tags=["acp"],
    dependencies=[Depends(get_current_admin), Depends(_no_store)],
)


def _login_command(command: str, managed_path: str | None) -> str:
    if managed_path is None:
        return command
    executable = "& '" + managed_path.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(managed_path)
    return f"{executable} {command.split(' ', 1)[1]}"


async def _agent_payload(
    container: Any, agent_id: str, *, refresh: bool = False, agent_name: str | None = None, cwd: str | None = None,
    check_updates: bool = False,
) -> dict[str, Any]:
    definition = AGENT_DEFINITIONS[agent_id]
    manager = get_acp_install_manager(container)
    operation = manager.operation(agent_id)
    operation_payload = operation.payload() if operation is not None else None
    running = operation is not None and operation.state == "running"
    if running:
        installation = operation.before
        executable_path = str(installation.path) if installation.path is not None else None
        version = operation.version
        revision = operation.revision
        auth = auth_status("error", error="ACP maintenance is in progress.") if executable_path else auth_status("not_installed")
    else:
        with manager.service.inspect_agent(agent_id):
            for attempt in range(2):
                refresh_probe = refresh or attempt > 0
                installation, revision = await asyncio.to_thread(manager.installation, agent_id, refresh=refresh_probe)
                executable_path, version = None, None
                if installation.path is not None:
                    executable = await asyncio.to_thread(installation.path.resolve)
                    executable_path = str(executable)
                    version, _ = await asyncio.to_thread(launcher.inspect_executable, agent_id, executable, refresh=refresh_probe)
                auth = await manager.auth.status(agent_id, refresh=refresh_probe, agent_name=agent_name, cwd=cwd)
                if await asyncio.to_thread(installation.is_current, agent_id, revision):
                    break
            else:
                raise HTTPException(
                    409, "The ACP installation changed. Refresh its status and try again.",
                    headers={"Cache-Control": "no-store"},
                )
    latest = await asyncio.to_thread(manager.latest, agent_id, installation, check=check_updates and not running, refresh=refresh)
    return {
        "id": agent_id,
        "display_name": definition.display_name,
        "terms_url": definition.terms_url,
        "installation_source": "managed" if installation.source == "managed" else definition.installation_source,
        "management_source": installation.source,
        "installation_revision": revision,
        "executable_path": executable_path,
        "installed": bool(executable_path),
        "version": version,
        "latest_version": latest,
        "actions": installation_actions(installation),
        "operation": operation_payload,
        "license": definition.license,
        "docs_url": definition.docs_url,
        "login_command": _login_command(definition.login_command, executable_path if installation.source == "managed" else None),
        "update_command": definition.update_command,
        "auth": auth,
    }


@router.get("/agents")
async def list_acp_agents(request: Request, refresh: bool = False, check_updates: bool = False) -> dict[str, Any]:
    container = get_app_container(request)
    return {"agents": await asyncio.gather(*(
        _agent_payload(container, agent_id, refresh=refresh, check_updates=check_updates) for agent_id in AGENT_DEFINITIONS
    ))}


@router.get("/agents/{agent_id}")
async def get_acp_agent(
    agent_id: AgentId, request: Request, refresh: bool = False, agent_name: str | None = None, cwd: str | None = None,
    check_updates: bool = False,
) -> dict[str, Any]:
    try:
        agent_name = normalize_agent_name(agent_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return await _agent_payload(
        get_app_container(request), agent_id, refresh=refresh, agent_name=agent_name, cwd=cwd, check_updates=check_updates,
    )


InstallRevision = Annotated[str | None, Header(alias="If-Match")]


async def _manage_agent(
    request: Request, agent_id: str, action: Literal["install", "update", "uninstall"], revision: str | None,
) -> dict[str, Any]:
    headers = {"Cache-Control": "no-store"}
    if revision is None:
        raise HTTPException(428, "Refresh the ACP installation status before making changes.", headers=headers)
    try:
        operation = await get_acp_install_manager(get_app_container(request)).start(agent_id, action, revision.strip('"'))
    except AcpConflict as exc:
        raise HTTPException(409, str(exc), headers=headers) from None
    except AcpError as exc:
        raise HTTPException(422, str(exc), headers=headers) from None
    return {"operation": operation.payload()}


@router.post("/agents/{agent_id}/install", status_code=202)
async def install_acp_agent(agent_id: AgentId, request: Request, revision: InstallRevision = None) -> dict[str, Any]:
    return await _manage_agent(request, agent_id, "install", revision)


@router.post("/agents/{agent_id}/update", status_code=202)
async def update_acp_agent(agent_id: AgentId, request: Request, revision: InstallRevision = None) -> dict[str, Any]:
    return await _manage_agent(request, agent_id, "update", revision)


@router.delete("/agents/{agent_id}", status_code=202)
async def uninstall_acp_agent(agent_id: AgentId, request: Request, revision: InstallRevision = None) -> dict[str, Any]:
    return await _manage_agent(request, agent_id, "uninstall", revision)
