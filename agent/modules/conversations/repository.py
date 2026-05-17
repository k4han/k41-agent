from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from agent.modules.conversations.models import ConversationThread
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.infrastructure.db.session import get_async_session


def _trim(value: str, max_length: int) -> str:
    return str(value or "").strip()[:max_length]


def serialize_thread(thread: ConversationThread) -> dict[str, Any]:
    return {
        "thread_id": thread.thread_id,
        "platform": thread.platform,
        "user_id": thread.user_id,
        "channel_id": thread.channel_id,
        "agent_name": thread.agent_name,
        "title": thread.title,
        "kind": thread.kind,
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
    }


class ConversationThreadRepository:
    async def upsert(
        self,
        *,
        thread_id: str,
        platform: str,
        user_id: str,
        channel_id: str = "",
        agent_name: str = "",
        title: str = "",
        kind: str = "user",
    ) -> dict[str, Any]:
        now = utcnow()
        normalized_thread_id = _trim(thread_id, 512)
        normalized_platform = _trim(platform or "unknown", 50) or "unknown"
        normalized_user_id = _trim(user_id, 255)
        normalized_channel_id = _trim(channel_id, 255)
        normalized_agent_name = _trim(agent_name, 255)
        normalized_title = _trim(title or normalized_thread_id, 255)
        normalized_kind = _trim(kind or "user", 50) or "user"
        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ConversationThread).where(
                    ConversationThread.thread_id == normalized_thread_id
                )
            )
            thread = result.scalar_one_or_none()
            if thread is None:
                thread = ConversationThread(
                    thread_id=normalized_thread_id,
                    platform=normalized_platform,
                    user_id=normalized_user_id,
                    channel_id=normalized_channel_id,
                    agent_name=normalized_agent_name,
                    title=normalized_title,
                    kind=normalized_kind,
                    created_at=now,
                    updated_at=now,
                )
                session.add(thread)
            else:
                thread.platform = normalized_platform or thread.platform
                thread.user_id = normalized_user_id or thread.user_id
                thread.channel_id = normalized_channel_id
                if normalized_agent_name:
                    thread.agent_name = normalized_agent_name
                if normalized_title and (
                    not thread.title or thread.title == thread.thread_id
                ):
                    thread.title = normalized_title
                thread.kind = normalized_kind or thread.kind
                thread.updated_at = now
                thread.deleted_at = None

            await session.commit()
            await session.refresh(thread)
            return serialize_thread(thread)

    async def get(self, thread_id: str) -> dict[str, Any] | None:
        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ConversationThread).where(
                    ConversationThread.thread_id == thread_id,
                    ConversationThread.deleted_at.is_(None),
                )
            )
            thread = result.scalar_one_or_none()
            return serialize_thread(thread) if thread else None

    async def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        kind: str | None = "user",
    ) -> list[dict[str, Any]]:
        stmt = select(ConversationThread).where(
            ConversationThread.deleted_at.is_(None)
        )
        if kind:
            stmt = stmt.where(ConversationThread.kind == kind)
        stmt = stmt.order_by(
            ConversationThread.updated_at.desc(),
            ConversationThread.id.desc(),
        )
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)

        session = await get_async_session()
        async with session:
            result = await session.execute(stmt)
            return [serialize_thread(thread) for thread in result.scalars().all()]

    async def count(self, *, kind: str | None = "user") -> int:
        stmt = select(func.count()).select_from(ConversationThread).where(
            ConversationThread.deleted_at.is_(None)
        )
        if kind:
            stmt = stmt.where(ConversationThread.kind == kind)

        session = await get_async_session()
        async with session:
            result = await session.execute(stmt)
            return int(result.scalar_one() or 0)

    async def update_title(self, thread_id: str, title: str) -> dict[str, Any] | None:
        normalized_title = _trim(title, 255)
        if not normalized_title:
            raise ValueError("Thread title cannot be empty.")

        now = utcnow()
        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ConversationThread).where(
                    ConversationThread.thread_id == thread_id,
                    ConversationThread.deleted_at.is_(None),
                )
            )
            thread = result.scalar_one_or_none()
            if thread is None:
                return None

            thread.title = normalized_title
            thread.updated_at = now
            await session.commit()
            await session.refresh(thread)
            return serialize_thread(thread)

    async def mark_deleted(self, thread_id: str) -> bool:
        now = utcnow()
        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ConversationThread).where(
                    ConversationThread.thread_id == thread_id
                )
            )
            thread = result.scalar_one_or_none()
            if thread is None:
                return False

            thread.deleted_at = now
            thread.updated_at = now
            await session.commit()
            return True


_repository: ConversationThreadRepository | None = None


def get_conversation_thread_repository() -> ConversationThreadRepository:
    global _repository
    if _repository is None:
        _repository = ConversationThreadRepository()
    return _repository


__all__ = [
    "ConversationThreadRepository",
    "get_conversation_thread_repository",
    "serialize_thread",
]
