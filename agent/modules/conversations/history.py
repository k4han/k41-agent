"""Read conversation history (threads, messages, branches) from workflow checkpoints."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.modules.conversations.service import (
    THREAD_KIND_BACKGROUND,
    THREAD_KIND_USER,
    infer_thread_kind,
    list_conversation_threads,
    parse_thread_metadata,
)
from agent.shared.infrastructure.parsing import extract_final_text_content

logger = logging.getLogger(__name__)


class ConversationHistoryUnavailableError(RuntimeError):
    """Raised when the workflow checkpointer backing thread history is not available."""


class CheckpointNotFoundError(LookupError):
    """Raised when a requested checkpoint does not exist for a thread."""


def get_history_checkpointer():
    from agent.modules.workflows import get_checkpointer

    try:
        return get_checkpointer()
    except RuntimeError as exc:
        raise ConversationHistoryUnavailableError(str(exc)) from exc


async def get_checkpoint_stats(thread_id: str) -> dict[str, Any]:
    """Read lightweight checkpoint stats via the checkpointer API."""
    try:
        checkpointer = get_history_checkpointer()
    except ConversationHistoryUnavailableError:
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


async def list_legacy_threads_from_checkpoints(
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Fallback for checkpoint-only threads created before thread metadata existed."""
    try:
        checkpointer = get_history_checkpointer()
    except ConversationHistoryUnavailableError:
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
                    **parse_thread_metadata(thread_id),
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


async def list_user_threads_with_stats(
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List user-facing conversation threads with checkpoint stats."""
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
        return await list_legacy_threads_from_checkpoints(limit=limit, offset=offset)

    result = []
    for thread in threads:
        stats = await get_checkpoint_stats(thread["thread_id"])
        result.append({
            **thread,
            **stats,
        })
    return result


async def list_background_threads_with_stats(
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List background task threads with checkpoint stats."""
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
        stats = await get_checkpoint_stats(thread["thread_id"])
        result.append({
            **thread,
            **stats,
        })
    return result


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
    if isinstance(msg, HumanMessage):
        attachments = _serialize_message_attachments(msg)
        return _human_content_text(msg.content, has_attachments=bool(attachments))
    if isinstance(msg, (AIMessage, ToolMessage)):
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
    checkpointer = get_history_checkpointer()
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


async def get_thread_messages_payload(
    thread_id: str,
    *,
    checkpoint_id: str | None = None,
    include_branch_metadata: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Get serialized messages and active checkpoint from a thread.

    Raises CheckpointNotFoundError when an explicit checkpoint_id cannot be loaded.
    """
    checkpointer = get_history_checkpointer()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    if checkpoint_id:
        config["configurable"]["checkpoint_id"] = checkpoint_id

    try:
        checkpoint_tuple = await checkpointer.aget_tuple(config)
    except Exception as exc:
        logger.warning("Failed to get checkpoint for thread %s: %s", thread_id, exc)
        if checkpoint_id:
            raise CheckpointNotFoundError("Checkpoint not found.") from exc
        return [], ""

    if checkpoint_tuple is None:
        if checkpoint_id:
            raise CheckpointNotFoundError("Checkpoint not found.")
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


async def get_thread_messages(thread_id: str) -> list[dict[str, Any]]:
    """Get messages from a thread via the checkpointer."""
    messages, _ = await get_thread_messages_payload(thread_id)
    return messages
