"""Workspace helpers for built-in tools."""

from typing import Any

from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.runtime.context import ToolContext, get_context_value
from agent.modules.workspaces import (
    WorkspaceFileIO,
    WorkspaceRef,
    get_workspace_file_io,
    normalize_workspace_ref,
)


def get_workspace(runtime: ToolRuntime[Any, Any]) -> WorkspaceRef:
    """Return the effective workspace reference from tool runtime context."""
    raw_workspace = get_context_value(runtime.context, "workspace", None)
    raw_working_dir = get_context_value(runtime.context, "working_dir", None)
    from agent.shared.config.service import get_config_service
    default_locator = str(get_config_service().get_path("workspace.root", "~/k41-agent"))
    return normalize_workspace_ref(
        raw_workspace if raw_workspace is not None else raw_working_dir,
        default_locator=default_locator,
    )


async def get_file_io(runtime: ToolRuntime[Any, Any]) -> WorkspaceFileIO:
    """Return the workspace file I/O capability for a tool runtime."""
    tool_context = ToolContext.from_runtime(runtime)
    return await get_workspace_file_io(
        get_workspace(runtime),
        thread_id=tool_context.thread_id,
    )


def get_working_dir(runtime: ToolRuntime[Any, Any]) -> str:
    """Return the physical workspace path for prompt/tool compatibility.

    For Daytona/Modal/OpenShell sandboxes the workspace ``locator`` is a sandbox ID and
    is not a usable filesystem path. Prefer ``metadata["root"]`` so the value
    reflects the actual cwd used by the backend (which may sit inside a
    cloned repository).
    """
    workspace = get_workspace(runtime)
    if workspace.backend in {"daytona", "modal", "openshell"}:
        root = str(workspace.metadata.get("root") or "").strip()
        if root:
            return root
    return workspace.locator
