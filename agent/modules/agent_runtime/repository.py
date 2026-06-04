from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from agent.modules.agent_runtime.models import BackgroundTaskRecord
from agent.modules.workspaces import (
    DEFAULT_LOCAL_WORKSPACE,
    WorkspaceRef,
    normalize_workspace_ref,
    workspace_ref_from_columns,
)
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.infrastructure.db.session import get_async_session


def _datetime_from_timestamp(value: float | int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _timestamp_from_datetime(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _trim(value: str | None, max_length: int) -> str:
    return str(value or "").strip()[:max_length]


def _dump_json_list(values: list[str] | None) -> str:
    """Serialize a list of names to a JSON string for storage.

    ``None`` is treated as "no whitelist configured" and stored as an
    empty list. Empty list means "deny all" (semantically distinct from
    unset), so we preserve that distinction at the API layer.
    """
    if values is None:
        return "[]"
    return json.dumps([str(v) for v in values], ensure_ascii=False)


def serialize_background_task(record: BackgroundTaskRecord) -> dict[str, Any]:
    workspace = _workspace_from_record(record)
    return {
        "task_id": record.task_id,
        "thread_id": record.thread_id,
        "request": record.request,
        "agent_name": record.agent_name,
        "workspace": workspace.model_dump() if workspace else None,
        "notify_platform": record.notify_platform,
        "notify_external_id": record.notify_external_id,
        "notify_channel_id": record.notify_channel_id,
        "allowed_tool_names": _parse_json_list(record.allowed_tool_names_json),
        "allowed_skill_names": _parse_json_list(record.allowed_skill_names_json),
        "status": record.status,
        "result": record.result,
        "error": record.error,
        "created_at": _timestamp_from_datetime(record.created_at),
        "started_at": _timestamp_from_datetime(record.started_at),
        "completed_at": _timestamp_from_datetime(record.completed_at),
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _parse_json_list(value: Any) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if item is not None]


def _workspace_from_record(record: BackgroundTaskRecord) -> WorkspaceRef | None:
    locator = record.workspace_locator or record.working_dir
    if not locator:
        return None
    return workspace_ref_from_columns(
        backend=record.workspace_backend,
        locator=locator,
        label=record.workspace_label,
        metadata_json=record.workspace_metadata_json,
    )


class BackgroundTaskRepository:
    async def upsert(
        self,
        *,
        task_id: str,
        thread_id: str,
        request: str,
        agent_name: str,
        working_dir: str | None,
        notify_platform: str,
        notify_external_id: str,
        notify_channel_id: str,
        status: str,
        result: str,
        error: str,
        created_at: float,
        started_at: float | None,
        completed_at: float | None,
        allowed_tool_names: list[str] | None = None,
        allowed_skill_names: list[str] | None = None,
        workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        normalized_task_id = _trim(task_id, 64)
        from agent.shared.config.service import get_config_service
        default_locator = str(get_config_service().get_path("workspace.root", "~/kaka-agent"))
        workspace_ref = (
            normalize_workspace_ref(
                workspace if workspace is not None else working_dir,
                default_locator=default_locator,
            )
            if workspace is not None or working_dir
            else None
        )
        session = await get_async_session()
        async with session:
            stmt = select(BackgroundTaskRecord).where(
                BackgroundTaskRecord.task_id == normalized_task_id
            )
            query_result = await session.execute(stmt)
            record = query_result.scalar_one_or_none()
            if record is None:
                record = BackgroundTaskRecord(task_id=normalized_task_id)
                session.add(record)

            record.thread_id = _trim(thread_id, 512)
            record.request = str(request or "")
            record.agent_name = _trim(agent_name or "default", 255) or "default"
            record.working_dir = workspace_ref.locator if workspace_ref else None
            record.workspace_backend = workspace_ref.backend if workspace_ref else None
            record.workspace_locator = workspace_ref.locator if workspace_ref else None
            record.workspace_label = workspace_ref.label if workspace_ref else None
            record.workspace_metadata_json = (
                json.dumps(workspace_ref.metadata, ensure_ascii=False, sort_keys=True)
                if workspace_ref
                else None
            )
            record.notify_platform = _trim(notify_platform, 50)
            record.notify_external_id = _trim(notify_external_id, 255)
            record.notify_channel_id = _trim(notify_channel_id, 255)
            record.allowed_tool_names_json = _dump_json_list(allowed_tool_names)
            record.allowed_skill_names_json = _dump_json_list(allowed_skill_names)
            record.status = _trim(status or "pending", 50) or "pending"
            record.result = str(result or "")
            record.error = str(error or "")
            record.created_at = _datetime_from_timestamp(created_at) or now
            record.started_at = _datetime_from_timestamp(started_at)
            record.completed_at = _datetime_from_timestamp(completed_at)
            record.updated_at = now
            record.deleted_at = None

            await session.commit()
            await session.refresh(record)
            return serialize_background_task(record)

    async def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        stmt = select(BackgroundTaskRecord).where(
            BackgroundTaskRecord.deleted_at.is_(None)
        )
        stmt = stmt.order_by(
            BackgroundTaskRecord.created_at.desc(),
            BackgroundTaskRecord.id.desc(),
        )
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)

        session = await get_async_session()
        async with session:
            query_result = await session.execute(stmt)
            return [
                serialize_background_task(record)
                for record in query_result.scalars().all()
            ]

    async def mark_deleted(self, task_id: str) -> bool:
        return await self._mark_deleted(
            BackgroundTaskRecord.task_id == task_id,
            BackgroundTaskRecord.deleted_at.is_(None),
        )

    async def mark_deleted_by_thread_id(self, thread_id: str) -> bool:
        return await self._mark_deleted(
            BackgroundTaskRecord.thread_id == thread_id,
            BackgroundTaskRecord.deleted_at.is_(None),
        )

    async def _mark_deleted(self, *where_clauses: Any) -> bool:
        session = await get_async_session()
        async with session:
            stmt = select(BackgroundTaskRecord).where(*where_clauses)
            query_result = await session.execute(stmt)
            record = query_result.scalar_one_or_none()
            if record is None:
                return False

            now = utcnow()
            record.deleted_at = now
            record.updated_at = now
            await session.commit()
            return True


_repository: BackgroundTaskRepository | None = None


def get_background_task_repository() -> BackgroundTaskRepository:
    global _repository
    if _repository is None:
        _repository = BackgroundTaskRepository()
    return _repository


__all__ = [
    "BackgroundTaskRepository",
    "get_background_task_repository",
    "serialize_background_task",
]
