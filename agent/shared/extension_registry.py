"""Lightweight extension-point registry.

Provides a central place to list all extension points across the system
without changing the internal logic of any module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExtensionPoint:
    """Describes a single extension slot in the system."""

    module: str  # e.g. "providers", "channels", "workflows", "skills"
    kind: str  # e.g. "factory", "adapter", "graph", "skill"
    name: str  # e.g. "openai_compatible", "telegram", "chat"
    description: str = ""


@dataclass
class ExtensionRegistry:
    """Central catalog of all registered extension points.

    This is intentionally a thin data structure — it records what
    extension points exist, but does NOT own the actual instances.
    """

    _entries: dict[str, list[ExtensionPoint]] = field(default_factory=dict)

    def register(self, ep: ExtensionPoint) -> None:
        """Add an extension point to the registry."""
        self._entries.setdefault(ep.module, []).append(ep)
        logger.debug(
            "Registered extension point: %s/%s/%s", ep.module, ep.kind, ep.name
        )

    def list_all(self) -> dict[str, list[ExtensionPoint]]:
        """Return all entries grouped by module."""
        return dict(self._entries)

    def list_by_module(self, module: str) -> list[ExtensionPoint]:
        """Return extension points for a specific module."""
        return list(self._entries.get(module, []))

    def clear(self) -> None:
        """Remove all entries (useful for tests)."""
        self._entries.clear()


# Module-level singleton
_registry: ExtensionRegistry | None = None


def get_extension_registry() -> ExtensionRegistry:
    """Return the global extension registry singleton."""
    global _registry
    if _registry is None:
        _registry = ExtensionRegistry()
    return _registry


__all__ = ["ExtensionPoint", "ExtensionRegistry", "get_extension_registry"]
