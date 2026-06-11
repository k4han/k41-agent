from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.delivery.http.dashboard.routes.helpers.sse import (
    SSE_HEARTBEAT_SECONDS,
    active_session_for_thread,
    is_active_background_task,
    sse_event,
)
from agent.delivery.http.dashboard.routes.helpers.workspace import (
    NO_WORKSPACE_KEY,
    NO_WORKSPACE_LABEL,
    workspace_ref_for_thread,
)
from agent.modules.agent_runtime import get_background_task_manager, get_background_task_repository
from agent.modules.conversations import (
    CheckpointNotFoundError,
    ConversationHistoryUnavailableError,
    THREAD_KIND_BACKGROUND,
    get_checkpoint_stats,
    get_conversation_thread,
    get_thread_messages,
    get_thread_messages_payload,
    list_background_threads_with_stats,
    list_user_threads_with_stats,
    mark_conversation_thread_deleted,
    parse_thread_metadata,
    rename_conversation_thread,
)
from agent.modules.tools import close_thread_shell_sessions
from agent.modules.workspaces import (
    delete_thread_workspace,
    get_thread_workspace_refs,
    resolve_workspace_ref,
)
from agent.modules.workflows import delete_workflow_thread_tree

logger = logging.getLogger(__name__)

router = APIRouter()


class RenameThreadBody(BaseModel):
    """Request body for renaming a conversation thread."""

    title: str = Field(..., min_length=1, max_length=255, description="New title for the thread (1-255 characters).")


def _parse_thread_id_safe(thread_id: str) -> dict[str, str]:
    return parse_thread_metadata(thread_id)


def _workspace_summary(workspace: Any | None) -> dict[str, Any]:
    if workspace is None:
        return {
            "workspace": None,
            "workspace_key": NO_WORKSPACE_KEY,
            "workspace_label": NO_WORKSPACE_LABEL,
        }
    return {
        "workspace": workspace.model_dump(),
        "workspace_key": f"{workspace.backend}:{workspace.locator}",
        "workspace_label": workspace.display_label(),
    }


