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
from agent.modules.mcp.db_models import AgentMCPInstall, MCPCredential, MCPServerInstall
from agent.modules.mcp.install_repository import McpInstallRepository
from agent.modules.mcp.installer import McpMarketplaceService, McpInstallError
from agent.modules.mcp.migrations import migrate_mcp_tables
from agent.modules.mcp.registry_client import McpRegistryClient


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


def list_agent_mcp_server_names(agent_name: str) -> list[str]:
    repo = McpInstallRepository()
    try:
        return repo.list_agent_server_names(agent_name)
    finally:
        repo.close()


def list_agent_mcp_installs(agent_name: str) -> list[dict[str, object]]:
    repo = McpInstallRepository()
    try:
        return repo.list_agent_installs(agent_name)
    finally:
        repo.close()


def list_all_agent_mcp_installs() -> dict[str, list[dict[str, object]]]:
    repo = McpInstallRepository()
    try:
        return repo.list_all_agent_installs()
    finally:
        repo.close()


def list_mcp_installs() -> list[dict[str, object]]:
    repo = McpInstallRepository()
    try:
        return repo.list_all_installs()
    finally:
        repo.close()


__all__ = [
    "AgentMCPInstall",
    "ConfigMcpServerRepository",
    "MCPService",
    "MCPCredential",
    "MCPServerConfig",
    "MCPServerInstall",
    "MCPServerStatus",
    "MCPTestResult",
    "MCPToolInfo",
    "MCPTransport",
    "McpInstallError",
    "McpInstallRepository",
    "McpMarketplaceService",
    "McpRegistryClient",
    "POPULAR_MCP_SERVERS",
    "PopularMcpEnvField",
    "PopularMcpServer",
    "migrate_mcp_tables",
    "parse_mcp_server_key",
    "reload_mcp_service",
    "list_agent_mcp_installs",
    "list_agent_mcp_server_names",
    "list_all_agent_mcp_installs",
    "list_mcp_installs",
    "list_mcp_servers",
    "get_mcp_server",
    "list_mcp_server_status",
    "get_all_mcp_tools",
    "get_mcp_server_tools",
    "reload_mcp_server_tools",
    "test_mcp_connection",
]
