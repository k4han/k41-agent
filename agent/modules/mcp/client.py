"""MCP client wrapper — connects to a single MCP server and loads its tools.

We use ``langchain-mcp-adapters`` to convert MCP tools into LangChain tools, then
re-wrap each tool with a canonical ``mcp__<server>__<tool>`` prefix so they are
addressable from agent configs.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.tools import BaseTool

from agent.modules.mcp.models import MCPServerConfig, MCPToolInfo, MCPTransport

logger = logging.getLogger(__name__)

_TOOL_NAME_INVALID = re.compile(r"[^A-Za-z0-9_-]")


def build_connection_payload(config: MCPServerConfig) -> dict[str, Any]:
    """Build the per-server connection dict expected by ``MultiServerMCPClient``."""
    if config.transport == MCPTransport.STDIO:
        if not config.command.strip():
            raise ValueError(
                f"MCP server {config.name!r} uses stdio transport but has no command."
            )
        return {
            "transport": "stdio",
            "command": config.command,
            "args": list(config.args),
            "env": dict(config.env) if config.env else None,
        }

    if config.transport == MCPTransport.HTTP:
        if not config.url.strip():
            raise ValueError(
                f"MCP server {config.name!r} uses HTTP transport but has no URL."
            )
        payload: dict[str, Any] = {
            "transport": "streamable_http",
            "url": config.url,
        }
        if config.headers:
            payload["headers"] = dict(config.headers)
        return payload

    raise ValueError(f"Unsupported MCP transport: {config.transport}")


def _sanitize_server_token(name: str) -> str:
    sanitized = _TOOL_NAME_INVALID.sub("_", name.strip())
    return sanitized or "server"


def prefixed_tool_name(server_name: str, tool_name: str) -> str:
    """Return ``mcp__<server>__<tool>`` ensuring identifier-safe characters."""
    return f"mcp__{_sanitize_server_token(server_name)}__{tool_name}"


def _wrap_tool_with_prefix(tool: BaseTool, server_name: str) -> BaseTool:
    """Return a copy of *tool* whose name is prefixed with the server name.

    Falls back to mutating the tool in place if the LangChain tool model does
    not support ``copy()`` for this attribute.
    """
    new_name = prefixed_tool_name(server_name, tool.name)
    try:
        return tool.model_copy(update={"name": new_name})
    except Exception:
        logger.debug(
            "Falling back to in-place rename for MCP tool %s -> %s",
            tool.name,
            new_name,
        )
        tool.name = new_name  # type: ignore[assignment]
        return tool


async def fetch_tools_for_config(config: MCPServerConfig) -> list[BaseTool]:
    """Connect to a single MCP server and return its tools as LangChain tools."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    connection = build_connection_payload(config)
    client = MultiServerMCPClient({config.name: connection})
    raw_tools = await client.get_tools(server_name=config.name)
    return [_wrap_tool_with_prefix(tool, config.name) for tool in raw_tools]


def _flatten_exception(exc: BaseException) -> list[BaseException]:
    """Flatten ``ExceptionGroup`` (and chained causes) into a list of leaves."""
    flat: list[BaseException] = []
    seen: set[int] = set()

    def visit(node: BaseException) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))

        sub_exceptions = getattr(node, "exceptions", None)
        if sub_exceptions:
            for child in sub_exceptions:
                visit(child)
            return

        flat.append(node)
        if node.__cause__ is not None:
            visit(node.__cause__)
        elif node.__context__ is not None and not node.__suppress_context__:
            visit(node.__context__)

    visit(exc)
    return flat


def format_exception_chain(exc: BaseException) -> str:
    """Produce a single readable message that unwraps ``ExceptionGroup``.

    The ``mcp`` SDK uses ``anyio``/``asyncio.TaskGroup`` extensively, so most
    failures surface as ``unhandled errors in a TaskGroup`` which hides the real
    cause. This helper drills down to the leaf exceptions and renders them as
    ``ClassName: message``.
    """
    leaves = _flatten_exception(exc)
    if not leaves:
        return f"{type(exc).__name__}: {exc}".strip()
    parts: list[str] = []
    for leaf in leaves:
        text = str(leaf).strip()
        parts.append(f"{type(leaf).__name__}: {text}" if text else type(leaf).__name__)
    seen: set[str] = set()
    unique = [part for part in parts if not (part in seen or seen.add(part))]
    return " | ".join(unique)


def tools_to_info(tools: list[BaseTool], server_name: str) -> tuple[MCPToolInfo, ...]:
    infos: list[MCPToolInfo] = []
    prefix = f"mcp__{_sanitize_server_token(server_name)}__"
    for tool in tools:
        prefixed = tool.name
        base = prefixed[len(prefix):] if prefixed.startswith(prefix) else prefixed
        infos.append(
            MCPToolInfo(
                name=base,
                prefixed_name=prefixed,
                description=(tool.description or "").strip(),
            )
        )
    return tuple(infos)


__all__ = [
    "build_connection_payload",
    "fetch_tools_for_config",
    "format_exception_chain",
    "prefixed_tool_name",
    "tools_to_info",
]
