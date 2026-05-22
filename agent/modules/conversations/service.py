from __future__ import annotations

from collections.abc import Sequence
import uuid
from typing import Any

from agent.modules.agent_runtime import SessionManager
from agent.modules.conversations.repository import (
    ConversationThreadRepository,
    get_conversation_thread_repository,
)

THREAD_KIND_USER = "user"
THREAD_KIND_SUB_AGENT = "sub_agent"
THREAD_KIND_BACKGROUND = "background"
THREAD_KIND_SCHEDULED = "scheduled"


def create_thread_id(
    *,
    platform: str,
    user_id: str,
    channel_id: str | None = None,
) -> str:
    platform_value = getattr(platform, "value", platform)
    resolved_channel_id = channel_id or uuid.uuid4().hex[:12]
    return SessionManager.make_thread_id(
        str(platform_value),
        user_id,
        resolved_channel_id,
    )


def infer_thread_kind(thread_id: str) -> str:
    if ":sub:" in thread_id:
        return THREAD_KIND_SUB_AGENT
    if thread_id.startswith("task_"):
        return THREAD_KIND_BACKGROUND
    if thread_id.startswith("bg_"):
        return THREAD_KIND_SCHEDULED
    return THREAD_KIND_USER


def parse_thread_metadata(thread_id: str) -> dict[str, str]:
    try:
        platform, user_id, channel_id = SessionManager.parse_thread_id(thread_id)
    except ValueError:
        return {"platform": "unknown", "user_id": thread_id, "channel_id": ""}
    return {"platform": platform, "user_id": user_id, "channel_id": channel_id}


async def upsert_conversation_thread(
    *,
    thread_id: str,
    agent_name: str = "",
    title: str = "",
    kind: str | None = None,
    platform: str | None = None,
    user_id: str | None = None,
    channel_id: str | None = None,
    repository: ConversationThreadRepository | None = None,
) -> dict[str, Any]:
    parsed = parse_thread_metadata(thread_id)
    repo = repository or get_conversation_thread_repository()
    return await repo.upsert(
        thread_id=thread_id,
        platform=platform or parsed["platform"],
        user_id=user_id or parsed["user_id"],
        channel_id=channel_id if channel_id is not None else parsed["channel_id"],
        agent_name=agent_name,
        title=title or thread_id,
        kind=kind or infer_thread_kind(thread_id),
    )


async def list_conversation_threads(
    *,
    limit: int | None = None,
    offset: int = 0,
    kind: str | None = THREAD_KIND_USER,
    kinds: Sequence[str] | None = None,
    repository: ConversationThreadRepository | None = None,
) -> list[dict[str, Any]]:
    repo = repository or get_conversation_thread_repository()
    return await repo.list(limit=limit, offset=offset, kind=kind, kinds=kinds)


async def count_conversation_threads(
    *,
    kind: str | None = THREAD_KIND_USER,
    kinds: Sequence[str] | None = None,
    repository: ConversationThreadRepository | None = None,
) -> int:
    repo = repository or get_conversation_thread_repository()
    return await repo.count(kind=kind, kinds=kinds)


async def get_conversation_thread(
    thread_id: str,
    *,
    repository: ConversationThreadRepository | None = None,
) -> dict[str, Any] | None:
    repo = repository or get_conversation_thread_repository()
    return await repo.get(thread_id)


async def rename_conversation_thread(
    thread_id: str,
    title: str,
    *,
    repository: ConversationThreadRepository | None = None,
) -> dict[str, Any]:
    repo = repository or get_conversation_thread_repository()
    renamed = await repo.update_title(thread_id, title)
    if renamed is not None:
        return renamed

    return await upsert_conversation_thread(
        thread_id=thread_id,
        title=title,
        repository=repo,
    )


async def mark_conversation_thread_deleted(
    thread_id: str,
    *,
    repository: ConversationThreadRepository | None = None,
) -> bool:
    repo = repository or get_conversation_thread_repository()
    return await repo.mark_deleted(thread_id)


__all__ = [
    "THREAD_KIND_BACKGROUND",
    "THREAD_KIND_SCHEDULED",
    "THREAD_KIND_SUB_AGENT",
    "THREAD_KIND_USER",
    "count_conversation_threads",
    "create_thread_id",
    "get_conversation_thread",
    "infer_thread_kind",
    "list_conversation_threads",
    "mark_conversation_thread_deleted",
    "parse_thread_metadata",
    "rename_conversation_thread",
    "upsert_conversation_thread",
]
