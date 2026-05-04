"""Tool registry service with singleton pattern."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from agent.modules.tools.catalog import ToolCatalog

_registry_service: ToolRegistryService | None = None


class ToolRegistryService:
    """Service for managing tool registration and resolution."""

    def __init__(self) -> None:
        self._catalog = ToolCatalog()
        self._initialized = False

    def initialize(self, tools: list[BaseTool]) -> None:
        """Initialize the catalog with tools."""
        if self._initialized:
            return
        for tool in tools:
            self._catalog.add_tool(tool)
        self._initialized = True

    def get_tool_by_name(self, name: str) -> BaseTool | None:
        """Get a tool by name."""
        return self._catalog.get_tool(name)  # type: ignore[return-value]

    def get_all_tools(self) -> list[BaseTool]:
        """Return all tools."""
        return self._catalog.get_all_tools()  # type: ignore[return-value]

    def resolve_tools(self, names: list[str]) -> list[BaseTool]:
        """Resolve tools by names."""
        return self._catalog.resolve_tools(names)  # type: ignore[return-value]

    def get_tool_names(self) -> list[str]:
        """Return all tool names."""
        return self._catalog.get_tool_names()


def get_registry_service() -> ToolRegistryService:
    """Get the singleton tool registry service."""
    global _registry_service
    if _registry_service is None:
        _registry_service = ToolRegistryService()
        # Lazy initialization will happen on first use
        from agent.modules.tools.langchain.registry import (
            get_all_langchain_tools,
        )

        _registry_service.initialize(get_all_langchain_tools())
    return _registry_service
