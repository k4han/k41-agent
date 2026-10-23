"""Project catalog use cases."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, cast, func, or_, select

from agent.modules.projects.models import Project
from agent.modules.projects.repository import cursor_condition, encode_cursor, register_project, serialize_project
from agent.modules.workspaces import WorkspaceBinding, WorkspaceInput, bind_workspace_ref
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.infrastructure.db.session import get_async_session


async def list_projects(*, status: str = "active", search: str = "", limit: int = 20,
                        after: str | None = None) -> dict[str, Any]:
    session = await get_async_session()
    async with session:
        key = Project.key.collate("C" if session.get_bind().dialect.name == "postgresql" else "BINARY")
        statement = select(Project)
        if status != "all":
            statement = statement.where(Project.status == status)
        if search.strip():
            needle = search.strip()
            if session.get_bind().dialect.name == "postgresql":
                locator = cast(Project.workspace_json, JSON)["execution"]["locator"].as_string()
            else:
                locator = func.json_extract(Project.workspace_json, "$.execution.locator")
            statement = statement.where(or_(Project.label.icontains(needle, autoescape=True),
                                             Project.key.icontains(needle, autoescape=True),
                                             locator.icontains(needle, autoescape=True),
                                             Project.workspace_json.icontains(needle, autoescape=True)))
        if after:
            statement = statement.where(cursor_condition(Project.is_pinned, key, after))
        records = list((await session.execute(statement.order_by(Project.is_pinned.desc(), key)
                                             .limit(limit + 1))).scalars())
        rows = [serialize_project(record) for record in records[:limit]]
    from agent.modules.projects.workspaces import get_project_workspace_service
    if rows:
        descriptions = await get_project_workspace_service().describe_many([row["id"] for row in rows])
        rows = [dict(row, **descriptions[row["id"]]) for row in rows if row["id"] in descriptions]
    return {"projects": rows, "has_more": len(records) > limit,
            "next_cursor": encode_cursor(records[limit - 1].is_pinned, records[limit - 1].key) if len(records) > limit else None}


async def add_project(workspace: WorkspaceInput, *, label: str | None = None) -> dict[str, Any]:
    from agent.modules.workspaces import resolve_workspace_ref

    binding = WorkspaceBinding.model_validate(workspace) if isinstance(workspace, (WorkspaceBinding, dict)) and (
        isinstance(workspace, WorkspaceBinding) or "scope" in workspace
    ) else bind_workspace_ref(resolve_workspace_ref(workspace))
    from agent.modules.projects.workspaces import get_project_workspace_service
    projects = get_project_workspace_service()
    existing = await projects.find_binding(workspace)
    if existing:
        binding = existing
    session = await get_async_session()
    async with session:
        record = await register_project(session, binding, label=label, restore=True)
        if record is None:
            raise ValueError("Temporary workspaces cannot be added to the project catalog.")
        from agent.modules.projects.workspaces import register_workspace
        ready = record.migration_status == "ready"
        if ready:
            if existing is None:
                existing = await register_workspace(session, record, binding.execution)
        await session.commit()
        project_id = record.id
    if ready and existing is not None:
        projects.monitor(existing.project_workspace_id)
    return await get_project(project_id)


async def get_project(project_id: int) -> dict[str, Any]:
    session = await get_async_session()
    async with session:
        record = await session.get(Project, project_id)
        if record is None:
            raise KeyError("Project not found.")
        result = serialize_project(record)
    from agent.modules.projects.workspaces import get_project_workspace_service
    result.update(await get_project_workspace_service().describe(project_id))
    return result


async def update_project(project_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    session = await get_async_session()
    async with session:
        record = await session.get(Project, project_id)
        if record is None:
            raise KeyError("Project not found.")
        for field, value in changes.items():
            setattr(record, field, value)
        record.updated_at = utcnow()
        await session.commit()
    return await get_project(project_id)


async def import_existing_projects() -> None:
    """Insert missing scopes using the most recent live root conversation's workspace."""
    from agent.modules.conversations import project_previews_statement as _project_previews_statement, serialize_project_thread as _serialize_project_thread
    from agent.modules.agent_runtime import BackgroundTaskRecord
    from agent.modules.conversations import ConversationThread
    from agent.modules.workspaces import NO_WORKSPACE_KEY, TEMP_WORKSPACE_KEY
    from agent.modules.workspaces import workspace_ref_from_columns

    session = await get_async_session()
    async with session:
        after = None
        while True:
            rows = (await session.execute(_project_previews_statement(
                dialect=session.get_bind().dialect.name, limit=100, after=after,
                active_thread_id="", thread_limit=1,
            ))).all()
            for row in rows[:100]:
                summary = _serialize_project_thread(row)
                if summary["workspace_key"] in {TEMP_WORKSPACE_KEY, NO_WORKSPACE_KEY} or not summary["workspace"]:
                    continue
                binding = WorkspaceBinding.model_validate(summary["workspace"])
                binding.scope.key = summary["workspace_key"]
                await register_project(session, binding, only_missing=True)
            if len(rows) <= 100:
                break
            after = rows[99][1]
        # Queued tasks may have a saved workspace before their conversation exists.
        offset = 0
        while True:
            tasks = list((await session.execute(
                select(BackgroundTaskRecord)
                .outerjoin(ConversationThread, ConversationThread.thread_id == BackgroundTaskRecord.thread_id)
                .where(BackgroundTaskRecord.deleted_at.is_(None), ConversationThread.id.is_(None),
                       BackgroundTaskRecord.execution_locator.is_not(None))
                .order_by(BackgroundTaskRecord.updated_at.desc(), BackgroundTaskRecord.id.desc())
                .offset(offset).limit(100)
            )).scalars())
            for task in tasks:
                if ":sub:" in task.thread_id:
                    continue
                binding = bind_workspace_ref(workspace_ref_from_columns(
                    backend=task.execution_backend, locator=task.execution_locator,
                    label=task.execution_label, metadata_json=task.execution_metadata_json,
                ))
                if task.scope_key:
                    binding.scope.key = task.scope_key
                await register_project(session, binding, only_missing=True)
            if len(tasks) < 100:
                break
            offset += len(tasks)
        await session.commit()
