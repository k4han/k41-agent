from __future__ import annotations

from typing import Any

from sqlalchemy import select

from agent.modules.workspaces.models import ThreadWorkspace
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.infrastructure.db.session import get_async_session


def _trim(value: str | None, max_length: int) -> str:
    return str(value or "").strip()[:max_length]


def serialize_thread_workspace(record: ThreadWorkspace) -> dict[str, Any]:
    return {
        "thread_id": record.thread_id,
        "working_dir": record.working_dir,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


class ThreadWorkspaceRepository:
    async def upsert(self, *, thread_id: str, working_dir: str) -> dict[str, Any]:
        now = utcnow()
        normalized_thread_id = _trim(thread_id, 512)
        normalized_working_dir = str(working_dir or "").strip()
        if not normalized_thread_id:
            raise ValueError("Thread ID is required.")
        if not normalized_working_dir:
            raise ValueError("Working directory is required.")

        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ThreadWorkspace).where(
                    ThreadWorkspace.thread_id == normalized_thread_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                record = ThreadWorkspace(
                    thread_id=normalized_thread_id,
                    working_dir=normalized_working_dir,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                record.working_dir = normalized_working_dir
                record.updated_at = now

            await session.commit()
            await session.refresh(record)
            return serialize_thread_workspace(record)

    async def get(self, thread_id: str) -> dict[str, Any] | None:
        normalized_thread_id = _trim(thread_id, 512)
        if not normalized_thread_id:
            return None

        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ThreadWorkspace).where(
                    ThreadWorkspace.thread_id == normalized_thread_id
                )
            )
            record = result.scalar_one_or_none()
            return serialize_thread_workspace(record) if record else None


_repository: ThreadWorkspaceRepository | None = None


def get_thread_workspace_repository() -> ThreadWorkspaceRepository:
    global _repository
    if _repository is None:
        _repository = ThreadWorkspaceRepository()
    return _repository


__all__ = [
    "ThreadWorkspaceRepository",
    "get_thread_workspace_repository",
    "serialize_thread_workspace",
]
