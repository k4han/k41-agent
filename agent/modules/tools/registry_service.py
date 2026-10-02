"""Tool registry service with container-scoped resolution.

Thin orchestration layer on top of ``ToolRegistry`` that loads built-in tools
via ``BuiltinToolSource`` on first access. Exposes a stable API to the rest of
the codebase so workflow nodes do not need to know about descriptors.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from agent.modules.tools.domain import (
    ToolCapability,
    ToolCategory,
    ToolDescriptor,
    ToolSource,
)
from agent.modules.tools.registry import ToolRegistry


class ToolRegistryService:
    """Service for managing tool registration and resolution."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry or ToolRegistry()

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


async def ensure_mcp_loaded(*, force: bool = False, container=None) -> None:
    """Load MCP-backed tools into the unified registry (idempotent per scope)."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container(container)
    if bool(getattr(active, "_mcp_loaded", False)) and not force:
        return
    from agent.modules.tools.sources.mcp import McpToolSource

    service = get_registry_service(container=active)
    if force:
        service.remove_by_source(ToolSource.MCP)
    descriptors = await McpToolSource().load()
    service.load_descriptors(descriptors)
    active._mcp_loaded = True


async def reload_mcp_descriptors(container=None) -> None:
    """Reload MCP service state and replace MCP descriptors in the registry."""
    from agent.bootstrap.container import require_active_container

    from agent.modules.mcp import reload_mcp_service

    active = require_active_container(container)
    reload_mcp_service()
    active._mcp_loaded = False
    await ensure_mcp_loaded(force=True, container=active)


def _reset_registry_service_for_tests() -> None:
    """Test-only helper to force re-initialization."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container()
    active._tool_registry_service = None
    active._mcp_loaded = False
