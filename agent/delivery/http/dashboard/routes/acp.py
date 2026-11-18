"""Read-only dashboard status for the externally managed Antigravity CLI."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agent.delivery.http.dashboard.routes.helpers.deps import get_app_container
from agent.modules.acp import AGENT_DEFINITIONS, ANTIGRAVITY_CLI_AGENT_ID, get_acp_auth_manager
from agent.modules.acp.launcher import cli_details, normalize_agent_name
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


async def _agent_payload(
    container: Any, agent_id: str, *, refresh: bool = False, agent_name: str | None = None, cwd: str | None = None,
) -> dict[str, Any]:
    definition = AGENT_DEFINITIONS[agent_id]
    if agent_id == ANTIGRAVITY_CLI_AGENT_ID:
        details = await asyncio.to_thread(cli_details, refresh=refresh)
    else:
        details = await asyncio.to_thread(cli_details, agent_id, refresh=refresh)
    auth = await get_acp_auth_manager(container).status(agent_id, refresh=refresh, agent_name=agent_name, cwd=cwd)
    return {
        "id": agent_id,
        "display_name": definition.display_name,
        "terms_url": definition.terms_url,
        "installation_source": definition.installation_source,
        "executable_path": details["executable_path"],
        "installed": bool(details["executable_path"]),
        "version": details["version"],
        "license": definition.license,
        "docs_url": definition.docs_url,
        "login_command": definition.login_command,
        "update_command": definition.update_command,
        "auth": auth,
    }


@router.get("/agents")
async def list_acp_agents(request: Request, refresh: bool = False) -> dict[str, Any]:
    container = get_app_container(request)
    return {"agents": [await _agent_payload(container, agent_id, refresh=refresh) for agent_id in AGENT_DEFINITIONS]}


@router.get("/agents/{agent_id}")
async def get_acp_agent(
    agent_id: AgentId, request: Request, refresh: bool = False, agent_name: str | None = None, cwd: str | None = None,
) -> dict[str, Any]:
    try:
        agent_name = normalize_agent_name(agent_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return await _agent_payload(get_app_container(request), agent_id, refresh=refresh, agent_name=agent_name, cwd=cwd)
