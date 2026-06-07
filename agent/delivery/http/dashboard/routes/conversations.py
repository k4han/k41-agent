from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.modules.workspaces import WorkspaceRef
from agent.delivery.http.dashboard.routes.shared import (
    NO_WORKSPACE_KEY,
    NO_WORKSPACE_LABEL,
    SSE_HEARTBEAT_SECONDS,
    _active_session_for_thread,
    _is_active_background_task,
    _sse_event,
    _workspace_ref_for_thread,
)
from agent.modules.agent_runtime import get_background_task_manager
from agent.modules.workspaces import (
    get_thread_workspace_refs,
    resolve_workspace_ref,
)

logger = logging.getLogger(__name__)


router = APIRouter()


class RenameThreadBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)


def _get_checkpointer():
    from agent.modules.workflows import get_checkpointer

    try:
        return get_checkpointer()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _parse_thread_id_safe(thread_id: str) -> dict[str, str]:
    from agent.modules.conversations import parse_thread_metadata

    return parse_thread_metadata(thread_id)


async def _get_checkpoint_stats(thread_id: str) -> dict[str, Any]:
    """Read lightweight checkpoint stats via the checkpointer API."""
    try:
        checkpointer = _get_checkpointer()
    except HTTPException:
        return {"latest_checkpoint_id": "", "checkpoint_count": 0}

    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    latest_checkpoint_id = ""
    checkpoint_count = 0

    try:
        async for checkpoint_tuple in checkpointer.alist(config):
            checkpoint_count += 1
            if latest_checkpoint_id:
                continue

            tuple_config = getattr(checkpoint_tuple, "config", {}) or {}
            configurable = tuple_config.get("configurable", {})
            if isinstance(configurable, dict):
                latest_checkpoint_id = str(configurable.get("checkpoint_id", "") or "")

            if not latest_checkpoint_id:
                checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
                if isinstance(checkpoint, dict):
                    latest_checkpoint_id = str(checkpoint.get("id", "") or "")
    except Exception as exc:
        logger.warning("Failed to get checkpoint stats for %s: %s", thread_id, exc)
        return {"latest_checkpoint_id": "", "checkpoint_count": 0}

    return {
        "latest_checkpoint_id": latest_checkpoint_id,
        "checkpoint_count": checkpoint_count,
    }


