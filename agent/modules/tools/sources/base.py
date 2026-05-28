"""Protocol for tool source adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent.modules.tools.domain import ToolDescriptor


@runtime_checkable
class ToolSourceAdapter(Protocol):
    """A loader that produces ``ToolDescriptor`` items for the registry."""

    name: str

    def load(self) -> list[ToolDescriptor]:
        """Return all descriptors known by this source."""
        ...


__all__ = ["ToolSourceAdapter"]
