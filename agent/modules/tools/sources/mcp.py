"""MCP tool source adapter.

Wraps :class:`agent.modules.mcp.MCPService` so the unified registry can hold
MCP-backed tools alongside built-in ones. MCP tools are loaded asynchronously
because the MCP service itself is async.
"""

from __future__ import annotations

import logging

from langchain_core.tools import BaseTool

from agent.modules.tools.domain import (
    ToolCategory,
    ToolDescriptor,
    ToolSource,
)
from agent.modules.tools.middleware import apply_default_middleware

logger = logging.getLogger(__name__)

MCP_TOOL_PREFIX = "mcp__"


class McpToolSource:
    """Async source returning descriptors for all enabled MCP servers."""

    name = "mcp"

    def __init__(self) -> None:
        self._descriptors: list[ToolDescriptor] | None = None

    async def load(self) -> list[ToolDescriptor]:
        if self._descriptors is None:
            self._descriptors = await self._build()
        return list(self._descriptors)

    async def reload(self) -> list[ToolDescriptor]:
        self._descriptors = None
        return await self.load()

    async def _build(self) -> list[ToolDescriptor]:
        # local import to avoid loading MCP machinery during built-in setup
        from agent.modules.mcp import get_all_mcp_tools

        try:
            tools = await get_all_mcp_tools()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load MCP tools: %s", exc)
            return []

        descriptors: list[ToolDescriptor] = []
        seen_ids: set[str] = set()
        for raw_tool in tools:
            if not isinstance(raw_tool, BaseTool):
                continue
            tool_name = raw_tool.name
            server_name = _extract_server_name(tool_name)
            desc_id = f"{ToolSource.MCP.value}.{server_name}.{tool_name}"
            if desc_id in seen_ids:
                continue
            seen_ids.add(desc_id)
            apply_default_middleware(raw_tool)
            descriptors.append(
                ToolDescriptor(
                    id=desc_id,
                    name=tool_name,
                    description=raw_tool.description or "",
                    source=ToolSource.MCP,
                    category=ToolCategory.UNKNOWN,
                    tool=raw_tool,
                    tags=frozenset({"mcp", server_name}),
                    args_schema=getattr(raw_tool, "args_schema", None),
                )
            )
        return descriptors


def _extract_server_name(tool_name: str) -> str:
    """Best-effort guess of the MCP server name from the tool prefix.

    Examples:
        ``mcp__github__create_issue`` -> ``github``
        ``mcp__custom-tool``           -> ``unknown``
    """
    if not tool_name.startswith(MCP_TOOL_PREFIX):
        return "unknown"
    rest = tool_name[len(MCP_TOOL_PREFIX):]
    parts = rest.split("__", 1)
    if len(parts) >= 2 and parts[0]:
        return parts[0]
    return "unknown"


__all__ = ["MCP_TOOL_PREFIX", "McpToolSource"]
