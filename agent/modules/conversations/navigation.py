"""Bounded conversation previews grouped by stable workspace identity."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, String, case, cast, func, or_, select

from agent.modules.agent_runtime.models import BackgroundTaskRecord
from agent.modules.conversations.models import ConversationThread
from agent.modules.conversations.repository import serialize_thread
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


def _project_previews_statement(*, dialect: str, limit: int, after: str | None, active_thread_id: str):
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
        .cte("ranked_project_threads")
    )
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
        .where(ranked.c.position <= NAV_PROJECT_THREAD_LIMIT)
        .order_by(ranked.c.project_key, ConversationThread.updated_at.desc(), ConversationThread.id.desc())
    )


async def list_conversation_projects(
    *, limit: int = 20, after: str | None = None, active_thread_id: str = "",
) -> dict[str, Any]:
    """Read at most four previews per project without loading checkpoints."""
    session = await get_async_session()
    async with session:
        statement = _project_previews_statement(
            dialect=session.get_bind().dialect.name,
            limit=limit,
            after=after,
            active_thread_id=resolve_thread_id(active_thread_id) if active_thread_id else "",
        )
        rows = (await session.execute(statement)).all()

    groups: dict[str, dict[str, Any]] = {}
    for thread, key, backend, locator, label, metadata_json in rows:
        binding = None
        if locator:
            binding = bind_workspace_ref(workspace_ref_from_columns(
                backend=backend, locator=locator, label=label, metadata_json=metadata_json,
            ))
        project_label = (
            TEMP_WORKSPACE_LABEL if key == TEMP_WORKSPACE_KEY
            else binding.scope.label if binding else NO_WORKSPACE_LABEL
        )
        if key not in groups:
            groups[key] = {
                "key": key,
                "label": project_label,
                "threads": [],
            }
        groups[key]["threads"].append({
            **serialize_thread(thread),
            "workspace": binding.model_dump() if binding else None,
            "workspace_key": key,
            "workspace_label": project_label,
        })

    projects = list(groups.values())
    has_more = len(projects) > limit
    projects = projects[:limit]
    return {
        "projects": projects,
        "has_more": has_more,
        "next_cursor": projects[-1]["key"] if has_more else None,
    }
