"""Resolve which machine a tool call runs on.

A conversation may use its main workspace and the auxiliary machines that its
agents created (recorded in ``thread_machines`` under the root conversation, so
sub-agents share them). Every tool that accepts a machine goes through
``resolve_target`` so that machines of other conversations are refused.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from agent.modules.tools.result import ToolError, ToolErrorCode

TARGET_FIELD_DESCRIPTION = (
    "ID of an auxiliary machine created in this conversation. Omit to use the main workspace."
)


@dataclass(frozen=True)
class ExecutionTarget:
    backend: str
    locator: str
    root: str
    auxiliary: bool
    record_id: int | None = None


def _thread_id(runtime: Any) -> str:
    from agent.modules.tools.runtime.context import get_thread_id

    return get_thread_id(getattr(runtime, "config", None)) or ""


def main_target(runtime: Any) -> ExecutionTarget:
    from agent.modules.tools.builtin.workspace import get_workspace

    workspace = get_workspace(runtime)
    if workspace.backend == "local":
        root = os.path.realpath(workspace.locator)
    else:
        root = str(workspace.metadata.get("root") or "")
    return ExecutionTarget(workspace.backend, workspace.locator, root, False)


async def resolve_target(runtime: Any, target: str | None, *, backend: str | None = None) -> ExecutionTarget:
    """Return the main workspace for an empty target, else an owned auxiliary machine."""
    main = main_target(runtime)
    value = str(target or "").strip()
    if not value or (value == main.locator and (backend is None or backend == main.backend)):
        return main
    from agent.modules.workspaces import find_thread_resource, touch_thread_resource

    thread_id = _thread_id(runtime)
    record = None
    if thread_id:
        try:
            record = await find_thread_resource(thread_id, value, backend=backend)
        except Exception:
            record = None
    if record is None:
        raise ToolError(
            ToolErrorCode.PERMISSION_DENIED,
            f"Machine {value} is neither the current workspace nor an auxiliary machine of this conversation.",
        )
    try:
        await touch_thread_resource(record)
    except Exception:
        pass
    return ExecutionTarget(record.backend, record.locator, record.root, True, record.id)


async def owned_targets(runtime: Any, *, backend: str | None = None) -> list[ExecutionTarget]:
    """The main workspace (when it matches ``backend``) followed by owned auxiliary machines."""
    from agent.modules.workspaces import list_thread_resources

    main = main_target(runtime)
    targets = [main] if backend is None or main.backend == backend else []
    thread_id = _thread_id(runtime)
    if thread_id:
        for record in await list_thread_resources(thread_id, backend=backend):
            targets.append(ExecutionTarget(record.backend, record.locator, record.root, True, record.id))
    return targets


async def forget_machine(backend: str, locator: str, *, deleted: bool = False) -> None:
    """Drop cached runtimes after a machine stops or disappears.

    A stopped machine loses its coding worker, so a cached client would report
    a disconnected runtime instead of bootstrapping a new one.
    """
    from agent.modules.tools.coding.service import active_coding_service
    from agent.modules.workspaces import invalidate_workspace_computer

    coding = active_coding_service()
    if coding is not None:
        coding.remote.forget(backend, locator)
    await invalidate_workspace_computer(backend, locator)
    if deleted:
        from agent.modules.workspaces import mark_resource_deleted

        try:
            await mark_resource_deleted(backend, locator)
        except Exception:
            pass


__all__ = [
    "ExecutionTarget",
    "TARGET_FIELD_DESCRIPTION",
    "forget_machine",
    "main_target",
    "owned_targets",
    "resolve_target",
]
