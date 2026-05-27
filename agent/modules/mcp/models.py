"""Domain models for MCP servers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class MCPTransport(StrEnum):
    """Supported MCP transports."""

    STDIO = "stdio"
    HTTP = "streamable_http"


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    transport: MCPTransport
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class MCPToolInfo:
    """Lightweight metadata about a tool exposed by an MCP server."""

    name: str
    prefixed_name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class MCPServerStatus:
    """Status snapshot for an MCP server, used by the dashboard."""

    name: str
    transport: str
    enabled: bool
    loaded: bool
    tool_count: int
    tools: tuple[MCPToolInfo, ...] = ()
    error: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MCPTestResult:
    """Outcome of a one-off MCP connection test."""

    ok: bool
    tools: tuple[MCPToolInfo, ...] = ()
    error: str = ""


__all__ = [
    "MCPServerConfig",
    "MCPServerStatus",
    "MCPTestResult",
    "MCPToolInfo",
    "MCPTransport",
]