async def _list_legacy_threads_from_checkpoints(
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Fallback for checkpoint-only threads created before thread metadata existed."""
    from agent.modules.conversations import THREAD_KIND_USER, infer_thread_kind

    try:
        checkpointer = _get_checkpointer()
    except HTTPException:
        return []

    summaries: dict[str, dict[str, Any]] = {}
    try:
        async for checkpoint_tuple in checkpointer.alist(None):
            tuple_config = getattr(checkpoint_tuple, "config", {}) or {}
            configurable = tuple_config.get("configurable", {})
            if not isinstance(configurable, dict):
                continue

            thread_id = str(configurable.get("thread_id", "") or "")
            if not thread_id or infer_thread_kind(thread_id) != THREAD_KIND_USER:
                continue

            checkpoint_ns = str(configurable.get("checkpoint_ns", "") or "")
            if checkpoint_ns:
                continue

            checkpoint_id = str(configurable.get("checkpoint_id", "") or "")
            summary = summaries.get(thread_id)
            if summary is None:
                summary = {
                    "thread_id": thread_id,
                    "latest_checkpoint_id": checkpoint_id,
                    "checkpoint_count": 0,
                    "agent_name": "",
                    "title": thread_id,
                    "kind": THREAD_KIND_USER,
                    "created_at": None,
                    "updated_at": None,
                    **_parse_thread_id_safe(thread_id),
                }
                summaries[thread_id] = summary
            summary["checkpoint_count"] += 1
    except Exception as exc:
        logger.warning("Failed to list legacy checkpoint threads: %s", exc)
        return []

    rows = list(summaries.values())
    if offset:
        rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    return rows


async def _list_threads_from_db(
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List user-facing conversation threads from the domain table."""
    from agent.modules.conversations import (
        THREAD_KIND_BACKGROUND,
        THREAD_KIND_USER,
        list_conversation_threads,
    )

    try:
        threads = await list_conversation_threads(
            limit=limit,
            offset=offset,
            kinds=[THREAD_KIND_USER, THREAD_KIND_BACKGROUND],
        )
    except Exception as exc:
        logger.warning("Failed to list conversation threads: %s", exc)
        return []

    if not threads:
        legacy_threads = await _list_legacy_threads_from_checkpoints(
            limit=limit,
            offset=offset,
        )
        return await _attach_workspace_summaries(legacy_threads)

    result = []
    for thread in threads:
        stats = await _get_checkpoint_stats(thread["thread_id"])
        result.append({
            **thread,
            **stats,
        })
    return await _attach_workspace_summaries(result)


def _workspace_summary(workspace: WorkspaceRef | None) -> dict[str, Any]:
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


def _serialize_message_attachments(msg: Any) -> list[dict[str, Any]]:
    additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
    raw_attachments = additional_kwargs.get("attachments")
    if not isinstance(raw_attachments, list):
        return []

    attachments = []
    for attachment in raw_attachments:
        if not isinstance(attachment, dict):
            continue
        entry: dict[str, Any] = {
            "name": str(attachment.get("name") or ""),
            "mime_type": str(attachment.get("mime_type") or ""),
            "size": int(attachment.get("size") or 0),
            "kind": str(attachment.get("kind") or ""),
        }
        if attachment.get("content"):
            entry["content"] = str(attachment["content"])
        if attachment.get("base64"):
            entry["base64"] = str(attachment["base64"])
        attachments.append(entry)
    return attachments


def _human_content_text(content: Any, *, has_attachments: bool) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    text_parts: list[str] = []
    for index, part in enumerate(content):
        if isinstance(part, dict):
            part_type = str(part.get("type") or "").strip().lower()
            if part_type == "text":
                if has_attachments and index > 0:
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
                continue
            if part_type in {"image", "file", "audio", "video"} and not has_attachments:
                text_parts.append(f"[Attached {part_type}]")
            continue
        text_parts.append(str(part))

    return "\n\n".join(text_parts).strip()


def _checkpoint_configurable(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    configurable = config.get("configurable", {})
    return configurable if isinstance(configurable, dict) else {}


def _checkpoint_id_from_config(config: Any) -> str:
    return str(_checkpoint_configurable(config).get("checkpoint_id", "") or "")


def _checkpoint_id_from_tuple(checkpoint_tuple: Any) -> str:
    checkpoint_id = _checkpoint_id_from_config(getattr(checkpoint_tuple, "config", None))
    if checkpoint_id:
        return checkpoint_id
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    if isinstance(checkpoint, dict):
        return str(checkpoint.get("id", "") or "")
    return ""


def _parent_checkpoint_id(checkpoint_tuple: Any) -> str:
    return _checkpoint_id_from_config(getattr(checkpoint_tuple, "parent_config", None))


def _checkpoint_sort_key(checkpoint_tuple: Any) -> tuple[str, str]:
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    timestamp = ""
    if isinstance(checkpoint, dict):
        timestamp = str(checkpoint.get("ts", "") or "")
    return (timestamp, _checkpoint_id_from_tuple(checkpoint_tuple))


def _checkpoint_messages(checkpoint_tuple: Any) -> list[Any]:
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    if not isinstance(checkpoint, dict):
        return []
    channel_values = checkpoint.get("channel_values", {})
    if not isinstance(channel_values, dict):
        return []
    messages = channel_values.get("messages", [])
    return list(messages) if isinstance(messages, list) else []


def _message_content_key(msg: Any) -> str:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    if isinstance(msg, HumanMessage):
        attachments = _serialize_message_attachments(msg)
        return _human_content_text(msg.content, has_attachments=bool(attachments))
    if isinstance(msg, (AIMessage, ToolMessage)):
        from agent.shared.infrastructure.parsing import extract_final_text_content

        return extract_final_text_content(getattr(msg, "content", None)) or ""
    return str(getattr(msg, "content", ""))


def _message_signature(msg: Any) -> tuple[str, str, str, str]:
    return (
        str(getattr(msg, "type", "") or msg.__class__.__name__),
        str(getattr(msg, "id", "") or ""),
        str(getattr(msg, "tool_call_id", "") or ""),
        _message_content_key(msg),
    )


def _message_prefix_key(messages: list[Any], end_index: int) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(_message_signature(message) for message in messages[:end_index])


async def _list_checkpoint_tuples(thread_id: str) -> list[Any]:
    checkpointer = _get_checkpointer()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    tuples: list[Any] = []
    try:
        async for checkpoint_tuple in checkpointer.alist(config):
            configurable = _checkpoint_configurable(
                getattr(checkpoint_tuple, "config", None)
            )
            if str(configurable.get("checkpoint_ns", "") or ""):
                continue
            tuples.append(checkpoint_tuple)
    except Exception as exc:
        logger.warning("Failed to list checkpoints for thread %s: %s", thread_id, exc)
    return tuples


def _leaf_checkpoint_tuples(checkpoint_tuples: list[Any]) -> list[Any]:
    parent_ids = {
        _parent_checkpoint_id(checkpoint_tuple)
        for checkpoint_tuple in checkpoint_tuples
        if _parent_checkpoint_id(checkpoint_tuple)
    }
    leaves = [
        checkpoint_tuple
        for checkpoint_tuple in checkpoint_tuples
        if _checkpoint_id_from_tuple(checkpoint_tuple)
        and _checkpoint_id_from_tuple(checkpoint_tuple) not in parent_ids
        and _checkpoint_messages(checkpoint_tuple)
    ]
    return sorted(leaves, key=_checkpoint_sort_key)


def _checkpoint_path_to_active(
    checkpoint_tuples: list[Any],
    active_checkpoint_id: str,
) -> list[Any]:
    by_id = {
        _checkpoint_id_from_tuple(checkpoint_tuple): checkpoint_tuple
        for checkpoint_tuple in checkpoint_tuples
        if _checkpoint_id_from_tuple(checkpoint_tuple)
    }
    path: list[Any] = []
    current_id = active_checkpoint_id
    visited: set[str] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        checkpoint_tuple = by_id.get(current_id)
        if checkpoint_tuple is None:
            break
        path.append(checkpoint_tuple)
        current_id = _parent_checkpoint_id(checkpoint_tuple)
    path.reverse()
    return path


def _message_source_by_index(
    *,
    active_messages: list[Any],
    active_path: list[Any],
) -> dict[int, tuple[str, str]]:
    sources: dict[int, tuple[str, str]] = {}
    for checkpoint_tuple in active_path:
        checkpoint_messages = _checkpoint_messages(checkpoint_tuple)
        checkpoint_id = _checkpoint_id_from_tuple(checkpoint_tuple)
        parent_id = _parent_checkpoint_id(checkpoint_tuple)
        max_index = min(len(active_messages), len(checkpoint_messages))
        for index in range(max_index):
            if index in sources:
                continue
            if _message_prefix_key(checkpoint_messages, index + 1) != _message_prefix_key(
                active_messages,
                index + 1,
            ):
                continue
            sources[index] = (checkpoint_id, parent_id)
    return sources


def _branch_metadata_by_index(
    *,
    active_messages: list[Any],
    active_checkpoint_id: str,
    leaf_tuples: list[Any],
) -> dict[int, dict[str, Any]]:
    from langchain_core.messages import HumanMessage

    branches: dict[int, dict[str, Any]] = {}
    for index, active_message in enumerate(active_messages):
        if not isinstance(active_message, HumanMessage):
            continue
        prefix_key = _message_prefix_key(active_messages, index)
        options_by_signature: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for leaf_tuple in leaf_tuples:
            leaf_messages = _checkpoint_messages(leaf_tuple)
            if len(leaf_messages) <= index:
                continue
            leaf_message = leaf_messages[index]
            if not isinstance(leaf_message, HumanMessage):
                continue
            if _message_prefix_key(leaf_messages, index) != prefix_key:
                continue
            checkpoint_id = _checkpoint_id_from_tuple(leaf_tuple)
            if not checkpoint_id:
                continue
            attachments = _serialize_message_attachments(leaf_message)
            signature = _message_signature(leaf_message)
            options_by_signature[signature] = {
                "checkpoint_id": checkpoint_id,
                "message": _human_content_text(
                    leaf_message.content,
                    has_attachments=bool(attachments),
                ),
            }

        options = list(options_by_signature.values())
        if len(options) <= 1:
            continue

        active_signature = _message_signature(active_message)
        current_option = options_by_signature.get(active_signature)
        current_index = 0
        if current_option is not None:
            current_index = next(
                (
                    option_index
                    for option_index, option in enumerate(options)
                    if option["checkpoint_id"] == current_option["checkpoint_id"]
                ),
                0,
            )

        branches[index] = {
            "current": current_index + 1,
            "total": len(options),
            "options": options,
        }
    return branches


def _serialize_thread_messages(
    messages: list[Any],
    *,
    sources: dict[int, tuple[str, str]] | None = None,
    branches: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    sources = sources or {}
    branches = branches or {}

    result = []
    for message_index, msg in enumerate(messages):
        entry: dict[str, Any] = {"id": getattr(msg, "id", None)}
        if isinstance(msg, HumanMessage):
            attachments = _serialize_message_attachments(msg)
            entry["role"] = "user"
            entry["content"] = _human_content_text(
                msg.content,
                has_attachments=bool(attachments),
            )
            if attachments:
                entry["attachments"] = attachments
            source_checkpoint_id, parent_checkpoint_id = sources.get(
                message_index,
                ("", ""),
            )
            if source_checkpoint_id:
                entry["message_index"] = message_index
                entry["source_checkpoint_id"] = source_checkpoint_id
                entry["parent_checkpoint_id"] = parent_checkpoint_id
            if message_index in branches:
                entry["branch"] = branches[message_index]
        elif isinstance(msg, AIMessage):
            from agent.shared.infrastructure.parsing import extract_final_text_content

            content = extract_final_text_content(getattr(msg, "content", None))
            entry["role"] = "assistant"
            entry["content"] = content or ""
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.get("id"),
                        "name": tc.get("name"),
                        "args": tc.get("args"),
                    }
                    for tc in tool_calls
                ]
        elif isinstance(msg, ToolMessage):
            from agent.shared.infrastructure.parsing import extract_final_text_content

            entry["role"] = "tool"
            entry["name"] = getattr(msg, "name", None)
            entry["tool_call_id"] = getattr(msg, "tool_call_id", None)
            entry["content"] = extract_final_text_content(
                getattr(msg, "content", None)
            ) or ""
        else:
            entry["role"] = "system"
            entry["content"] = str(getattr(msg, "content", ""))

        result.append(entry)

    return result


async def _get_thread_messages_payload(
    thread_id: str,
    *,
    checkpoint_id: str | None = None,
    include_branch_metadata: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Get serialized messages and active checkpoint from a thread."""
    checkpointer = _get_checkpointer()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    if checkpoint_id:
        config["configurable"]["checkpoint_id"] = checkpoint_id

    try:
        checkpoint_tuple = await checkpointer.aget_tuple(config)
    except Exception as exc:
        logger.warning("Failed to get checkpoint for thread %s: %s", thread_id, exc)
        if checkpoint_id:
            raise HTTPException(status_code=404, detail="Checkpoint not found.") from exc
        return [], ""

    if checkpoint_tuple is None:
        if checkpoint_id:
            raise HTTPException(status_code=404, detail="Checkpoint not found.")
        return [], ""

    active_checkpoint_id = _checkpoint_id_from_tuple(checkpoint_tuple)
    messages = _checkpoint_messages(checkpoint_tuple)
    if not include_branch_metadata:
        return _serialize_thread_messages(messages), active_checkpoint_id

    checkpoint_tuples = await _list_checkpoint_tuples(thread_id)
    checkpoint_ids = {
        _checkpoint_id_from_tuple(existing_tuple)
        for existing_tuple in checkpoint_tuples
    }
    if active_checkpoint_id not in checkpoint_ids:
        checkpoint_tuples.append(checkpoint_tuple)
    active_path = _checkpoint_path_to_active(checkpoint_tuples, active_checkpoint_id)
    sources = _message_source_by_index(
        active_messages=messages,
        active_path=active_path,
    )
    branches = _branch_metadata_by_index(
        active_messages=messages,
        active_checkpoint_id=active_checkpoint_id,
        leaf_tuples=_leaf_checkpoint_tuples(checkpoint_tuples),
    )
    return (
        _serialize_thread_messages(
            messages,
            sources=sources,
            branches=branches,
        ),
        active_checkpoint_id,
    )


async def _get_thread_messages(thread_id: str) -> list[dict[str, Any]]:
    """Get messages from a thread via the checkpointer."""
    messages, _ = await _get_thread_messages_payload(thread_id)
    return messages


@router.get("/dashboard-api/chat-history")
async def get_chat_history(
    limit: int | None = Query(default=None, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
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
    checkpoint_id: str | None = Query(default=None, min_length=1),
) -> dict[str, Any]:
    messages, active_checkpoint_id = await _get_thread_messages_payload(
        thread_id,
        checkpoint_id=checkpoint_id,
        include_branch_metadata=True,
    )
    from agent.modules.conversations import get_conversation_thread

    metadata = await get_conversation_thread(thread_id)
    parsed = metadata or _parse_thread_id_safe(thread_id)
    workspace = await _workspace_ref_for_thread(thread_id, include_default=False)
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
    from agent.modules.conversations import rename_conversation_thread

    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Thread title cannot be empty.")

    metadata = await rename_conversation_thread(thread_id, title)
    stats = await _get_checkpoint_stats(thread_id)
    workspace = await _workspace_ref_for_thread(thread_id, include_default=False)
    return {
        **metadata,
        **stats,
        **_workspace_summary(workspace),
    }


@router.delete("/dashboard-api/chat-history/{thread_id:path}")
async def delete_chat_thread(thread_id: str) -> dict[str, str]:
    from agent.modules.conversations import mark_conversation_thread_deleted
    from agent.modules.tools import close_thread_shell_sessions
    from agent.modules.workspaces import delete_thread_workspace
    from agent.modules.workflows import delete_workflow_thread_tree

    close_thread_shell_sessions(thread_id)
    await delete_thread_workspace(thread_id)
    await mark_conversation_thread_deleted(thread_id)
    await delete_workflow_thread_tree(thread_id)
    return {"status": "deleted", "thread_id": thread_id}


# --- background task conversation endpoints -------------------------------------------


async def _list_background_threads_from_db(
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List background task threads from the domain table."""
    from agent.modules.conversations import THREAD_KIND_BACKGROUND, list_conversation_threads

    try:
        threads = await list_conversation_threads(
            limit=limit,
            offset=offset,
            kind=THREAD_KIND_BACKGROUND,
        )
    except Exception as exc:
        logger.warning("Failed to list background task threads: %s", exc)
        return []

    if not threads:
        return []

    result = []
    for thread in threads:
        stats = await _get_checkpoint_stats(thread["thread_id"])
        result.append({
            **thread,
            **stats,
        })
    return result


@router.get("/dashboard-api/background-tasks")
async def list_background_task_threads(
    limit: int | None = Query(default=None, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
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
    messages = await _get_thread_messages(thread_id)
    from agent.modules.conversations import get_conversation_thread

    metadata = await get_conversation_thread(thread_id)
    parsed = metadata or _parse_thread_id_safe(thread_id)
    workspace = await _workspace_ref_for_thread(thread_id, include_default=False)
    return {
        "thread_id": thread_id,
        "messages": messages,
        "workspace": workspace.model_dump() if workspace else None,
        **parsed,
    }


async def _get_background_task_stream_metadata(
    thread_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    from agent.modules.conversations import THREAD_KIND_BACKGROUND, get_conversation_thread

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
        return await _get_thread_messages(thread_id)
    except HTTPException as exc:
        if exc.status_code == 503:
            return []
        raise


async def _background_task_snapshot(
    thread_id: str,
    *,
    task: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from agent.modules.conversations import THREAD_KIND_BACKGROUND

    manager = get_background_task_manager()
    current_task = task if task is not None else manager.get_by_thread_id(thread_id)
    parsed = metadata or _parse_thread_id_safe(thread_id)
    if current_task is not None:
        parsed = {**parsed, "kind": THREAD_KIND_BACKGROUND}

    workspace = await _workspace_ref_for_thread(thread_id)
    return {
        "thread_id": thread_id,
        "messages": await _get_thread_messages_for_stream(thread_id),
        "task": current_task,
        "active_session": _active_session_for_thread(thread_id),
        "workspace": workspace.model_dump() if workspace else None,
        **parsed,
    }


@router.get("/dashboard-api/background-task-events")
async def stream_background_task_events(
    thread_id: str = Query(..., min_length=1),
) -> StreamingResponse:
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
            yield _sse_event("snapshot", initial_snapshot)
            latest_task = initial_snapshot.get("task")
            if not _is_active_background_task(latest_task):
                yield _sse_event("done", {"task": latest_task})
                return

            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=SSE_HEARTBEAT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    latest_task = manager.get_by_thread_id(thread_id)
                    yield _sse_event("heartbeat", {})
                    if not _is_active_background_task(latest_task):
                        yield _sse_event("done", {"task": latest_task})
                        return
                    continue

                event_name = str(event.get("event") or "message")
                event_data = event.get("data")
                yield _sse_event(
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
    from agent.modules.conversations import mark_conversation_thread_deleted
    from agent.modules.agent_runtime import get_background_task_repository
    from agent.modules.tools import close_thread_shell_sessions
    from agent.modules.workspaces import delete_thread_workspace
    from agent.modules.workflows import delete_workflow_thread_tree

    close_thread_shell_sessions(thread_id)
    await delete_thread_workspace(thread_id)
    await get_background_task_repository().mark_deleted_by_thread_id(thread_id)
    await mark_conversation_thread_deleted(thread_id)
    await delete_workflow_thread_tree(thread_id)
    return {"status": "deleted", "thread_id": thread_id}
