"""Tool registry service with singleton pattern.

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
from agent.modules.tools.sources.builtin import BuiltinToolSource

_registry_service: ToolRegistryService | None = None
_mcp_loaded: bool = False


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


def get_registry_service() -> ToolRegistryService:
    """Get the singleton tool registry service.

    Loads built-in tools eagerly. MCP tools require an async call to
    :func:`ensure_mcp_loaded` because the MCP service itself is async.
    """
    global _registry_service
    if _registry_service is None:
        service = ToolRegistryService()
        service.load_descriptors(BuiltinToolSource().load())
        _registry_service = service
    return _registry_service


async def ensure_mcp_loaded(*, force: bool = False) -> None:
    """Load MCP-backed tools into the unified registry (idempotent)."""
    global _mcp_loaded
    if _mcp_loaded and not force:
        return
    from agent.modules.tools.sources.mcp import McpToolSource

    service = get_registry_service()
    descriptors = await McpToolSource().load()
    service.load_descriptors(descriptors)
    _mcp_loaded = True


def _reset_registry_service_for_tests() -> None:
    """Test-only helper to force re-initialization."""
    global _registry_service, _mcp_loaded
    _registry_service = None
    _mcp_loaded = False
