"""Bounded conversation previews grouped by stable workspace identity."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import JSON, String, case, cast, func, literal, or_, select, union_all

from agent.modules.agent_runtime.models import BackgroundTaskRecord
from agent.modules.conversations.models import ConversationThread
from agent.modules.conversations.repository import serialize_thread
from agent.modules.projects.models import Project
from agent.modules.projects.repository import cursor_condition, encode_cursor
from agent.modules.workspaces.constants import (
    NO_WORKSPACE_KEY,
    NO_WORKSPACE_LABEL,
    TEMP_WORKSPACE_KEY,
    TEMP_WORKSPACE_LABEL,
)
from agent.modules.workspaces.models import ThreadWorkspace
from agent.modules.workspaces.refs import bind_workspace_ref, workspace_ref_from_columns
from agent.shared.infrastructure.db.session import get_async_session
from agent.shared.thread_ids import resolve_thread_id

NAV_PROJECT_THREAD_LIMIT = 4


def _ranked_project_threads(*, dialect: str, active_thread_id: str, project_key_filter: str | None = None):
    has_mapping = ThreadWorkspace.execution_locator.is_not(None)

    def workspace_column(name: str):
        return case(
            (has_mapping, getattr(ThreadWorkspace, name)),
            else_=getattr(BackgroundTaskRecord, name),
        )

    metadata = func.coalesce(workspace_column("execution_metadata_json"), "{}")

    def metadata_text(key: str):
        if dialect == "sqlite":
            return cast(func.json_extract(metadata, f"$.{key}"), String)
        return cast(metadata, JSON)[key].as_string()

    is_temp = or_(
        metadata_text("temp").in_(["true", "1"]),
        func.lower(func.trim(metadata_text("source"))) == "temp",
    )
    project_key = case(
        (is_temp, TEMP_WORKSPACE_KEY),
        else_=func.coalesce(func.nullif(workspace_column("scope_key"), ""), NO_WORKSPACE_KEY),
    ).collate("C" if dialect == "postgresql" else "BINARY")
    order = [ConversationThread.updated_at.desc(), ConversationThread.id.desc()]
    if active_thread_id:
        order.insert(0, case((ConversationThread.thread_id == active_thread_id, 0), else_=1))

    ranked = (
        select(
            ConversationThread.id.label("conversation_id"),
            project_key.label("project_key"),
            *(workspace_column(name).label(name) for name in (
                "execution_backend", "execution_locator", "execution_label", "execution_metadata_json",
            )),
            func.row_number().over(partition_by=project_key, order_by=order).label("position"),
        )
        .outerjoin(ThreadWorkspace, ThreadWorkspace.thread_id == ConversationThread.thread_id)
        .outerjoin(
            BackgroundTaskRecord,
            (BackgroundTaskRecord.thread_id == ConversationThread.thread_id)
            & BackgroundTaskRecord.deleted_at.is_(None),
        )
        .where(
            ConversationThread.deleted_at.is_(None),
            ConversationThread.kind.in_(["user", "background"]),
        )
    )
    if project_key_filter is not None:
        ranked = ranked.where(project_key == project_key_filter)
    return ranked.cte("ranked_project_threads")


def _project_previews_statement(
    *, dialect: str, limit: int, after: str | None, active_thread_id: str,
    project_key_filter: str | None = None, thread_limit: int = NAV_PROJECT_THREAD_LIMIT, thread_offset: int = 0,
):
    ranked = _ranked_project_threads(dialect=dialect, active_thread_id=active_thread_id,
                                    project_key_filter=project_key_filter)
    # The cursor uses an immutable workspace key, so updating a conversation
    # cannot move a project across a page boundary.
    page = select(ranked.c.project_key).where(ranked.c.position == 1)
    if after is not None:
        page = page.where(ranked.c.project_key > after)
    page = page.order_by(ranked.c.project_key).limit(limit + 1).cte("project_page")
    return (
        select(
            ConversationThread,
            ranked.c.project_key,
            ranked.c.execution_backend,
            ranked.c.execution_locator,
            ranked.c.execution_label,
            ranked.c.execution_metadata_json,
        )
        .join(ranked, ranked.c.conversation_id == ConversationThread.id)
        .join(page, page.c.project_key == ranked.c.project_key)
        .where(ranked.c.position > thread_offset, ranked.c.position <= thread_offset + thread_limit)
        .order_by(ranked.c.project_key, ConversationThread.updated_at.desc(), ConversationThread.id.desc())
    )


def _serialize_project_thread(row) -> dict[str, Any]:
    thread, key, backend, locator, label, metadata_json = row
    binding = None
    if locator:
        binding = bind_workspace_ref(workspace_ref_from_columns(
            backend=backend, locator=locator, label=label, metadata_json=metadata_json,
        ))
    project_label = (
        TEMP_WORKSPACE_LABEL if key == TEMP_WORKSPACE_KEY
        else binding.scope.label if binding else NO_WORKSPACE_LABEL
    )
    return {
        **serialize_thread(thread),
        "workspace": binding.model_dump() if binding else None,
        "workspace_key": key,
        "workspace_label": project_label,
    }


async def list_project_threads(*, project_key: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Page through one project's conversations without loading checkpoints."""
    session = await get_async_session()
    async with session:
        statement = _project_previews_statement(
            dialect=session.get_bind().dialect.name, limit=1, after=None, active_thread_id="",
            project_key_filter=project_key, thread_limit=limit + 1, thread_offset=offset,
        )
        rows = (await session.execute(statement)).all()
    return {
        "threads": [_serialize_project_thread(row) for row in rows[:limit]],
        "has_more": len(rows) > limit,
        "next_offset": offset + min(len(rows), limit),
    }


