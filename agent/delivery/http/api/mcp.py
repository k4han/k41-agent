from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agent.delivery.http.common.mcp import InstallRepository
from agent.modules.mcp import McpInstallError, McpMarketplaceService
from agent.modules.tools import reload_mcp_descriptors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])


class McpInstallBody(BaseModel):
    """Request body for installing an MCP server from the registry."""

    agent_name: str = Field(..., min_length=1, description="Target agent name to install the MCP server for.")
    registry_name: str = Field(..., min_length=1, description="Full registry name (namespace/name) of the MCP server.")
    version: str = Field(default="latest", description="Version to install. Use 'latest' for the most recent version.")
    target_id: str = Field(default="", description="Optional target ID for the install.")
    server_name: str = Field(default="", description="Custom server name override. Uses registry name if empty.")
    input_values: dict[str, Any] = Field(default_factory=dict, description="Input values to pass to the MCP server configuration.")
    auth_method: str = Field(default="secret", description="Authentication method ('secret', 'oauth', or 'none').")


class ToggleAgentMcpInstallBody(BaseModel):
    """Request body to enable or disable an MCP install for an agent."""

    enabled: bool = Field(..., description="Whether the MCP install should be enabled or disabled.")


class BindAgentMcpInstallBody(BaseModel):
    """Request body to bind an existing MCP server to an agent."""

    server_name: str = Field(..., min_length=1, description="Name of the MCP server to bind.")
    enabled: bool = Field(default=True, description="Whether the bind should be enabled.")


def _marketplace_service() -> Iterator[McpMarketplaceService]:
    service = McpMarketplaceService()
    try:
        yield service
    finally:
        service.close()


MarketplaceService = Annotated[McpMarketplaceService, Depends(_marketplace_service)]


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, McpInstallError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        detail = exc.response.text or str(exc)
        return HTTPException(status_code=status if status < 500 else 502, detail=detail)
    if isinstance(exc, httpx.HTTPError):
        return HTTPException(status_code=502, detail=str(exc))
    logger.exception("Unexpected error in MCP endpoint")
    return HTTPException(status_code=500, detail="Internal server error.")


@router.get("/search")
async def search_mcp_registry(
    service: MarketplaceService,
    q: str = "",
    cursor: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Search the MCP server registry by keyword."""
    try:
        return await service.search(q, cursor=cursor, limit=limit)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/servers/{namespace}/{name}/versions/{version}")
async def get_mcp_registry_server_by_parts(
    namespace: str,
    name: str,
    version: str,
    service: MarketplaceService,
) -> dict[str, Any]:
    """Get a specific MCP server version by namespace, name, and version."""
    return await get_mcp_registry_server(f"{namespace}/{name}", version, service)


@router.get("/servers/{server_name:path}/versions/{version}")
async def get_mcp_registry_server(
    server_name: str,
    version: str,
    service: MarketplaceService,
) -> dict[str, Any]:
    """Get a specific MCP server version by full server name."""
    try:
        return await service.get_server_version(server_name, version)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/install")
async def install_mcp_server(
    body: McpInstallBody,
    service: MarketplaceService,
) -> dict[str, Any]:
    """Install an MCP server from the registry and bind it to an agent."""
    try:
        result = await service.install(
            agent_name=body.agent_name,
            registry_name=body.registry_name,
            version=body.version,
            target_id=body.target_id,
            server_name=body.server_name,
            input_values=body.input_values,
            auth_method=body.auth_method,
        )
        if result.get("status") == "installed":
            await reload_mcp_descriptors()
        return result
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/agents/{agent_name}/installs")
async def list_agent_mcp_installs(
    agent_name: str,
    repo: InstallRepository,
) -> dict[str, Any]:
    """List all MCP server installs for a specific agent."""
    return {"installs": repo.list_agent_installs(agent_name)}


@router.post("/agents/{agent_name}/installs")
async def bind_agent_mcp_install(
    agent_name: str,
    body: BindAgentMcpInstallBody,
    repo: InstallRepository,
) -> dict[str, Any]:
    """Bind an existing MCP server to an agent."""
    install = repo.bind_agent_server(
        agent_name=agent_name,
        server_name=body.server_name,
        enabled=body.enabled,
    )
    if install is None:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    await reload_mcp_descriptors()
    return {"status": "bound", "install": install}


@router.put("/agents/{agent_name}/installs/{install_id}/toggle")
async def toggle_agent_mcp_install(
    agent_name: str,
    install_id: int,
    body: ToggleAgentMcpInstallBody,
    repo: InstallRepository,
) -> dict[str, Any]:
    """Toggle an MCP server install on or off for an agent."""
    if not repo.toggle_agent_install(agent_name, install_id, body.enabled):
        raise HTTPException(status_code=404, detail="MCP install not found.")
    await reload_mcp_descriptors()
    return {"status": "updated", "install_id": install_id, "enabled": body.enabled}


@router.delete("/agents/{agent_name}/installs/{install_id}")
async def delete_agent_mcp_install(
    agent_name: str,
    install_id: int,
    repo: InstallRepository,
) -> dict[str, Any]:
    """Delete an MCP server install from an agent."""
    if not repo.delete_agent_install(agent_name, install_id):
        raise HTTPException(status_code=404, detail="MCP install not found.")
    await reload_mcp_descriptors()
    return {"status": "deleted", "install_id": install_id}


__all__ = ["router"]
