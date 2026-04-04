"""Base protocols and interfaces for tools."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ToolProtocol(Protocol):
    """Protocol defining the interface for a tool."""

    @property
    def name(self) -> str:
        """Return the tool's name."""
        ...

    @property
    def description(self) -> str:
        """Return the tool's description."""
        ...