async def _attach_workspace_summaries(
    threads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not threads:
        return threads

    thread_ids = [str(thread.get("thread_id") or "") for thread in threads]
    try:
        workspaces = await get_thread_workspace_refs(thread_ids)
    except Exception as exc:
        logger.warning("Failed to list thread workspaces: %s", exc)
        workspaces = {}

    workspaces = dict(workspaces)
    for thread_id in thread_ids:
        if thread_id in workspaces:
            continue
        try:
            task = get_background_task_manager().get_by_thread_id(thread_id)
        except Exception as exc:
            logger.debug(
                "Failed to load background task workspace for thread %s: %s",
                thread_id,
                exc,
            )
            continue
        task_workspace = (task or {}).get("workspace")
        if task_workspace:
            workspaces[thread_id] = resolve_workspace_ref(task_workspace)

    return [
        {
            **thread,
            **_workspace_summary(workspaces.get(str(thread.get("thread_id") or ""))),
        }
        for thread in threads
    ]


async def _list_threads_from_db(
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    threads = await list_user_threads_with_stats(limit=limit, offset=offset)
    return await _attach_workspace_summaries(threads)


async def _list_background_threads_from_db(
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return await list_background_threads_with_stats(limit=limit, offset=offset)


@router.get("/dashboard-api/chat-history")
async def get_chat_history(
    limit: int | None = Query(default=None, ge=1, le=100, description="Max threads to return."),
    offset: int = Query(default=0, ge=0, description="Number of threads to skip."),
) -> dict[str, Any]:
    """List conversation threads with pagination."""
    fetch_limit = limit + 1 if limit is not None else None
    threads = await _list_threads_from_db(limit=fetch_limit, offset=offset)
    has_more = limit is not None and len(threads) > limit

    if limit is not None:
        threads = threads[:limit]

    return {
        "threads": threads,
        "has_more": has_more,
        "next_offset": offset + len(threads),
    }


@router.get("/dashboard-api/chat-history/{thread_id:path}")
async def get_chat_thread_messages(
    thread_id: str,
    checkpoint_id: str | None = Query(default=None, min_length=1, description="Specific checkpoint ID to load."),
) -> dict[str, Any]:
    """Get all messages and metadata for a specific conversation thread."""
    try:
        messages, active_checkpoint_id = await get_thread_messages_payload(
            thread_id,
            checkpoint_id=checkpoint_id,
            include_branch_metadata=True,
        )
    except CheckpointNotFoundError:
        raise HTTPException(status_code=404, detail="Checkpoint not found.") from None
    except ConversationHistoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    metadata = await get_conversation_thread(thread_id)
    parsed = metadata or _parse_thread_id_safe(thread_id)
    workspace = await workspace_ref_for_thread(thread_id, include_default=False)
    return {
        "thread_id": thread_id,
        "active_checkpoint_id": active_checkpoint_id,
        "messages": messages,
        "workspace": workspace.model_dump() if workspace else None,
        **parsed,
    }


@router.patch("/dashboard-api/chat-history/{thread_id:path}")
async def rename_chat_thread(
    thread_id: str,
    body: RenameThreadBody,
) -> dict[str, Any]:
    """Rename a conversation thread."""
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Thread title cannot be empty.")

    metadata = await rename_conversation_thread(thread_id, title)
    stats = await get_checkpoint_stats(thread_id)
    workspace = await workspace_ref_for_thread(thread_id, include_default=False)
    return {
        **metadata,
        **stats,
        **_workspace_summary(workspace),
    }


@router.delete("/dashboard-api/chat-history/{thread_id:path}")
async def delete_chat_thread(thread_id: str) -> dict[str, str]:
    """Delete a conversation thread and all its checkpoints."""
    close_thread_shell_sessions(thread_id)
    await delete_thread_workspace(thread_id)
    await mark_conversation_thread_deleted(thread_id)
    await delete_workflow_thread_tree(thread_id)
    return {"status": "deleted", "thread_id": thread_id}


# --- background task conversation endpoints -------------------------------------------


@router.get("/dashboard-api/background-tasks")
async def list_background_task_threads(
    limit: int | None = Query(default=None, ge=1, le=100, description="Max tasks to return."),
    offset: int = Query(default=0, ge=0, description="Number of tasks to skip."),
) -> dict[str, Any]:
    """List background task threads with pagination."""
    fetch_limit = limit + 1 if limit is not None else None
    threads = await _list_background_threads_from_db(limit=fetch_limit, offset=offset)
    has_more = limit is not None and len(threads) > limit

    if limit is not None:
        threads = threads[:limit]

    return {
        "tasks": threads,
        "has_more": has_more,
        "next_offset": offset + len(threads),
    }


@router.get("/dashboard-api/background-tasks/{thread_id:path}")
async def get_background_task_messages(thread_id: str) -> dict[str, Any]:
    """Get all messages for a specific background task thread."""
    try:
        messages = await get_thread_messages(thread_id)
    except ConversationHistoryUnavailableError:
        messages = []

    metadata = await get_conversation_thread(thread_id)
    parsed = metadata or _parse_thread_id_safe(thread_id)
    workspace = await workspace_ref_for_thread(thread_id, include_default=False)
    return {
        "thread_id": thread_id,
        "messages": messages,
        "workspace": workspace.model_dump() if workspace else None,
        **parsed,
    }


async def _get_background_task_stream_metadata(
    thread_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    manager = get_background_task_manager()
    task = manager.get_by_thread_id(thread_id)
    metadata = await get_conversation_thread(thread_id)
    if task is None and (
        metadata is None or metadata.get("kind") != THREAD_KIND_BACKGROUND
    ):
        raise HTTPException(
            status_code=404,
            detail=f"Background task thread '{thread_id}' not found.",
        )
    return task, metadata


async def _get_thread_messages_for_stream(thread_id: str) -> list[dict[str, Any]]:
    try:
        return await get_thread_messages(thread_id)
    except ConversationHistoryUnavailableError:
        return []


async def _background_task_snapshot(
    thread_id: str,
    *,
    task: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manager = get_background_task_manager()
    current_task = task if task is not None else manager.get_by_thread_id(thread_id)
    parsed = metadata or _parse_thread_id_safe(thread_id)
    if current_task is not None:
        parsed = {**parsed, "kind": THREAD_KIND_BACKGROUND}

    workspace = await workspace_ref_for_thread(thread_id)
    return {
        "thread_id": thread_id,
        "messages": await _get_thread_messages_for_stream(thread_id),
        "task": current_task,
        "active_session": active_session_for_thread(thread_id),
        "workspace": workspace.model_dump() if workspace else None,
        **parsed,
    }


@router.get("/dashboard-api/background-task-events")
async def stream_background_task_events(
    thread_id: str = Query(..., min_length=1, description="Background task thread ID to stream events for."),
) -> StreamingResponse:
    """Stream real-time events (messages, status updates) for a background task via SSE."""
    manager = get_background_task_manager()
    task, metadata = await _get_background_task_stream_metadata(thread_id)
    queue = manager.subscribe(thread_id)
    try:
        initial_snapshot = await _background_task_snapshot(
            thread_id,
            task=task,
            metadata=metadata,
        )
    except Exception:
        manager.unsubscribe(thread_id, queue)
        raise

    async def event_generator():
        try:
            yield sse_event("snapshot", initial_snapshot)
            latest_task = initial_snapshot.get("task")
            if not is_active_background_task(latest_task):
                yield sse_event("done", {"task": latest_task})
                return

            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=SSE_HEARTBEAT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    latest_task = manager.get_by_thread_id(thread_id)
                    yield sse_event("heartbeat", {})
                    if not is_active_background_task(latest_task):
                        yield sse_event("done", {"task": latest_task})
                        return
                    continue

                event_name = str(event.get("event") or "message")
                event_data = event.get("data")
                yield sse_event(
                    event_name,
                    event_data if isinstance(event_data, dict) else {},
                )
                if event_name == "done":
                    return
        finally:
            manager.unsubscribe(thread_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/dashboard-api/background-tasks/{thread_id:path}")
async def delete_background_task_thread(thread_id: str) -> dict[str, str]:
    """Delete a background task thread and its associated resources."""
    close_thread_shell_sessions(thread_id)
    await delete_thread_workspace(thread_id)
    await get_background_task_repository().mark_deleted_by_thread_id(thread_id)
    await mark_conversation_thread_deleted(thread_id)
    await delete_workflow_thread_tree(thread_id)
    return {"status": "deleted", "thread_id": thread_id}
