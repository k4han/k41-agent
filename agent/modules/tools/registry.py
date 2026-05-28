"""Unified tool registry.

Indexes ``ToolDescriptor`` objects by id, name, source, category, and tags so
callers can resolve tools by any of these axes. Concrete tools enter the
registry via ``ToolSource`` adapters (built-in, MCP, future skill source).
"""

from __future__ import annotations

from collections.abc import Iterable

from langchain_core.tools import BaseTool

from agent.modules.tools.domain import (
    ToolCapability,
    ToolCategory,
    ToolDescriptor,
    ToolSource,
)


class ToolRegistry:
    """In-memory catalog of ``ToolDescriptor`` instances."""

    def __init__(self) -> None:
        self._by_id: dict[str, ToolDescriptor] = {}
        self._by_name: dict[str, ToolDescriptor] = {}

    def add(self, descriptor: ToolDescriptor) -> None:
        """Register a descriptor. Duplicate ids raise; duplicate names overwrite."""
        if descriptor.id in self._by_id:
            raise ValueError(f"Tool id already registered: {descriptor.id}")
        self._by_id[descriptor.id] = descriptor
        self._by_name[descriptor.name] = descriptor

    def remove(self, id_or_name: str) -> None:
        desc = self.get(id_or_name)
        if desc is None:
            return
        self._by_id.pop(desc.id, None)
        # only drop by_name if it still maps to this descriptor
        if self._by_name.get(desc.name) is desc:
            self._by_name.pop(desc.name, None)

    def get(self, id_or_name: str) -> ToolDescriptor | None:
        return self._by_id.get(id_or_name) or self._by_name.get(id_or_name)

    def get_tool(self, id_or_name: str) -> BaseTool | None:
        desc = self.get(id_or_name)
        return desc.tool if desc else None

    def all_descriptors(self) -> list[ToolDescriptor]:
        return list(self._by_id.values())

    def all_tools(self) -> list[BaseTool]:
        return [d.tool for d in self._by_id.values()]

    def names(self) -> list[str]:
        return [d.name for d in self._by_id.values()]

    def resolve(self, names: Iterable[str]) -> list[BaseTool]:
        """Return tools matching the given names, in input order, skipping unknown."""
        out: list[BaseTool] = []
        for name in names:
            desc = self.get(name)
            if desc is not None:
                out.append(desc.tool)
        return out

    def find(
        self,
        *,
        category: ToolCategory | None = None,
        source: ToolSource | None = None,
        capabilities: Iterable[ToolCapability] | None = None,
        any_capabilities: Iterable[ToolCapability] | None = None,
        tags: Iterable[str] | None = None,
    ) -> list[ToolDescriptor]:
        """Filter descriptors. All non-None filters must match (AND).

        - ``capabilities``: descriptor must contain ALL listed capabilities.
        - ``any_capabilities``: descriptor must contain at least ONE.
        - ``tags``: descriptor must contain ALL listed tags.
        """
        required_caps = frozenset(capabilities) if capabilities else None
        any_caps = frozenset(any_capabilities) if any_capabilities else None
        required_tags = frozenset(tags) if tags else None

        out: list[ToolDescriptor] = []
        for desc in self._by_id.values():
            if category is not None and desc.category is not category:
                continue
            if source is not None and desc.source is not source:
                continue
            if required_caps is not None and not required_caps.issubset(desc.capabilities):
                continue
            if any_caps is not None and desc.capabilities.isdisjoint(any_caps):
                continue
            if required_tags is not None and not required_tags.issubset(desc.tags):
                continue
            out.append(desc)
        return out

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, id_or_name: object) -> bool:
        if not isinstance(id_or_name, str):
            return False
        return self.get(id_or_name) is not None


__all__ = ["ToolRegistry"]
