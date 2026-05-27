"""Public interface for the MCP (Model Context Protocol) module.

Other modules should import from here, not from internal packages.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from agent.modules.mcp.catalog import (
    POPULAR_MCP_SERVERS,
    PopularMcpEnvField,
    PopularMcpServer,
)
from agent.modules.mcp.models import (
    MCPServerConfig,
    MCPServerStatus,
    MCPTestResult,
    MCPToolInfo,
    MCPTransport,
)
from agent.modules.mcp.repository import (
    ConfigMcpServerRepository,
    parse_mcp_server_key,
)
from agent.modules.mcp.service import MCPService


_mcp_service: MCPService | None = None


def _get_mcp_service() -> MCPService:
    global _mcp_service
    if _mcp_service is None:
        _mcp_service = MCPService(repository=ConfigMcpServerRepository())
    return _mcp_service


def reload_mcp_service() -> None:
    """Reload MCP server configs and invalidate cached tools."""
    service = _get_mcp_service()
    service.reload()


def list_mcp_servers() -> list[MCPServerConfig]:
    return _get_mcp_service().list_servers()


def get_mcp_server(name: str) -> MCPServerConfig:
    return _get_mcp_service().get_server(name)


async def list_mcp_server_status() -> list[MCPServerStatus]:
    return await _get_mcp_service().list_status()


async def get_all_mcp_tools() -> list[BaseTool]:
    return await _get_mcp_service().get_all_tools()


async def get_mcp_server_tools(name: str) -> list[BaseTool]:
    return await _get_mcp_service().get_tools_for(name)


async def reload_mcp_server_tools(name: str) -> MCPServerStatus:
    return await _get_mcp_service().reload_server_tools(name)


async def test_mcp_connection(config: MCPServerConfig) -> MCPTestResult:
    return await _get_mcp_service().test_connection(config)


__all__ = [
    "ConfigMcpServerRepository",
    "MCPService",
    "MCPServerConfig",
    "MCPServerStatus",
    "MCPTestResult",
    "MCPToolInfo",
    "MCPTransport",
    "POPULAR_MCP_SERVERS",
    "PopularMcpEnvField",
    "PopularMcpServer",
    "parse_mcp_server_key",
    "reload_mcp_service",
    "list_mcp_servers",
    "get_mcp_server",
    "list_mcp_server_status",
    "get_all_mcp_tools",
    "get_mcp_server_tools",
    "reload_mcp_server_tools",
    "test_mcp_connection",
]
