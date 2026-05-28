from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from agent.modules.workspaces.models import ThreadWorkspace
from agent.modules.workspaces.refs import (
    DEFAULT_LOCAL_WORKSPACE,
    WorkspaceRef,
    normalize_workspace_ref,
    workspace_ref_from_columns,
)
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.infrastructure.db.session import get_async_session


def _trim(value: str | None, max_length: int) -> str:
    return str(value or "").strip()[:max_length]


def serialize_thread_workspace(record: ThreadWorkspace) -> dict[str, Any]:
    workspace = _workspace_from_record(record)
    return {
        "thread_id": record.thread_id,
        "workspace": workspace.model_dump(),
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _workspace_from_record(record: ThreadWorkspace) -> WorkspaceRef:
    from agent.shared.config.service import get_config_service
    default_locator = str(get_config_service().get_path("workspace.root", "~/kaka-agent"))
    locator = record.workspace_locator or record.working_dir or default_locator
    return workspace_ref_from_columns(
        backend=record.workspace_backend,
        locator=locator,
        label=record.workspace_label,
        metadata_json=record.workspace_metadata_json,
    )


class ThreadWorkspaceRepository:
    async def upsert(
        self,
        *,
        thread_id: str,
        workspace: WorkspaceRef | dict[str, Any] | str,
    ) -> dict[str, Any]:
        now = utcnow()
        normalized_thread_id = _trim(thread_id, 512)
        from agent.shared.config.service import get_config_service
        default_locator = str(get_config_service().get_path("workspace.root", "~/kaka-agent"))
        workspace_ref = normalize_workspace_ref(
            workspace,
            default_locator=default_locator,
        )
        if not normalized_thread_id:
            raise ValueError("Thread ID is required.")
        if not workspace_ref.locator:
            raise ValueError("Workspace locator is required.")

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
                    working_dir=workspace_ref.locator,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                record.working_dir = workspace_ref.locator
                record.updated_at = now
            record.workspace_backend = workspace_ref.backend
            record.workspace_locator = workspace_ref.locator
            record.workspace_label = workspace_ref.label
            record.workspace_metadata_json = json.dumps(
                workspace_ref.metadata,
                ensure_ascii=False,
                sort_keys=True,
            )

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

    async def list_by_thread_ids(
        self,
        thread_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        normalized_thread_ids = list(
            dict.fromkeys(
                thread_id
                for thread_id in (_trim(thread_id, 512) for thread_id in thread_ids)
                if thread_id
            )
        )
        if not normalized_thread_ids:
            return {}

        session = await get_async_session()
        async with session:
            result = await session.execute(
                select(ThreadWorkspace).where(
                    ThreadWorkspace.thread_id.in_(normalized_thread_ids)
                )
            )
            return {
                record.thread_id: serialize_thread_workspace(record)
                for record in result.scalars().all()
            }


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