async def list_conversation_projects(
    *, limit: int = 20, after: str | None = None, active_thread_id: str = "",
) -> dict[str, Any]:
    """Page catalog entries, including empty projects, with bounded conversation previews."""
    session = await get_async_session()
    async with session:
        dialect = session.get_bind().dialect.name
        ranked = _ranked_project_threads(
            dialect=dialect,
            active_thread_id=resolve_thread_id(active_thread_id) if active_thread_id else "",
        )
        candidates = union_all(
            select(Project.id, Project.key, Project.label, Project.is_pinned, Project.workspace_json,
                   Project.status, Project.created_at, Project.updated_at).where(Project.status == "active"),
            select(literal(None), ranked.c.project_key,
                   case((ranked.c.project_key == TEMP_WORKSPACE_KEY, TEMP_WORKSPACE_LABEL), else_=NO_WORKSPACE_LABEL),
                   literal(False), literal(None), literal("active"), literal(None), literal(None))
            .where(ranked.c.position == 1,
                   ranked.c.project_key.in_([TEMP_WORKSPACE_KEY, NO_WORKSPACE_KEY])),
        ).cte("navigation_projects")
        key = candidates.c.key.collate("C" if dialect == "postgresql" else "BINARY")
        statement = select(candidates)
        if after:
            statement = statement.where(cursor_condition(candidates.c.is_pinned, key, after))
        records = (await session.execute(statement.order_by(candidates.c.is_pinned.desc(), key)
                                         .limit(limit + 1))).mappings().all()
        has_more = len(records) > limit
        groups = {
            row["key"]: {
                "id": row["id"], "key": row["key"], "label": row["label"],
                "is_pinned": row["is_pinned"], "status": row["status"],
                "workspace": json.loads(row["workspace_json"]) if row["workspace_json"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                "threads": [],
            } for row in records[:limit]
        }
        if groups:
            rows = (await session.execute(
                select(ConversationThread, ranked.c.project_key, ranked.c.execution_backend,
                       ranked.c.execution_locator, ranked.c.execution_label, ranked.c.execution_metadata_json)
                .join(ranked, ranked.c.conversation_id == ConversationThread.id)
                .where(ranked.c.project_key.in_(list(groups)), ranked.c.position <= NAV_PROJECT_THREAD_LIMIT)
                .order_by(ConversationThread.updated_at.desc(), ConversationThread.id.desc())
            )).all()
            for row in rows:
                summary = _serialize_project_thread(row)
                group = groups[summary["workspace_key"]]
                summary["workspace_label"] = group["label"]
                group["threads"].append(summary)
    projects = list(groups.values())
    return {
        "projects": projects,
        "has_more": has_more,
        "next_cursor": encode_cursor(projects[-1]["is_pinned"], projects[-1]["key"]) if has_more else None,
    }
