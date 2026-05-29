from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, distinct, func, select

from agent.modules.usage.models import LLMUsageEvent
from agent.shared.infrastructure.db.session import get_async_session


@dataclass(frozen=True, slots=True)
class UsageEventInput:
    thread_id: str
    root_thread_id: str
    platform: str
    user_id: str
    channel_id: str
    agent_name: str
    provider_name: str
    model_name: str
    call_kind: str
    internal: bool
    has_usage_metadata: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    input_token_details: dict[str, Any] | None = None
    output_token_details: dict[str, Any] | None = None
    usage_metadata: dict[str, Any] | None = None
    run_id: str = ""
    parent_run_id: str = ""
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UsageQuery:
    start: datetime
    end: datetime
    platform: str = ""
    user_id: str = ""
    channel_id: str = ""
    agent_name: str = ""
    provider_name: str = ""
    model_name: str = ""
    limit: int = 50
    offset: int = 0


def _trim(value: object, max_length: int) -> str:
    return str(value or "").strip()[:max_length]


def _json_or_none(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class LLMUsageRepository:
    async def record(self, event: UsageEventInput) -> dict[str, Any]:
        values = {
            "thread_id": _trim(event.thread_id, 512),
            "root_thread_id": _trim(event.root_thread_id, 512),
            "platform": _trim(event.platform or "unknown", 50) or "unknown",
            "user_id": _trim(event.user_id, 255),
            "channel_id": _trim(event.channel_id, 255),
            "agent_name": _trim(event.agent_name, 255),
            "provider_name": _trim(event.provider_name, 255),
            "model_name": _trim(event.model_name, 255),
            "call_kind": _trim(event.call_kind or "agent", 64) or "agent",
            "internal": bool(event.internal),
            "has_usage_metadata": bool(event.has_usage_metadata),
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "total_tokens": event.total_tokens,
            "input_token_details_json": _json_or_none(event.input_token_details),
            "output_token_details_json": _json_or_none(event.output_token_details),
            "usage_metadata_json": _json_or_none(event.usage_metadata),
            "run_id": _trim(event.run_id, 64),
            "parent_run_id": _trim(event.parent_run_id, 64),
        }
        if event.created_at is not None:
            values["created_at"] = event.created_at
        record = LLMUsageEvent(**values)
        session = await get_async_session()
        async with session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return serialize_usage_event(record)

    async def summary(self, query: UsageQuery) -> dict[str, int]:
        stmt = select(
            func.count(LLMUsageEvent.id),
            func.coalesce(func.sum(LLMUsageEvent.input_tokens), 0),
            func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0),
            func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0),
            func.coalesce(
                func.sum(case((LLMUsageEvent.has_usage_metadata.is_(False), 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((LLMUsageEvent.internal.is_(True), 1), else_=0)),
                0,
            ),
        ).where(*_where_clauses(query))

        session = await get_async_session()
        async with session:
            row = (await session.execute(stmt)).one()
        event_count = int(row[0] or 0)
        missing_usage_count = int(row[4] or 0)
        return {
            "event_count": event_count,
            "input_tokens": int(row[1] or 0),
            "output_tokens": int(row[2] or 0),
            "total_tokens": int(row[3] or 0),
            "missing_usage_count": missing_usage_count,
            "known_usage_count": max(0, event_count - missing_usage_count),
            "internal_event_count": int(row[5] or 0),
        }

    async def grouped_by_identity(self, query: UsageQuery) -> tuple[list[dict[str, Any]], int]:
        total_expr = func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0)
        input_expr = func.coalesce(func.sum(LLMUsageEvent.input_tokens), 0)
        output_expr = func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0)
        missing_expr = func.coalesce(
            func.sum(case((LLMUsageEvent.has_usage_metadata.is_(False), 1), else_=0)),
            0,
        )
        internal_expr = func.coalesce(
            func.sum(case((LLMUsageEvent.internal.is_(True), 1), else_=0)),
            0,
        )

        base = (
            select(
                LLMUsageEvent.platform,
                LLMUsageEvent.user_id,
                LLMUsageEvent.channel_id,
                func.count(LLMUsageEvent.id).label("event_count"),
                input_expr.label("input_tokens"),
                output_expr.label("output_tokens"),
                total_expr.label("total_tokens"),
                missing_expr.label("missing_usage_count"),
                internal_expr.label("internal_event_count"),
                func.max(LLMUsageEvent.created_at).label("last_used_at"),
            )
            .where(*_where_clauses(query))
            .group_by(
                LLMUsageEvent.platform,
                LLMUsageEvent.user_id,
                LLMUsageEvent.channel_id,
            )
        )
        count_stmt = select(func.count()).select_from(base.subquery())
        rows_stmt = (
            base.order_by(total_expr.desc(), func.max(LLMUsageEvent.created_at).desc())
            .limit(query.limit)
            .offset(query.offset)
        )

        session = await get_async_session()
        async with session:
            total = int((await session.execute(count_stmt)).scalar_one() or 0)
            result = await session.execute(rows_stmt)

        rows = [
            {
                "platform": row.platform,
                "user_id": row.user_id,
                "channel_id": row.channel_id,
                "event_count": int(row.event_count or 0),
                "input_tokens": int(row.input_tokens or 0),
                "output_tokens": int(row.output_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "missing_usage_count": int(row.missing_usage_count or 0),
                "internal_event_count": int(row.internal_event_count or 0),
                "last_used_at": _iso(row.last_used_at),
            }
            for row in result.all()
        ]
        return rows, total

    async def filter_options(self, query: UsageQuery) -> dict[str, list[Any]]:
        clauses = [
            LLMUsageEvent.created_at >= query.start,
            LLMUsageEvent.created_at <= query.end,
        ]
        session = await get_async_session()
        async with session:
            platforms = await _distinct_strings(session, LLMUsageEvent.platform, clauses)
            agents = await _distinct_strings(session, LLMUsageEvent.agent_name, clauses)
            providers = await _distinct_strings(session, LLMUsageEvent.provider_name, clauses)
            models = await _distinct_strings(session, LLMUsageEvent.model_name, clauses)

            user_rows = (
                await session.execute(
                    select(
                        distinct(LLMUsageEvent.platform),
                        LLMUsageEvent.user_id,
                    )
                    .where(*clauses, LLMUsageEvent.user_id != "")
                    .order_by(LLMUsageEvent.platform.asc(), LLMUsageEvent.user_id.asc())
                )
            ).all()
            channel_rows = (
                await session.execute(
                    select(
                        distinct(LLMUsageEvent.platform),
                        LLMUsageEvent.user_id,
                        LLMUsageEvent.channel_id,
                    )
                    .where(*clauses, LLMUsageEvent.channel_id != "")
                    .order_by(
                        LLMUsageEvent.platform.asc(),
                        LLMUsageEvent.user_id.asc(),
                        LLMUsageEvent.channel_id.asc(),
                    )
                )
            ).all()

        return {
            "platforms": platforms,
            "agents": agents,
            "providers": providers,
            "models": models,
            "users": [
                {"platform": row[0], "user_id": row[1]}
                for row in user_rows
                if row[0] and row[1]
            ],
            "channels": [
                {"platform": row[0], "user_id": row[1], "channel_id": row[2]}
                for row in channel_rows
                if row[0] and row[1] and row[2]
            ],
        }

    async def prune_before(self, cutoff: datetime) -> int:
        session = await get_async_session()
        async with session:
            result = await session.execute(
                delete(LLMUsageEvent).where(LLMUsageEvent.created_at < cutoff)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def aggregate_by_thread(self, thread_id: str) -> dict[str, Any]:
        session = await get_async_session()
        stmt = (
            select(
                LLMUsageEvent.model_name,
                LLMUsageEvent.provider_name,
                func.count(LLMUsageEvent.id).label("calls"),
                func.coalesce(func.sum(LLMUsageEvent.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0).label("total_tokens"),
            )
            .where(
                (LLMUsageEvent.thread_id == thread_id) | (LLMUsageEvent.root_thread_id == thread_id)
            )
            .group_by(LLMUsageEvent.model_name, LLMUsageEvent.provider_name)
        )
        async with session:
            result = await session.execute(stmt)
            rows = result.all()

        models = []
        total_tokens = 0
        input_tokens = 0
        output_tokens = 0

        for row in rows:
            t = int(row.total_tokens or 0)
            i = int(row.input_tokens or 0)
            o = int(row.output_tokens or 0)
            total_tokens += t
            input_tokens += i
            output_tokens += o
            models.append({
                "model": row.model_name,
                "provider": row.provider_name,
                "calls": int(row.calls or 0),
                "input_tokens": i,
                "output_tokens": o,
                "total_tokens": t,
            })

        for m in models:
            m["percentage"] = round((m["total_tokens"] / total_tokens * 100), 1) if total_tokens > 0 else 0.0

        models.sort(key=lambda x: x["total_tokens"], reverse=True)

        return {
            "thread_id": thread_id,
            "total_tokens": total_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "models": models,
        }

    async def aggregate_by_workspace(self, backend: str, locator: str) -> dict[str, Any]:
        from agent.modules.workspaces.models import ThreadWorkspace

        session = await get_async_session()
        thread_stmt = (
            select(ThreadWorkspace.thread_id)
            .where(
                (ThreadWorkspace.workspace_backend == backend) &
                (ThreadWorkspace.workspace_locator == locator)
            )
        )
        async with session:
            thread_result = await session.execute(thread_stmt)
            thread_ids = [row[0] for row in thread_result.all() if row[0]]

        if not thread_ids:
            return {
                "backend": backend,
                "locator": locator,
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "models": [],
            }

        stmt = (
            select(
                LLMUsageEvent.model_name,
                LLMUsageEvent.provider_name,
                func.count(LLMUsageEvent.id).label("calls"),
                func.coalesce(func.sum(LLMUsageEvent.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0).label("total_tokens"),
            )
            .where(
                LLMUsageEvent.thread_id.in_(thread_ids) | LLMUsageEvent.root_thread_id.in_(thread_ids)
            )
            .group_by(LLMUsageEvent.model_name, LLMUsageEvent.provider_name)
        )

        async with session:
            result = await session.execute(stmt)
            rows = result.all()

        models = []
        total_tokens = 0
        input_tokens = 0
        output_tokens = 0

        for row in rows:
            t = int(row.total_tokens or 0)
            i = int(row.input_tokens or 0)
            o = int(row.output_tokens or 0)
            total_tokens += t
            input_tokens += i
            output_tokens += o
            models.append({
                "model": row.model_name,
                "provider": row.provider_name,
                "calls": int(row.calls or 0),
                "input_tokens": i,
                "output_tokens": o,
                "total_tokens": t,
            })

        for m in models:
            m["percentage"] = round((m["total_tokens"] / total_tokens * 100), 1) if total_tokens > 0 else 0.0

        models.sort(key=lambda x: x["total_tokens"], reverse=True)

        return {
            "backend": backend,
            "locator": locator,
            "total_tokens": total_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "models": models,
        }

    async def aggregate_workspaces(self, query: UsageQuery) -> list[dict[str, Any]]:
        from agent.modules.workspaces.models import ThreadWorkspace

        session = await get_async_session()
        stmt = (
            select(
                ThreadWorkspace.workspace_backend,
                ThreadWorkspace.workspace_locator,
                ThreadWorkspace.workspace_label,
                func.count(distinct(ThreadWorkspace.thread_id)).label("thread_count"),
                func.count(LLMUsageEvent.id).label("event_count"),
                func.coalesce(func.sum(LLMUsageEvent.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0).label("total_tokens"),
                func.max(LLMUsageEvent.created_at).label("last_used_at"),
            )
            .join(
                LLMUsageEvent,
                (LLMUsageEvent.thread_id == ThreadWorkspace.thread_id) |
                (LLMUsageEvent.root_thread_id == ThreadWorkspace.thread_id)
            )
            .where(*_where_clauses(query))
            .group_by(
                ThreadWorkspace.workspace_backend,
                ThreadWorkspace.workspace_locator,
                ThreadWorkspace.workspace_label
            )
            .order_by(func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0).desc())
        )

        async with session:
            result = await session.execute(stmt)
            workspace_rows = result.all()

        if not workspace_rows:
            return []

        breakdown_stmt = (
            select(
                ThreadWorkspace.workspace_backend,
                ThreadWorkspace.workspace_locator,
                LLMUsageEvent.model_name,
                LLMUsageEvent.provider_name,
                func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0).label("total_tokens"),
            )
            .join(
                LLMUsageEvent,
                (LLMUsageEvent.thread_id == ThreadWorkspace.thread_id) |
                (LLMUsageEvent.root_thread_id == ThreadWorkspace.thread_id)
            )
            .where(*_where_clauses(query))
            .group_by(
                ThreadWorkspace.workspace_backend,
                ThreadWorkspace.workspace_locator,
                LLMUsageEvent.model_name,
                LLMUsageEvent.provider_name
            )
        )

        async with session:
            breakdown_result = await session.execute(breakdown_stmt)
            breakdown_rows = breakdown_result.all()

        breakdowns = {}
        for r in breakdown_rows:
            key = (r.workspace_backend, r.workspace_locator)
            if key not in breakdowns:
                breakdowns[key] = []
            breakdowns[key].append({
                "model": r.model_name,
                "provider": r.provider_name,
                "total_tokens": int(r.total_tokens or 0)
            })

        workspaces = []
        for row in workspace_rows:
            key = (row.workspace_backend, row.workspace_locator)
            total = int(row.total_tokens or 0)

            model_details = breakdowns.get(key, [])
            for md in model_details:
                md["percentage"] = round((md["total_tokens"] / total * 100), 1) if total > 0 else 0.0
            model_details.sort(key=lambda x: x["total_tokens"], reverse=True)

            workspaces.append({
                "backend": row.workspace_backend,
                "locator": row.workspace_locator,
                "label": row.workspace_label or row.workspace_locator,
                "thread_count": int(row.thread_count or 0),
                "event_count": int(row.event_count or 0),
                "input_tokens": int(row.input_tokens or 0),
                "output_tokens": int(row.output_tokens or 0),
                "total_tokens": total,
                "last_used_at": _iso(row.last_used_at),
                "models": model_details
            })

        return workspaces

    async def aggregate_threads(self, query: UsageQuery) -> list[dict[str, Any]]:
        from agent.modules.conversations.models import ConversationThread

        session = await get_async_session()
        stmt = (
            select(
                LLMUsageEvent.thread_id,
                ConversationThread.title,
                ConversationThread.agent_name,
                func.count(LLMUsageEvent.id).label("event_count"),
                func.coalesce(func.sum(LLMUsageEvent.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0).label("total_tokens"),
                func.max(LLMUsageEvent.created_at).label("last_used_at"),
            )
            .outerjoin(ConversationThread, LLMUsageEvent.thread_id == ConversationThread.thread_id)
            .where(*_where_clauses(query))
            .group_by(
                LLMUsageEvent.thread_id,
                ConversationThread.title,
                ConversationThread.agent_name
            )
            .order_by(func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0).desc())
        )

        async with session:
            result = await session.execute(stmt)
            thread_rows = result.all()

        if not thread_rows:
            return []

        breakdown_stmt = (
            select(
                LLMUsageEvent.thread_id,
                LLMUsageEvent.model_name,
                LLMUsageEvent.provider_name,
                func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0).label("total_tokens"),
            )
            .where(*_where_clauses(query))
            .group_by(
                LLMUsageEvent.thread_id,
                LLMUsageEvent.model_name,
                LLMUsageEvent.provider_name
            )
        )

        async with session:
            breakdown_result = await session.execute(breakdown_stmt)
            breakdown_rows = breakdown_result.all()

        breakdowns = {}
        for r in breakdown_rows:
            tid = r.thread_id
            if tid not in breakdowns:
                breakdowns[tid] = []
            breakdowns[tid].append({
                "model": r.model_name,
                "provider": r.provider_name,
                "total_tokens": int(r.total_tokens or 0)
            })

        threads = []
        for row in thread_rows:
            tid = row.thread_id
            total = int(row.total_tokens or 0)

            model_details = breakdowns.get(tid, [])
            for md in model_details:
                md["percentage"] = round((md["total_tokens"] / total * 100), 1) if total > 0 else 0.0
            model_details.sort(key=lambda x: x["total_tokens"], reverse=True)

            threads.append({
                "thread_id": tid,
                "title": row.title or tid,
                "agent_name": row.agent_name or "unknown",
                "event_count": int(row.event_count or 0),
                "input_tokens": int(row.input_tokens or 0),
                "output_tokens": int(row.output_tokens or 0),
                "total_tokens": total,
                "last_used_at": _iso(row.last_used_at),
                "models": model_details
            })

        return threads


def _where_clauses(query: UsageQuery) -> list[Any]:
    clauses: list[Any] = [
        LLMUsageEvent.created_at >= query.start,
        LLMUsageEvent.created_at <= query.end,
    ]
    if query.platform:
        clauses.append(LLMUsageEvent.platform == query.platform)
    if query.user_id:
        clauses.append(LLMUsageEvent.user_id == query.user_id)
    if query.channel_id:
        clauses.append(LLMUsageEvent.channel_id == query.channel_id)
    if query.agent_name:
        clauses.append(LLMUsageEvent.agent_name == query.agent_name)
    if query.provider_name:
        clauses.append(LLMUsageEvent.provider_name == query.provider_name)
    if query.model_name:
        clauses.append(LLMUsageEvent.model_name == query.model_name)
    return clauses


async def _distinct_strings(session: Any, column: Any, clauses: list[Any]) -> list[str]:
    result = await session.execute(
        select(distinct(column))
        .where(*clauses, column != "")
        .order_by(column.asc())
    )
    return [str(value or "") for value in result.scalars().all() if str(value or "").strip()]


def serialize_usage_event(record: LLMUsageEvent) -> dict[str, Any]:
    return {
        "id": record.id,
        "created_at": _iso(record.created_at),
        "thread_id": record.thread_id,
        "root_thread_id": record.root_thread_id,
        "platform": record.platform,
        "user_id": record.user_id,
        "channel_id": record.channel_id,
        "agent_name": record.agent_name,
        "provider_name": record.provider_name,
        "model_name": record.model_name,
        "call_kind": record.call_kind,
        "internal": record.internal,
        "has_usage_metadata": record.has_usage_metadata,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "total_tokens": record.total_tokens,
        "run_id": record.run_id,
        "parent_run_id": record.parent_run_id,
    }


__all__ = [
    "LLMUsageRepository",
    "UsageEventInput",
    "UsageQuery",
    "serialize_usage_event",
]
