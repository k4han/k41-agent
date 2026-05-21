"""Working directory helpers for LangChain tools."""

from typing import Any

from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.runtime.context import get_context_value
from agent.modules.workspaces import (
    DEFAULT_LOCAL_WORKSPACE,
    WorkspaceRef,
    get_workspace_backend,
    normalize_workspace_ref,
)


def get_workspace(runtime: ToolRuntime[Any, Any]) -> WorkspaceRef:
    """Return the effective workspace reference from tool runtime context."""
    raw_workspace = get_context_value(runtime.context, "workspace", None)
    raw_working_dir = get_context_value(runtime.context, "working_dir", None)
    return normalize_workspace_ref(
        raw_workspace if raw_workspace is not None else raw_working_dir,
        default_locator=DEFAULT_LOCAL_WORKSPACE,
    )


def get_backend(runtime: ToolRuntime[Any, Any]):
    """Return the workspace backend for a tool runtime."""
    return get_workspace_backend(get_workspace(runtime))


def get_working_dir(runtime: ToolRuntime[Any, Any]) -> str:
    """Return the local path view for prompt/tool compatibility."""
    return get_workspace(runtime).locator
