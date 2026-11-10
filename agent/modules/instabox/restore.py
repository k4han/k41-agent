"""In-place snapshot restore with workspace locking and cache invalidation.

After a restore the machine's disk is the source of truth: the saved copy is
refreshed from the machine and never pushed back onto it.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from sqlalchemy import select

from agent.modules.instabox.errors import InstaboxConfirmationError, InstaboxConflictError

logger = logging.getLogger(__name__)

INSTABOX_BACKEND = "instabox"
RUNNING_TASK_STATUSES = ("pending", "running", "queued")


async def _project_workspaces(machine_id: str) -> list[tuple[int, int]]:
    from agent.modules.projects import get_project_workspace_service

    records = await get_project_workspace_service().runtime_records(INSTABOX_BACKEND)
    found: set[tuple[int, int]] = set()
    for record in records:
        workspace = record.get("workspace") or {}
        if workspace.get("locator") != machine_id:
            continue
        metadata = workspace.get("metadata") or {}
        if metadata.get("project_id") and metadata.get("project_workspace_id"):
            found.add((int(metadata["project_id"]), int(metadata["project_workspace_id"])))
    return sorted(found, key=lambda item: item[1])


async def _thread_workspaces(machine_id: str) -> list[Any]:
    from agent.modules.workspaces import ThreadWorkspace
    from agent.shared.infrastructure.db import get_async_session_maker

    async with get_async_session_maker()() as session:
        rows = (await session.execute(select(ThreadWorkspace).where(
            ThreadWorkspace.execution_backend == INSTABOX_BACKEND,
            ThreadWorkspace.execution_locator == machine_id,
        ))).scalars().all()
        return list(rows)


async def _running_tasks(machine_id: str) -> int:
    from agent.modules.agent_runtime import BackgroundTaskRecord
    from agent.shared.infrastructure.db import get_async_session_maker

    async with get_async_session_maker()() as session:
        rows = (await session.execute(select(BackgroundTaskRecord.id).where(
            BackgroundTaskRecord.execution_backend == INSTABOX_BACKEND,
            BackgroundTaskRecord.execution_locator == machine_id,
            BackgroundTaskRecord.status.in_(RUNNING_TASK_STATUSES),
        ))).all()
        return len(rows)


async def _running_processes(machine_id: str, roots: list[str]) -> int:
    from agent.modules.tools import get_coding_service
    from agent.modules.workspaces import WorkspaceRef

    remote = get_coding_service().remote
    running = 0
    for root in roots or [""]:
        ref = WorkspaceRef(backend=INSTABOX_BACKEND, locator=machine_id, metadata={"root": root} if root else {})
        try:
            running += int(await remote.workspace_processes(ref))
        except Exception as exc:
            raise InstaboxConflictError(
                f"Cannot verify running processes before restore: {exc}", code="process_state_unknown"
            ) from exc
    return running


def _drop_coding_clients(machine_id: str) -> None:
    from agent.modules.tools import get_coding_service

    remote = get_coding_service().remote
    for key in [key for key in remote.clients if len(key) > 1 and key[0] == INSTABOX_BACKEND and key[1] == machine_id]:
        remote.clients.pop(key, None)


async def restore_machine(
    service: Any,
    machine_id: str,
    snapshot_id: str,
    *,
    confirm_machine: str,
    confirm_snapshot: str,
    created_by: str | None = None,
) -> dict[str, Any]:
    if (confirm_machine or "").strip() != machine_id or (confirm_snapshot or "").strip() != snapshot_id:
        raise InstaboxConfirmationError("Confirm the exact machine and snapshot IDs to restore.")
    await service.require_ready_snapshot(snapshot_id)
    active = await service.repository.list_operations(machine_id=machine_id, active_only=True)
    if active:
        raise InstaboxConflictError("Another change is still running on this machine.", code="operation_in_progress")
    if await _running_tasks(machine_id):
        raise InstaboxConflictError("Stop background tasks using this machine before restoring.", code="tasks_running")

    from agent.modules.projects import get_project_workspace_service

    workspaces = get_project_workspace_service()
    project_workspaces = await _project_workspaces(machine_id)
    thread_rows = await _thread_workspaces(machine_id)
    roots: list[str] = []
    for row in thread_rows:
        with contextlib.suppress(Exception):
            metadata = json.loads(row.execution_metadata_json or "{}")
            if metadata.get("root"):
                roots.append(str(metadata["root"]))
    if await _running_processes(machine_id, sorted(set(roots))):
        raise InstaboxConflictError("Stop running processes on this machine before restoring.", code="processes_running")

    # Save the current files first so a mistaken restore can be undone from the saved copy.
    for project_id, workspace_id in project_workspaces:
        await workspaces.checkpoint(project_id, workspace_id)

    async with contextlib.AsyncExitStack() as stack:
        for _project_id, workspace_id in project_workspaces:
            await stack.enter_async_context(workspaces.lock(workspace_id))
        async with service.machine_lock(machine_id):
            record = await service.machine_action(
                machine_id,
                "restore",
                body={"snapshotId": snapshot_id, "desiredState": "running"},
                created_by=created_by,
                wait=True,
            )
        if record.get("status") != "succeeded":
            return record
        await _invalidate_runtime(service, machine_id, project_workspaces, workspaces)

    # The restored machine is now authoritative; refresh the saved copy from it.
    for project_id, workspace_id in project_workspaces:
        try:
            await workspaces.checkpoint(project_id, workspace_id)
        except Exception:
            logger.warning("Saved copy refresh failed after restore workspace=%s", workspace_id, exc_info=True)
    return record


async def _invalidate_runtime(
    service: Any, machine_id: str, project_workspaces: list[tuple[int, int]], workspaces: Any
) -> None:
    from agent.modules.workspaces import WorkspaceRef, invalidate_workspace_metadata_cache

    for project_id, workspace_id in project_workspaces:
        project, workspace, runtime = await workspaces.rows(project_id, workspace_id)
        ref = WorkspaceRef.model_validate_json(runtime.execution_json)
        invalidate_workspace_metadata_cache(ref, root=str(ref.metadata.get("root") or ""))
        # A new generation makes in-flight writes against the old disk fail as stale.
        await workspaces._publish_runtime(project, workspace, ref, runtime.generation + 1, runtime.status)
        workspaces._notify_runtime_changed(project_id, workspace_id)
    _drop_coding_clients(machine_id)
    service.desktop.invalidate(machine_id)


__all__ = ["restore_machine"]
