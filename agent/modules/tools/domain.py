"""Domain model for the tool catalog.

These types describe a tool with rich metadata so the catalog can be queried
by category, capability, source, or tag instead of just by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
    from pydantic import BaseModel


class ToolSource(StrEnum):
    """Origin of a tool definition."""

    BUILTIN = "builtin"
    MCP = "mcp"
    SKILL = "skill"
    AGENT = "agent"


class ToolCategory(StrEnum):
    """Coarse-grained grouping shown to users and used for filtering."""

    FILE = "file"
    SHELL = "shell"
    WEB = "web"
    SCHEDULE = "schedule"
    AGENT = "agent"
    UTILITY = "utility"
    SKILL = "skill"
    UNKNOWN = "unknown"


class ToolCapability(StrEnum):
    """Fine-grained capabilities a tool needs or implies."""

    READ_FS = "read_fs"
    WRITE_FS = "write_fs"
    EXEC_SHELL = "exec_shell"
    NETWORK = "network"
    MUTATES_STATE = "mutates_state"
    REQUIRES_WORKSPACE = "requires_workspace"
    REQUIRES_THREAD = "requires_thread"
    ASYNC_ONLY = "async_only"


@dataclass(frozen=True)
class ToolDescriptor:
    """Metadata wrapper around a concrete tool instance."""

    id: str
    name: str
    description: str
    source: ToolSource
    category: ToolCategory
    tool: "BaseTool"
    capabilities: frozenset[ToolCapability] = field(default_factory=frozenset)
    tags: frozenset[str] = field(default_factory=frozenset)
    version: str = "1.0.0"
    args_schema: type["BaseModel"] | None = None

    def has_capability(self, capability: ToolCapability) -> bool:
        return capability in self.capabilities

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags


__all__ = [
    "ToolCapability",
    "ToolCategory",
    "ToolDescriptor",
    "ToolSource",
]
