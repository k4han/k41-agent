from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agent.delivery.http.common.mcp import InstallRepository
from agent.modules.mcp import (
    MCPServerConfig,
    MCPServerStatus,
    MCPTestResult,
    MCPTransport,
    McpInstallRepository,
    list_mcp_server_status,
    reload_mcp_server_tools,
    test_mcp_connection,
)
from agent.modules.tools import reload_mcp_descriptors


router = APIRouter()


_SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


# --- Request bodies ---


class McpServerBody(BaseModel):
    """Base request body for MCP server configuration."""

    transport: str = Field(..., min_length=1, description="Transport type: 'stdio' or 'streamable_http'.")
    command: str = Field(default="", description="Command to run for stdio transport (e.g. 'npx', 'uvx').")
    args: list[str] = Field(default_factory=list, description="Command arguments for stdio transport.")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables for the MCP server process.")
    url: str = Field(default="", description="URL for streamable_http transport.")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP headers for streamable_http transport.")
    enabled: bool = Field(default=True, description="Whether this MCP server is enabled.")


class CreateMcpServerBody(McpServerBody):
    """Request body for creating a new MCP server."""

    name: str = Field(..., min_length=1, description="Unique server name (letters, numbers, underscores, hyphens).")


class TestMcpServerBody(McpServerBody):
    """Request body for testing an MCP server connection without saving."""

    name: str = Field(default="test", description="Name label for the test connection.")


# --- Helpers ---


def _validate_server_name(name: str) -> str:
    value = name.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Server name is required.")
    if not _SERVER_NAME_PATTERN.match(value):
        raise HTTPException(
            status_code=400,
            detail="Server name can only contain letters, numbers, underscores, and hyphens.",
        )
    return value


def _parse_transport(value: str) -> MCPTransport:
    normalized = value.strip().lower()
    if normalized in ("stdio",):
        return MCPTransport.STDIO
    if normalized in ("streamable_http", "http", "https"):
        return MCPTransport.HTTP
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported MCP transport: {value!r}. Use 'stdio' or 'streamable_http'.",
    )


def _validate_body(body: McpServerBody, transport: MCPTransport) -> None:
    if transport == MCPTransport.STDIO and not body.command.strip():
        raise HTTPException(
            status_code=400,
            detail="Command is required for stdio MCP servers.",
        )
    if transport == MCPTransport.HTTP and not body.url.strip():
        raise HTTPException(
            status_code=400,
            detail="URL is required for HTTP MCP servers.",
        )


def _body_to_config(name: str, body: McpServerBody) -> MCPServerConfig:
    transport = _parse_transport(body.transport)
    _validate_body(body, transport)
    return MCPServerConfig(
        name=name,
        transport=transport,
        command=body.command.strip(),
        args=tuple(item.strip() for item in body.args if str(item).strip()),
        env={k: str(v) for k, v in body.env.items() if k.strip()},
        url=body.url.strip(),
        headers={k: str(v) for k, v in body.headers.items() if k.strip()},
        enabled=bool(body.enabled),
    )


def _credential_payload_from_config(config: MCPServerConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if config.env:
        payload["env"] = dict(config.env)
    if config.headers:
        payload["headers"] = dict(config.headers)
    return payload


def _serialize_status(status: MCPServerStatus) -> dict[str, Any]:
    return {
        "name": status.name,
        "transport": status.transport,
        "enabled": status.enabled,
        "loaded": status.loaded,
        "tool_count": status.tool_count,
        "tools": [
            {
                "name": tool.name,
                "prefixed_name": tool.prefixed_name,
                "description": tool.description,
            }
            for tool in status.tools
        ],
        "error": status.error,
        "command": status.command,
        "args": list(status.args),
        "env": dict(status.env),
        "url": status.url,
        "headers": dict(status.headers),
    }


def _serialize_test_result(result: MCPTestResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "error": result.error,
        "tools": [
            {
                "name": tool.name,
                "prefixed_name": tool.prefixed_name,
                "description": tool.description,
            }
            for tool in result.tools
        ],
    }


# --- Routes ---


@router.get("/dashboard-api/mcp/servers")
async def list_dashboard_mcp_servers() -> dict[str, Any]:
    """List all configured MCP servers with their status and available tools."""
    statuses = await list_mcp_server_status()
    return {"servers": [_serialize_status(status) for status in statuses]}


@router.post("/dashboard-api/mcp/servers")
async def create_dashboard_mcp_server(
    body: CreateMcpServerBody,
    repo: InstallRepository,
) -> dict[str, Any]:
    """Create a new custom MCP server configuration."""
    name = _validate_server_name(body.name)
    if repo.get_server_config(name) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"MCP server already exists: {name}.",
        )

    config = _body_to_config(name, body)
    repo.create_custom_server(
        server_name=name,
        config=config,
        credential_payload=_credential_payload_from_config(config),
    )
    await reload_mcp_descriptors()
    return {"status": "created", "name": name}


@router.put("/dashboard-api/mcp/servers/{server_name}")
async def update_dashboard_mcp_server(
    server_name: str,
    body: McpServerBody,
    repo: InstallRepository,
) -> dict[str, Any]:
    """Update an existing MCP server configuration."""
    name = _validate_server_name(server_name)
    config = _body_to_config(name, body)
    if not repo.update_custom_server(
        server_name=name,
        config=config,
        credential_payload=_credential_payload_from_config(config),
    ):
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_name}.")
    await reload_mcp_descriptors()
    return {"status": "updated", "name": name}


@router.delete("/dashboard-api/mcp/servers/{server_name}")
async def delete_dashboard_mcp_server(
    server_name: str,
    repo: InstallRepository,
) -> dict[str, Any]:
    """Delete an MCP server configuration."""
    if not repo.delete_server(server_name):
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_name}.")
    await reload_mcp_descriptors()
    return {"status": "deleted", "name": server_name}


class ToggleMcpServerBody(BaseModel):
    """Request body to enable or disable an MCP server."""

    enabled: bool = Field(..., description="Whether the MCP server should be enabled or disabled.")


@router.post("/dashboard-api/mcp/servers/test")
async def test_dashboard_mcp_server(body: TestMcpServerBody) -> dict[str, Any]:
    """Test an MCP server connection and list its available tools without saving."""
    name = _validate_server_name(body.name or "test")
    config = _body_to_config(name, body)
    result = await test_mcp_connection(config)
    return _serialize_test_result(result)


@router.post("/dashboard-api/mcp/servers/{server_name}/reload")
async def reload_dashboard_mcp_server(server_name: str) -> dict[str, Any]:
    """Reload an MCP server's tools from its source."""
    try:
        status = await reload_mcp_server_tools(server_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize_status(status)


@router.put("/dashboard-api/mcp/servers/{server_name}/toggle")
async def toggle_dashboard_mcp_server(
    server_name: str,
    body: ToggleMcpServerBody,
    repo: InstallRepository,
) -> dict[str, Any]:
    """Toggle an MCP server on or off."""
    if not repo.toggle_server(server_name, body.enabled):
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_name}.")
    await reload_mcp_descriptors()
    return {"status": "updated", "name": server_name, "enabled": body.enabled}
