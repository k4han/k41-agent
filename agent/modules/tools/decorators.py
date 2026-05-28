"""Decorator-based registration for built-in tools.

Applying ``@register_tool`` to a ``BaseTool`` attaches metadata and records
the tool in a module-level pending list. A ``BuiltinToolSource`` later walks
the tool packages, imports each module, then drains this list into typed
``ToolDescriptor`` objects.

Tools live as the result of the LangChain ``@tool`` decorator (or
``StructuredTool.from_function``), so this decorator must operate on the
``BaseTool`` instance returned, not on the underlying function.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from langchain_core.tools import BaseTool

from agent.modules.tools.domain import ToolCapability, ToolCategory

META_ATTR = "__kaka_tool_meta__"


@dataclass(frozen=True)
class PendingToolMeta:
    """Metadata captured by ``@register_tool`` before a descriptor is built."""

    category: ToolCategory
    capabilities: frozenset[ToolCapability]
    tags: frozenset[str]
    explicit_id: str | None
    version: str


_PENDING: list[tuple[BaseTool, PendingToolMeta]] = []
_REGISTERED_TOOL_IDS: set[int] = set()


def register_tool(
    *,
    category: ToolCategory,
    capabilities: Iterable[ToolCapability] = (),
    tags: Iterable[str] = (),
    id: str | None = None,
    version: str = "1.0.0",
    apply_middleware: bool = True,
) -> Callable[[BaseTool], BaseTool]:
    """Mark a ``BaseTool`` instance for built-in registration.

    When ``apply_middleware`` is True (default) the project's default middleware
    chain is applied immediately so error normalization works regardless of
    whether the registry has been initialized.
    """

    def decorator(tool_obj: BaseTool) -> BaseTool:
        if not isinstance(tool_obj, BaseTool):
            raise TypeError(
                "register_tool must be applied to a BaseTool instance "
                "(the result of @tool or StructuredTool.from_function); "
                f"got {type(tool_obj).__name__!r} instead."
            )
        meta = PendingToolMeta(
            category=category,
            capabilities=frozenset(capabilities),
            tags=frozenset(tags),
            explicit_id=id,
            version=version,
        )
        setattr(tool_obj, META_ATTR, meta)
        if builtins_id(tool_obj) not in _REGISTERED_TOOL_IDS:
            _PENDING.append((tool_obj, meta))
            _REGISTERED_TOOL_IDS.add(builtins_id(tool_obj))
        if apply_middleware:
            # local import to avoid circular dependency at module-import time
            from agent.modules.tools.middleware import apply_default_middleware

            apply_default_middleware(tool_obj)
        return tool_obj

    return decorator


def builtins_id(obj: object) -> int:
    """Stable identity for dedup; isolated for testability."""
    return id(obj)


def get_pending_registrations() -> list[tuple[BaseTool, PendingToolMeta]]:
    """Snapshot of all tools registered so far via ``@register_tool``."""
    return list(_PENDING)


def clear_pending_registrations() -> None:
    """Reset registration state. Intended for tests only."""
    _PENDING.clear()
    _REGISTERED_TOOL_IDS.clear()


__all__ = [
    "META_ATTR",
    "PendingToolMeta",
    "clear_pending_registrations",
    "get_pending_registrations",
    "register_tool",
]
