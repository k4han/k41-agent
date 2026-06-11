"""Domain model for the tool catalog.

These types describe a tool with rich metadata so the catalog can be queried
by category, capability, source, or tag instead of just by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Callable, Literal

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
    from pydantic import BaseModel

ToolConfigValue = str | int | float | bool | None
ToolFactory = Callable[[dict[str, ToolConfigValue]], "BaseTool"]


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
    IMAGE = "image"
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


ToolConfigInputType = Literal["text", "number", "boolean", "password", "select"]


@dataclass(frozen=True)
class ToolConfigField:
    """Single configurable field exposed by a tool."""

    name: str
    input_type: ToolConfigInputType
    label: str
    description: str = ""
    default: ToolConfigValue = None
    required: bool = False
    options: tuple[str, ...] = ()
    secret: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "input_type": self.input_type,
            "label": self.label,
            "description": self.description,
            "default": self.default,
            "required": self.required,
            "options": list(self.options),
            "secret": self.secret,
        }
        for key in ("min", "max", "step"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data


@dataclass(frozen=True)
class ToolConfigSchema:
    """Config schema used by dashboard and runtime materialization."""

    fields: tuple[ToolConfigField, ...] = ()

    def defaults(self) -> dict[str, ToolConfigValue]:
        return {
            field.name: field.default
            for field in self.fields
            if field.default is not None
        }

    def field_map(self) -> dict[str, ToolConfigField]:
        return {field.name: field for field in self.fields}

    def to_dict(self) -> dict[str, Any]:
        return {"fields": [field.to_dict() for field in self.fields]}


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
    config_schema: ToolConfigSchema | None = None
    default_config: dict[str, ToolConfigValue] = field(default_factory=dict)
    factory: ToolFactory | None = None

    def has_capability(self, capability: ToolCapability) -> bool:
        return capability in self.capabilities

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags


__all__ = [
    "ToolCapability",
    "ToolCategory",
    "ToolConfigField",
    "ToolConfigInputType",
    "ToolConfigSchema",
    "ToolConfigValue",
    "ToolDescriptor",
    "ToolFactory",
    "ToolSource",
]
