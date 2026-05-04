"""Tool catalog domain model."""

from __future__ import annotations

from collections.abc import Iterable

from agent.modules.tools.base import ToolProtocol


class ToolCatalog:
    """Domain model for managing a collection of tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolProtocol] = {}

    def add_tool(self, tool: ToolProtocol) -> None:
        """Add a tool to the catalog."""
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> ToolProtocol | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_all_tools(self) -> list[ToolProtocol]:
        """Return all tools in the catalog."""
        return list(self._tools.values())

    def resolve_tools(self, names: Iterable[str]) -> list[ToolProtocol]:
        """Resolve tools by names, skipping unknown names."""
        tools: list[ToolProtocol] = []
        for name in names:
            tool = self.get_tool(name)
            if tool is not None:
                tools.append(tool)
        return tools

    def get_tool_names(self) -> list[str]:
        """Return all tool names in the catalog."""
        return list(self._tools.keys())
