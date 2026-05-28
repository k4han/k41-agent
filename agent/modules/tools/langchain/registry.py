"""Backward-compatible LangChain tool registry.

The original implementation was a manual list of tool imports. It is now a
thin wrapper around :class:`BuiltinToolSource` so old callers keep working
while new code can use the registry/descriptor API directly.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from agent.modules.tools.sources.builtin import BuiltinToolSource


def get_all_langchain_tools() -> list[BaseTool]:
    """Return all built-in LangChain tools discovered via decorator."""
    return [desc.tool for desc in BuiltinToolSource().load()]


__all__ = ["get_all_langchain_tools"]
