"""Transactional catalog operations shared by workspace writers and the dashboard."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent.modules.projects.models import Project
from agent.modules.workspaces.refs import WorkspaceBinding
from agent.shared.infrastructure.db.base import utcnow


def serialize_project(record: Project) -> dict[str, Any]:
    return {
        "id": record.id, "key": record.key, "label": record.label,
        "status": record.status, "is_pinned": record.is_pinned,
        "workspace": json.loads(record.workspace_json),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def encode_cursor(is_pinned: bool, key: str) -> str:
    return json.dumps([is_pinned, key], ensure_ascii=False, separators=(",", ":"))


def decode_cursor(value: str) -> tuple[bool, str]:
    # Accept the previous navigation API's raw workspace-key cursors.
    if not value.startswith("["):
        return False, value
    try:
        data = json.loads(value)
        if (not isinstance(data, list) or len(data) != 2
                or not isinstance(data[0], bool) or not isinstance(data[1], str)):
            raise ValueError
        return data[0], data[1]
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid project cursor.") from exc


def cursor_condition(pinned_column, key_column, after: str):
    pinned, key = decode_cursor(after)
    same_group = and_(pinned_column == pinned, key_column > key)
    return or_(pinned_column.is_(False), same_group) if pinned else same_group


async def register_project(
    session: AsyncSession, binding: WorkspaceBinding, *, label: str | None = None,
    restore: bool = False, only_missing: bool = False,
) -> Project | None:
    """Upsert in the caller's transaction without resetting user-managed metadata."""
    metadata = binding.execution.metadata
    if bool(metadata.get("temp")) or str(metadata.get("source") or "").strip().lower() == "temp":
        return None
    now = utcnow()
    values = {
        "key": binding.scope.key, "label": (label or binding.scope.label or binding.scope.key)[:255],
        "status": "active", "is_pinned": False,
        "workspace_json": binding.model_dump_json(), "created_at": now, "updated_at": now,
    }
    insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(Project).values(**values)
    if only_missing:
        statement = statement.on_conflict_do_nothing(index_elements=[Project.key])
    else:
        updates = {"workspace_json": values["workspace_json"], "updated_at": now}
        if restore:
            updates["status"] = "active"
        statement = statement.on_conflict_do_update(index_elements=[Project.key], set_=updates)
    await session.execute(statement)
    return (await session.execute(select(Project).where(Project.key == binding.scope.key)
                                  .execution_options(populate_existing=True))).scalar_one()


async def register_thread_project(session: AsyncSession, thread_id: str, binding: WorkspaceBinding) -> None:
    if ":sub:" in thread_id:
        return
    from agent.modules.conversations.models import ConversationThread

    thread = (await session.execute(select(ConversationThread).where(
        ConversationThread.thread_id == thread_id,
    ))).scalar_one_or_none()
    if thread is not None and (thread.deleted_at is not None or thread.kind not in {"user", "background"}):
        return
    from agent.modules.workspaces.models import ThreadWorkspace

    mapped = (await session.execute(select(ThreadWorkspace).where(
        ThreadWorkspace.thread_id == thread_id,
    ))).scalar_one_or_none()
    if mapped is not None and mapped.execution_locator:
        from agent.modules.workspaces.refs import bind_workspace_ref, workspace_ref_from_columns

        binding = bind_workspace_ref(workspace_ref_from_columns(
            backend=mapped.execution_backend, locator=mapped.execution_locator,
            label=mapped.execution_label, metadata_json=mapped.execution_metadata_json,
        ))
    await register_project(session, binding)
