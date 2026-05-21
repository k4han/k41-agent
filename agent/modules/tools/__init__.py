"""Public facade for the tools module.

Other modules should import from here, not from internal packages.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from langchain_core.tools import BaseTool

from agent.modules.tools.registry_service import get_registry_service
from agent.modules.tools.runtime.context import get_context_value
from agent.modules.tools.runtime.path_guard import resolve_safe_path

T = TypeVar("T")


def get_tool_by_name(name: str) -> BaseTool | None:
    """Get a tool instance by its string name."""
    service = get_registry_service()
    return service.get_tool_by_name(name)


def get_default_tools() -> list[BaseTool]:
    """Return the default set of tools."""
    service = get_registry_service()
    return service.get_all_tools()


def resolve_tools(tool_names: Iterable[str]) -> list[BaseTool]:
    """Resolve tools by name, skipping unknown names."""
    service = get_registry_service()
    return service.resolve_tools(list(tool_names))


def get_default_tool_names() -> list[str]:
    """Return the names of all default tools."""
    service = get_registry_service()
    return service.get_tool_names()


def get_runtime_context_value(runtime_or_context, key: str, default: T) -> T:
    """Read a value from runtime context. Public wrapper to avoid importing from infrastructure."""
    return get_context_value(runtime_or_context, key, default)


__all__ = [
    "get_tool_by_name",
    "get_default_tools",
    "resolve_tools",
    "get_default_tool_names",
    "get_runtime_context_value",
    "resolve_safe_path",
]
