from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.modules.agent_runtime import NotifyChannel
from agent.modules.workspaces import WorkspaceRef
from agent.modules.agent_runtime import get_background_task_manager
from agent.modules.conversations import get_conversation_thread_repository
from agent.modules.workspaces import resolve_workspace_ref


router = APIRouter()


class SubmitTaskBody(BaseModel):
    """Request body for submitting a background coding task."""

    request: str = Field(..., description="Task description or instruction for the agent.")
    agent_name: str = Field(default="default", description="Agent card name to use.")
    workspace: WorkspaceRef | None = Field(default=None, description="Workspace reference. Defaults to the default workspace.")
    notify_platform: str | None = Field(default=None, description="Notification platform (e.g. 'telegram', 'discord').")
    notify_external_id: str | None = Field(default=None, description="External user/channel ID for notifications.")
    notify_channel_id: str | None = Field(default=None, description="Channel ID for notifications.")


@router.get("/tasks/list")
async def list_background_tasks() -> dict[str, Any]:
    """List all background tasks with their current status and thread information."""
    manager = get_background_task_manager()
    tasks = manager.list_all()

    thread_ids = [t["thread_id"] for t in tasks if t.get("thread_id")]
    repo = get_conversation_thread_repository()
    active_threads = await repo.list_active_thread_ids(thread_ids)

    for task in tasks:
        tid = task.get("thread_id")
        task["thread_deleted"] = bool(tid) and tid not in active_threads

    return {"tasks": tasks}


@router.post("/tasks")
async def submit_background_task(body: SubmitTaskBody) -> dict[str, Any]:
    """Submit a background coding task to be executed by an agent."""
    if not body.request.strip():
        raise HTTPException(status_code=400, detail="Request cannot be empty.")

    notify_channel = None
    if body.notify_platform and body.notify_external_id:
        notify_channel = NotifyChannel(
            platform=body.notify_platform,
            external_id=body.notify_external_id,
            channel_id=body.notify_channel_id or body.notify_external_id,
        )

    manager = get_background_task_manager()
    task_id = await manager.submit(
        request=body.request.strip(),
        agent_name=body.agent_name,
        workspace=body.workspace or resolve_workspace_ref(None),
        notify_channel=notify_channel,
    )
    return {"status": "submitted", "task_id": task_id}


@router.post("/tasks/{task_id}/cancel")
async def cancel_background_task(task_id: str) -> dict[str, str]:
    """Cancel a currently running background task."""
    manager = get_background_task_manager()
    result = manager.cancel(task_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    if result == "not_running":
        raise HTTPException(status_code=400, detail="Task is not running.")
    return {"status": "cancelled", "task_id": task_id}


@router.delete("/tasks/{task_id}")
async def remove_background_task(task_id: str) -> dict[str, str]:
    """Remove a background task from the task list. Cannot remove running tasks."""
    manager = get_background_task_manager()
    removed = await manager.remove(task_id)
    if not removed:
        raise HTTPException(
            status_code=400,
            detail=f"Task '{task_id}' not found or still running.",
        )
    return {"status": "removed", "task_id": task_id}
