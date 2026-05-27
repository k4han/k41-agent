"""MCP service — manages on-demand caching of MCP server tools."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from langchain_core.tools import BaseTool

from agent.modules.mcp.client import (
    fetch_tools_for_config,
    format_exception_chain,
    tools_to_info,
)
from agent.modules.mcp.models import (
    MCPServerConfig,
    MCPServerStatus,
    MCPTestResult,
    MCPToolInfo,
)
from agent.modules.mcp.repository import ConfigMcpServerRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ServerCacheEntry:
    """Per-server cache holding loaded tools and last error/status."""

    tools: list[BaseTool]
    tool_infos: tuple[MCPToolInfo, ...]
    error: str = ""

    @property
    def loaded(self) -> bool:
        return not self.error


class MCPService:
    """Central service that resolves MCP tools on demand and caches them."""

    def __init__(self, repository: ConfigMcpServerRepository) -> None:
        self._repository = repository
        self._cache: dict[str, _ServerCacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    # --- Public API ---

    def reload(self) -> None:
        """Drop config cache and any loaded tools (used after config edits)."""
        self._repository.reload()
        self._cache.clear()
        self._locks.clear()

    def list_servers(self) -> list[MCPServerConfig]:
        return self._repository.list_servers()

    def get_server(self, name: str) -> MCPServerConfig:
        return self._repository.get_server(name)

    async def get_tools_for(self, name: str) -> list[BaseTool]:
        config = self._repository.get_server(name)
        if not config.enabled:
            return []
        entry = await self._ensure_loaded(config)
        return list(entry.tools) if entry.loaded else []

    async def get_all_tools(self) -> list[BaseTool]:
        await self.ensure_loaded()
        tools: list[BaseTool] = []
        for entry in self._cache.values():
            if entry.loaded:
                tools.extend(entry.tools)
        return tools

    async def ensure_loaded(self) -> None:
        """Load all enabled servers' tools (cache hits short-circuit)."""
        configs = [config for config in self.list_servers() if config.enabled]
        if not configs:
            return
        await asyncio.gather(
            *(self._ensure_loaded(config) for config in configs),
            return_exceptions=False,
        )

    async def list_status(self) -> list[MCPServerStatus]:
        statuses: list[MCPServerStatus] = []
        for config in self.list_servers():
            entry = self._cache.get(config.name)
            statuses.append(
                MCPServerStatus(
                    name=config.name,
                    transport=str(config.transport),
                    enabled=config.enabled,
                    loaded=bool(entry and entry.loaded),
                    tool_count=len(entry.tools) if entry else 0,
                    tools=entry.tool_infos if entry else (),
                    error=entry.error if entry else "",
                    command=config.command,
                    args=config.args,
                    env=config.env,
                    url=config.url,
                    headers=config.headers,
                )
            )
        return statuses

    async def reload_server_tools(self, name: str) -> MCPServerStatus:
        config = self._repository.get_server(name)
        self._cache.pop(config.name, None)
        if not config.enabled:
            return MCPServerStatus(
                name=config.name,
                transport=str(config.transport),
                enabled=False,
                loaded=False,
                tool_count=0,
                command=config.command,
                args=config.args,
                env=config.env,
                url=config.url,
                headers=config.headers,
            )
        entry = await self._ensure_loaded(config)
        return MCPServerStatus(
            name=config.name,
            transport=str(config.transport),
            enabled=config.enabled,
            loaded=entry.loaded,
            tool_count=len(entry.tools),
            tools=entry.tool_infos,
            error=entry.error,
            command=config.command,
            args=config.args,
            env=config.env,
            url=config.url,
            headers=config.headers,
        )

    async def test_connection(self, config: MCPServerConfig) -> MCPTestResult:
        try:
            tools = await fetch_tools_for_config(config)
        except BaseException as exc:
            message = format_exception_chain(exc)
            logger.warning(
                "MCP test_connection failed for %s: %s",
                config.name,
                message,
            )
            return MCPTestResult(ok=False, error=message)
        return MCPTestResult(
            ok=True,
            tools=tools_to_info(tools, config.name),
        )

    # --- Internal ---

    def _lock_for(self, name: str) -> asyncio.Lock:
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    async def _ensure_loaded(self, config: MCPServerConfig) -> _ServerCacheEntry:
        cached = self._cache.get(config.name)
        if cached is not None:
            return cached

        async with self._lock_for(config.name):
            cached = self._cache.get(config.name)
            if cached is not None:
                return cached

            try:
                tools = await fetch_tools_for_config(config)
            except BaseException as exc:
                message = format_exception_chain(exc)
                logger.warning(
                    "Failed to load MCP server %s: %s",
                    config.name,
                    message,
                )
                entry = _ServerCacheEntry(
                    tools=[],
                    tool_infos=(),
                    error=message,
                )
            else:
                entry = _ServerCacheEntry(
                    tools=list(tools),
                    tool_infos=tools_to_info(tools, config.name),
                )
            self._cache[config.name] = entry
            return entry


__all__ = ["MCPService"]
