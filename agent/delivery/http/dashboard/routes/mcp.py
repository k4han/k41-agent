from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agent.delivery.http.dashboard.routes.shared import (
    _delete_config_tree,
    _get_config_service,
    _update_config_settings,
)
from agent.modules.mcp import (
    POPULAR_MCP_SERVERS,
    MCPServerConfig,
    MCPServerStatus,
    MCPTestResult,
    MCPTransport,
    PopularMcpServer,
    list_mcp_server_status,
    parse_mcp_server_key,
    reload_mcp_server_tools,
    reload_mcp_service,
    test_mcp_connection,
)


router = APIRouter()


_SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


# --- Request bodies ---


class McpServerBody(BaseModel):
    transport: str = Field(..., min_length=1)
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class CreateMcpServerBody(McpServerBody):
    name: str = Field(..., min_length=1)


class TestMcpServerBody(McpServerBody):
    name: str = "test"


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


def _server_config_keys(name: str) -> dict[str, Any | None]:
    return {
        f"mcp.servers.{name}.transport": None,
        f"mcp.servers.{name}.command": None,
        f"mcp.servers.{name}.args": None,
        f"mcp.servers.{name}.url": None,
        f"mcp.servers.{name}.enabled": None,
    }


def _config_to_flat_updates(config: MCPServerConfig) -> dict[str, Any | None]:
    name = config.name
    values: dict[str, Any | None] = {
        f"mcp.servers.{name}.transport": str(config.transport),
        f"mcp.servers.{name}.enabled": bool(config.enabled),
    }

    if config.transport == MCPTransport.STDIO:
        values[f"mcp.servers.{name}.command"] = config.command
        values[f"mcp.servers.{name}.args"] = list(config.args)
        values[f"mcp.servers.{name}.url"] = None
    else:
        values[f"mcp.servers.{name}.url"] = config.url
        values[f"mcp.servers.{name}.command"] = None
        values[f"mcp.servers.{name}.args"] = None

    for key, value in config.env.items():
        values[f"mcp.servers.{name}.env.{key}"] = value
    for key, value in config.headers.items():
        values[f"mcp.servers.{name}.headers.{key}"] = value
    return values


def _existing_server_names(service) -> dict[str, str]:
    """Return a mapping of normalized-name -> original-name from current config."""
    found: dict[str, str] = {}
    for key in service.get_all().keys():
        parsed = parse_mcp_server_key(key)
        if parsed is None:
            continue
        raw_name, _ = parsed
        normalized = raw_name.strip().lower().replace("-", "_")
        if normalized and normalized not in found:
            found[normalized] = raw_name.strip() or normalized
    return found


def _serialize_popular(server: PopularMcpServer) -> dict[str, Any]:
    return {
        "id": server.id,
        "name": server.name,
        "description": server.description,
        "transport": str(server.transport),
        "command": server.command,
        "args": list(server.args),
        "url": server.url,
        "homepage": server.homepage,
        "env_fields": [
            {
                "key": field.key,
                "label": field.label,
                "description": field.description,
                "required": field.required,
                "secret": field.secret,
            }
            for field in server.env_fields
        ],
    }


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
        "env": status.env,
        "url": status.url,
        "headers": status.headers,
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


@router.get("/dashboard-api/mcp/popular")
async def list_popular_mcp_servers() -> dict[str, Any]:
    return {"servers": [_serialize_popular(server) for server in POPULAR_MCP_SERVERS]}


@router.get("/dashboard-api/mcp/servers")
async def list_dashboard_mcp_servers() -> dict[str, Any]:
    statuses = await list_mcp_server_status()
    return {"servers": [_serialize_status(status) for status in statuses]}


@router.post("/dashboard-api/mcp/servers")
async def create_dashboard_mcp_server(
    body: CreateMcpServerBody,
    request: Request,
) -> dict[str, Any]:
    service = _get_config_service(request)
    name = _validate_server_name(body.name)
    existing = _existing_server_names(service)
    if name.lower().replace("-", "_") in existing:
        raise HTTPException(
            status_code=409,
            detail=f"MCP server already exists: {name}.",
        )

    config = _body_to_config(name, body)
    _update_config_settings(
        service,
        _config_to_flat_updates(config),
        require_writable=True,
    )
    reload_mcp_service()
    return {"status": "created", "name": name}


@router.put("/dashboard-api/mcp/servers/{server_name}")
async def update_dashboard_mcp_server(
    server_name: str,
    body: McpServerBody,
    request: Request,
) -> dict[str, Any]:
    service = _get_config_service(request)
    existing = _existing_server_names(service)
    normalized = server_name.strip().lower().replace("-", "_")
    if normalized not in existing:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_name}.")

    canonical_name = existing[normalized]
    # Drop the entire subtree first so removed env/header keys do not linger.
    _delete_config_tree(service, f"mcp.servers.{canonical_name}")

    config = _body_to_config(canonical_name, body)
    _update_config_settings(
        service,
        _config_to_flat_updates(config),
        require_writable=True,
    )
    reload_mcp_service()
    return {"status": "updated", "name": canonical_name}


@router.delete("/dashboard-api/mcp/servers/{server_name}")
async def delete_dashboard_mcp_server(
    server_name: str,
    request: Request,
) -> dict[str, Any]:
    service = _get_config_service(request)
    existing = _existing_server_names(service)
    normalized = server_name.strip().lower().replace("-", "_")
    if normalized not in existing:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_name}.")

    canonical_name = existing[normalized]
    deleted = _delete_config_tree(service, f"mcp.servers.{canonical_name}")
    if not deleted:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_name}.")
    reload_mcp_service()
    return {"status": "deleted", "name": canonical_name}


class ToggleMcpServerBody(BaseModel):
    enabled: bool


@router.post("/dashboard-api/mcp/servers/test")
async def test_dashboard_mcp_server(body: TestMcpServerBody) -> dict[str, Any]:
    name = _validate_server_name(body.name or "test")
    config = _body_to_config(name, body)
    result = await test_mcp_connection(config)
    return _serialize_test_result(result)


@router.post("/dashboard-api/mcp/servers/{server_name}/reload")
async def reload_dashboard_mcp_server(server_name: str) -> dict[str, Any]:
    try:
        status = await reload_mcp_server_tools(server_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _serialize_status(status)


@router.put("/dashboard-api/mcp/servers/{server_name}/toggle")
async def toggle_dashboard_mcp_server(
    server_name: str,
    body: ToggleMcpServerBody,
    request: Request,
) -> dict[str, Any]:
    service = _get_config_service(request)
    existing = _existing_server_names(service)
    normalized = server_name.strip().lower().replace("-", "_")
    if normalized not in existing:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_name}.")

    canonical_name = existing[normalized]
    _update_config_settings(
        service,
        {f"mcp.servers.{canonical_name}.enabled": bool(body.enabled)},
        require_writable=True,
    )
    reload_mcp_service()
    return {"status": "updated", "name": canonical_name, "enabled": body.enabled}
