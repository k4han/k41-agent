"""Working directory helpers for LangChain tools."""

from typing import Any

from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.runtime.context import get_context_value


def get_working_dir(runtime: ToolRuntime[Any, Any]) -> str:
    """Return the effective working directory from tool runtime context."""
    return get_context_value(runtime.context, "working_dir", ".")
