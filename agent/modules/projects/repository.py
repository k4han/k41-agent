"""Transactional catalog operations shared by workspace writers and the dashboard."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import JSON, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent.modules.projects.models import Project, ProjectWorkspace, WorkspaceRuntime
from agent.modules.workspaces import WorkspaceBinding
from agent.shared.infrastructure.db.base import utcnow


def serialize_project(record: Project) -> dict[str, Any]:
    return {
        "id": record.id, "key": record.key, "label": record.label,
        "status": record.status, "is_pinned": record.is_pinned,
        "workspace": json.loads(record.workspace_json),
        "migration_status": record.migration_status,
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
    if binding.project_id:
        record = await session.get(Project, binding.project_id)
        if record is None or record.key != binding.scope.key:
            raise ValueError("Project reference does not match the workspace identity.")
        if restore:
            record.status = "active"
        return record
    from agent.modules.projects.workspaces import source_key

    def field(column, *path):
        if session.get_bind().dialect.name == "postgresql":
            value = cast(column, JSON)
            for name in path:
                value = value[name]
            return value.as_string()
        return func.json_extract(column, "$." + ".".join(path))

    def matches(column):
        return and_(field(column, "backend") == binding.execution.backend,
                    field(column, "locator") == binding.execution.locator,
                    func.coalesce(field(column, "metadata", "root"), "") == metadata.get("root", ""))

    current = (await session.execute(select(Project).join(
        ProjectWorkspace, ProjectWorkspace.project_id == Project.id,
    ).join(WorkspaceRuntime, WorkspaceRuntime.project_workspace_id == ProjectWorkspace.id).where(or_(
        matches(WorkspaceRuntime.execution_json), matches(ProjectWorkspace.source_json),
        ProjectWorkspace.runtime_aliases_json.contains('"' + source_key(binding.execution) + '"'),
    )).limit(1))).scalar_one_or_none()
    if not only_missing and current is None and metadata.get("source") == "github" and metadata.get("repository_full_name"):
        current = (await session.execute(select(Project).where(
            field(Project.workspace_json, "execution", "metadata", "repository_full_name") == metadata["repository_full_name"],
        ).limit(1))).scalar_one_or_none()
    if current is not None:
        if restore:
            current.status = "active"
        return current
    now = utcnow()
    values = {
        "key": binding.scope.key, "label": (label or binding.scope.label or binding.scope.key)[:255],
        "status": "active", "is_pinned": False,
        "workspace_json": binding.model_dump_json(), "created_at": now, "updated_at": now,
        "migration_status": "pending" if only_missing else "ready",
    }
    insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(Project).values(**values)
    if only_missing:
        statement = statement.on_conflict_do_nothing(index_elements=[Project.key])
    else:
        updates = {"updated_at": now}
        if restore:
            updates["status"] = "active"
        statement = statement.on_conflict_do_update(index_elements=[Project.key], set_=updates)
    await session.execute(statement)
    return (await session.execute(select(Project).where(Project.key == binding.scope.key)
                                  .execution_options(populate_existing=True))).scalar_one()


async def register_thread_project(session: AsyncSession, thread_id: str, binding: WorkspaceBinding) -> WorkspaceBinding:
    if ":sub:" in thread_id:
        from agent.modules.workspaces import ThreadWorkspace
        from agent.modules.workspaces import binding_from_record
        parent = (await session.execute(select(ThreadWorkspace).where(
            ThreadWorkspace.thread_id == thread_id.split(":sub:", 1)[0],
        ))).scalar_one_or_none()
        if parent is not None and parent.project_workspace_id:
            return binding_from_record(parent, binding.execution)
        return binding
    from agent.modules.conversations import ConversationThread

    thread = (await session.execute(select(ConversationThread).where(
        ConversationThread.thread_id == thread_id,
    ))).scalar_one_or_none()
    if thread is not None and (thread.deleted_at is not None or thread.kind not in {"user", "background"}):
        return binding
    from agent.modules.workspaces import ThreadWorkspace

    mapped = (await session.execute(select(ThreadWorkspace).where(
        ThreadWorkspace.thread_id == thread_id,
    ))).scalar_one_or_none()
    if mapped is not None and mapped.execution_locator and not binding.project_id:
        from agent.modules.workspaces import binding_from_record, workspace_ref_from_columns

        binding = binding_from_record(mapped, workspace_ref_from_columns(
            backend=mapped.execution_backend, locator=mapped.execution_locator,
            label=mapped.execution_label, metadata_json=mapped.execution_metadata_json,
        ))
    project = await register_project(session, binding)
    if project is None:
        return binding
    from agent.modules.projects.workspaces import _binding, register_workspace
    if binding.project_workspace_id:
        workspace = await session.get(ProjectWorkspace, binding.project_workspace_id)
        if workspace is None or workspace.project_id != project.id:
            raise ValueError("Project workspace ownership does not match the project.")
        runtime = (await session.execute(select(WorkspaceRuntime).where(
            WorkspaceRuntime.project_workspace_id == workspace.id,
        ))).scalar_one()
        return _binding(project, workspace, runtime)
    if project.local_workspace_id:
        workspace = await session.get(ProjectWorkspace, project.local_workspace_id)
        runtime = (await session.execute(select(WorkspaceRuntime).where(
            WorkspaceRuntime.project_workspace_id == workspace.id,
        ))).scalar_one()
        from agent.modules.projects.workspaces import source_key
        from agent.modules.workspaces import WorkspaceRef
        if source_key(binding.execution) == source_key(WorkspaceRef.model_validate_json(runtime.execution_json)):
            return _binding(project, workspace, runtime)
    kind = "github_task" if binding.execution.metadata.get("workspace_kind") == "github_task" else "shared"
    if project.migration_status != "ready":
        kind = "legacy" if kind == "shared" else kind
    owned = await register_workspace(session, project, binding.execution, kind=kind)
    if mapped is not None:
        mapped.project_id, mapped.project_workspace_id = project.id, owned.project_workspace_id
    return owned
