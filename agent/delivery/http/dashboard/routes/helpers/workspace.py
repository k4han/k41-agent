from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from agent.modules.agent_runtime import get_background_task_manager
from agent.modules.workspaces import (
    UnsupportedWorkspaceCapabilityError,
    WorkspaceRef,
    WorkspaceUnavailableError,
    get_thread_workspace_ref,
    resolve_workspace_ref,
)
from agent.shared.integrations import IntegrationUnavailableError

logger = logging.getLogger(__name__)

NO_WORKSPACE_KEY = "no-workspace"
NO_WORKSPACE_LABEL = "No workspace"


async def workspace_ref_for_thread(
    thread_id: str,
    *,
    include_default: bool = True,
) -> WorkspaceRef | None:
    if thread_id:
        task = get_background_task_manager().get_by_thread_id(thread_id)
        task_workspace = (task or {}).get("workspace")
        if task_workspace:
            return resolve_workspace_ref(task_workspace)

        try:
            stored_workspace = await get_thread_workspace_ref(thread_id)
        except Exception as exc:
            logger.debug(
                "Failed to load workspace for thread %s: %s",
                thread_id,
                exc,
            )
            stored_workspace = None
        if stored_workspace:
            return resolve_workspace_ref(stored_workspace)

    return resolve_workspace_ref(None) if include_default else None


async def workspace_ref_from_request(
    *,
    thread_id: str | None = None,
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    backend: str | None = None,
    locator: str | None = None,
    root: str | None = None,
) -> WorkspaceRef:
    if workspace is not None:
        return resolve_workspace_ref(workspace)
    if locator and locator.strip():
        metadata: dict[str, Any] = {}
        if (backend or "").strip().lower() != "local" and root and root.strip():
            metadata["root"] = root.strip()
        return resolve_workspace_ref(
            {
                "backend": (backend or "local").strip() or "local",
                "locator": locator,
                "metadata": metadata,
            }
        )
    if thread_id and thread_id.strip():
        stored = await workspace_ref_for_thread(thread_id)
        if stored is not None:
            return stored
    return resolve_workspace_ref(None)


def workspace_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, IntegrationUnavailableError):
        return HTTPException(status_code=503, detail=exc.to_dict())
    if isinstance(exc, WorkspaceUnavailableError):
        return HTTPException(status_code=410, detail=str(exc))
    if isinstance(exc, UnsupportedWorkspaceCapabilityError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (NotADirectoryError, ValueError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("Unexpected workspace operation failure.")
    return HTTPException(status_code=500, detail=str(exc))
