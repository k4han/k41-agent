"""Centralised tool registry -- all available tools by name."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from agent.modules.workflows.infrastructure.langgraph.tools.chat import echo, get_current_time
from agent.modules.workflows.infrastructure.langgraph.tools.common import (
    list_files,
    read_file,
    run_bash,
    write_file,
)
from agent.modules.workflows.infrastructure.langgraph.tools.skills import skill

# Global tool catalog (without call_agent -- that one needs caller binding at runtime).
_TOOL_CATALOG: dict[str, BaseTool] = {
    tool.name: tool  # type: ignore[arg-type]
    for tool in (get_current_time, echo, skill, read_file, write_file, run_bash, list_files)
}


def get_tool_by_name(name: str) -> BaseTool | None:
    """Get a tool instance by its string name."""
    return _TOOL_CATALOG.get(name)


def get_default_tools() -> list[BaseTool]:
    """Return the default set of tools."""
    return list(_TOOL_CATALOG.values())


def get_default_tool_names() -> list[str]:
    """Return the names of all default tools."""
    return list(_TOOL_CATALOG.keys())
