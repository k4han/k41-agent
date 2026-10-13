"""Tool registry service with container-scoped resolution.

Thin orchestration layer on top of ``ToolRegistry`` that loads built-in tools
via ``BuiltinToolSource`` on first access. Exposes a stable API to the rest of
the codebase so workflow nodes do not need to know about descriptors.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool

from agent.modules.tools.domain import (
    ToolCapability,
    ToolCategory,
    ToolDescriptor,
    ToolSource,
)
from agent.modules.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolRegistryService:
    """Service for managing tool registration and resolution."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry if registry is not None else ToolRegistry()

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def load_descriptors(self, descriptors: list[ToolDescriptor]) -> None:
        for desc in descriptors:
            if desc.id in self._registry:
                continue
            self._registry.add(desc)

    def remove_by_source(self, source: ToolSource) -> None:
        for desc in list(self._registry.find(source=source)):
            self._registry.remove(desc.id)

    def replace_source(self, source: ToolSource, descriptors: list[ToolDescriptor]) -> None:
        """Validate a new source snapshot before replacing the catalog."""
        replacement = ToolRegistry()
        for descriptor in self._registry.all_descriptors():
            if descriptor.source is not source:
                replacement.add(descriptor)
        for descriptor in descriptors:
            if descriptor.source is not source:
                raise ValueError(f"Unexpected tool source: {descriptor.source}")
            replacement.add(descriptor)
        self._registry = replacement

    def get_tool_by_name(self, name: str) -> BaseTool | None:
        return self._registry.get_tool(name)

    def get_all_tools(self) -> list[BaseTool]:
        return self._registry.all_tools()

    def resolve_tools(self, names: list[str]) -> list[BaseTool]:
        return self._registry.resolve(names)

    def get_tool_names(self) -> list[str]:
        return self._registry.names()

    def get_descriptors(self) -> list[ToolDescriptor]:
        return self._registry.all_descriptors()

    def find(
        self,
        *,
        category: ToolCategory | None = None,
        source: ToolSource | None = None,
        capabilities=None,
        any_capabilities=None,
        tags=None,
    ) -> list[ToolDescriptor]:
        return self._registry.find(
            category=category,
            source=source,
            capabilities=capabilities,
            any_capabilities=any_capabilities,
            tags=tags,
        )

    def find_tools(
        self,
        *,
        category: ToolCategory | None = None,
        source: ToolSource | None = None,
        capabilities: list[ToolCapability] | None = None,
        any_capabilities: list[ToolCapability] | None = None,
        tags: list[str] | None = None,
    ) -> list[BaseTool]:
        return [
            d.tool
            for d in self.find(
                category=category,
                source=source,
                capabilities=capabilities,
                any_capabilities=any_capabilities,
                tags=tags,
            )
        ]


def get_registry_service(container=None) -> ToolRegistryService:
    """Return container-scoped tool registry service.

    Loads built-in tools eagerly. MCP tools require an async call to
    :func:`ensure_mcp_loaded` because the MCP service itself is async.
    """
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).tool_registry_service


def _get_mcp_load_lock(active) -> Any:
    getter = getattr(active, "get_mcp_load_lock", None)
    if callable(getter):
        return getter()
    # Fallback for containers without the helper: guard lazy creation so two
    # worker threads share one lock instance.
    guard = getattr(active, "_lazy_lock", None)
    if guard is None:
        import threading

        guard = threading.Lock()
    with guard:
        lock = getattr(active, "_mcp_load_lock", None)
        if lock is None:
            import asyncio

            lock = asyncio.Lock()
            active._mcp_load_lock = lock
    return lock


async def ensure_mcp_loaded(*, force: bool = False, container=None) -> bool:
    """Load MCP-backed tools into the unified registry (idempotent per scope)."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container(container)
    async with _get_mcp_load_lock(active):
        if active._mcp_loaded and not force:
            return True
        return await _load_mcp_snapshot(active)


async def _load_mcp_snapshot(container) -> bool:
    """Replace a snapshot while the caller owns the container load lock."""
    from agent.modules.tools.sources.mcp import McpToolSource, _extract_server_name

    try:
        mcp_service = container.mcp_service
        descriptors = await McpToolSource(mcp_service).aload()
        failed_servers = getattr(mcp_service, "failed_server_names", frozenset())
        registry_service = get_registry_service(container=container)
        descriptors.extend(
            descriptor for descriptor in registry_service.find(source=ToolSource.MCP)
            if _extract_server_name(descriptor.name) in failed_servers
        )
        registry_service.replace_source(ToolSource.MCP, descriptors)
    except Exception:
        container._mcp_loaded = False
        logger.warning("Failed to refresh MCP tools; preserving the previous catalog", exc_info=True)
        return False
    container._mcp_loaded = not failed_servers
    return container._mcp_loaded


async def reload_mcp_descriptors(container=None) -> None:
    """Reload MCP service state and replace MCP descriptors in the registry."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container(container)
    async with _get_mcp_load_lock(active):
        active.mcp_service.reload()
        active._mcp_loaded = False
        await _load_mcp_snapshot(active)


def _reset_registry_service_for_tests() -> None:
    """Test-only helper to force re-initialization."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container()
    active._tool_registry_service = None
    active._mcp_loaded = False
