from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, distinct, func, select

from agent.modules.usage.models import LLMUsageEvent
from agent.modules.usage.context_breakdown import CONTEXT_CATEGORIES, include_response_in_context
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
    call_kind: str = ""
    internal: bool | None = None
    limit: int = 50
    offset: int = 0


def _trim(value: object, max_length: int) -> str:
    return str(value or "").strip()[:max_length]


def _json_or_none(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _sanitize_context_breakdown(value: Any) -> dict[str, int] | None:
    """Validate stored breakdowns before arithmetic.

    Writer output is controlled, but the API also reads old or manually
    written rows. Mirror the frontend ``parseContextBreakdown`` guard so a
    malformed dict yields ``None`` instead of a 500 from ``+``/``.get``.
    """
    if not isinstance(value, dict):
        return None
    sanitized: dict[str, int] = {}
    for key in CONTEXT_CATEGORIES:
        raw = value.get(key, 0)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            return None
        sanitized[key] = raw
    return sanitized


def _root_thread_expr() -> Any:
    return func.coalesce(
        func.nullif(LLMUsageEvent.root_thread_id, ""),
        LLMUsageEvent.thread_id,
    )


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
            call_kinds = await _distinct_strings(session, LLMUsageEvent.call_kind, clauses)

            user_rows = (
                await session.execute(
                    select(
                        LLMUsageEvent.platform,
                        LLMUsageEvent.user_id,
                    )
                    .distinct()
                    .where(*clauses, LLMUsageEvent.user_id != "")
                    .order_by(LLMUsageEvent.platform.asc(), LLMUsageEvent.user_id.asc())
                )
            ).all()
            channel_rows = (
                await session.execute(
                    select(
                        LLMUsageEvent.platform,
                        LLMUsageEvent.user_id,
                        LLMUsageEvent.channel_id,
                    )
                    .distinct()
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
            "call_kinds": call_kinds,
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
            latest_event = await self._latest_context_event(session, thread_id)
            compaction = await self._latest_compaction_event(session, thread_id)
            context_estimated = compaction is not None and (
                latest_event is None or self._is_newer(compaction, latest_event)
            )
            retained_tokens = None
            compacted_breakdown = None
            if context_estimated:
                compaction_metadata = _json_dict(compaction.usage_metadata_json)
                retained_tokens = compaction_metadata["retained_tokens"]
                compacted_breakdown = _sanitize_context_breakdown(compaction_metadata.get("context_breakdown"))
                if compacted_breakdown is not None:
                    retained_tokens = sum(compacted_breakdown.values())
                latest_event = None

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

        context_metadata = _json_dict(latest_event.usage_metadata_json) if latest_event else {}
        input_breakdown = _sanitize_context_breakdown(context_metadata.get("context_breakdown"))
        output_breakdown = _sanitize_context_breakdown(
            context_metadata.get("context_output_breakdown")
        )
        latest_input_tokens = max(0, int(latest_event.input_tokens or 0)) if latest_event else 0
        latest_output_tokens = max(0, int(latest_event.output_tokens or 0)) if latest_event else 0
        context_breakdown = compacted_breakdown if context_estimated else (
            include_response_in_context(input_breakdown, latest_output_tokens, output_breakdown)
            if input_breakdown is not None else None
        )

        return {
            "thread_id": thread_id,
            "total_tokens": total_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "current_context_tokens": retained_tokens if context_estimated else latest_input_tokens + latest_output_tokens,
            "context_estimated": context_estimated,
            "latest_input_tokens": latest_input_tokens,
            "has_context_usage": latest_event is not None,
            "context_breakdown": context_breakdown,
            "latest_output_tokens": latest_output_tokens,
            "latest_total_tokens": int(latest_event.total_tokens or 0) if latest_event else 0,
            "latest_model": latest_event.model_name if latest_event else "",
            "latest_provider": latest_event.provider_name if latest_event else "",
            "latest_used_at": _iso(latest_event.created_at) if latest_event else None,
            "models": models,
        }

    async def latest_context_breakdown(self, thread_id: str) -> dict[str, int] | None:
        """Read the latest provider input composition, including before compaction."""
        session = await get_async_session()
        async with session:
            event = await self._latest_context_event(session, thread_id)
        metadata = _json_dict(event.usage_metadata_json) if event else {}
        return _sanitize_context_breakdown(metadata.get("context_breakdown"))

    async def _latest_context_event(self, session: Any, thread_id: str) -> LLMUsageEvent | None:
        common_clauses = [
            LLMUsageEvent.internal.is_(False),
            LLMUsageEvent.has_usage_metadata.is_(True),
            LLMUsageEvent.input_tokens.is_not(None),
        ]
        order_by = (LLMUsageEvent.created_at.desc(), LLMUsageEvent.id.desc())

        exact_result = await session.execute(
            select(LLMUsageEvent)
            .where(LLMUsageEvent.thread_id == thread_id, *common_clauses)
            .order_by(*order_by)
            .limit(1)
        )
        exact_event = exact_result.scalars().first()
        if exact_event is not None:
            return exact_event

        root_result = await session.execute(
            select(LLMUsageEvent)
            .where(
                (LLMUsageEvent.thread_id == thread_id)
                | (LLMUsageEvent.root_thread_id == thread_id),
                *common_clauses,
            )
            .order_by(*order_by)
            .limit(1)
        )
        return root_result.scalars().first()

    async def _latest_compaction_event(
        self, session: Any, thread_id: str
    ) -> LLMUsageEvent | None:
        """Return an applied compaction marker, excluding summarizer calls."""
        order_by = (LLMUsageEvent.created_at.desc(), LLMUsageEvent.id.desc())
        result = await session.execute(
            select(LLMUsageEvent)
            .where(
                LLMUsageEvent.thread_id == thread_id,
                LLMUsageEvent.call_kind == "compaction",
                LLMUsageEvent.internal.is_(True),
                LLMUsageEvent.has_usage_metadata.is_(False),
                LLMUsageEvent.usage_metadata_json.is_not(None),
            )
            .order_by(*order_by)
        )
        # The compactor writes retained_tokens only after updating the checkpoint.
        for event in result.scalars():
            retained_tokens = _json_dict(event.usage_metadata_json).get("retained_tokens")
            if isinstance(retained_tokens, int) and not isinstance(retained_tokens, bool) and retained_tokens >= 0:
                return event
        return None

    @staticmethod
    def _is_newer(first: LLMUsageEvent, second: LLMUsageEvent) -> bool:
        first_created = getattr(first, "created_at", None)
        second_created = getattr(second, "created_at", None)
        if first_created is not None and second_created is not None:
            try:
                if first_created != second_created:
                    return first_created > second_created
            except TypeError:
                pass
        first_id = getattr(first, "id", None)
        second_id = getattr(second, "id", None)
        if isinstance(first_id, int) and isinstance(second_id, int):
            return first_id > second_id
        return False

    async def aggregate_by_workspace(self, key: str) -> dict[str, Any]:
        from agent.modules.workspaces import ThreadWorkspace

        scope_key = str(key or "").strip()
        session = await get_async_session()
        thread_stmt = (
            select(ThreadWorkspace.thread_id)
            .where(ThreadWorkspace.scope_key == scope_key)
        )
        async with session:
            thread_result = await session.execute(thread_stmt)
            thread_ids = [row[0] for row in thread_result.all() if row[0]]

        if not thread_ids:
            return {
                "key": scope_key,
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
            "key": scope_key,
            "total_tokens": total_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "models": models,
        }

    async def aggregate_workspaces(self, query: UsageQuery) -> list[dict[str, Any]]:
        from agent.modules.workspaces import ThreadWorkspace

        session = await get_async_session()
        stmt = (
            select(
                ThreadWorkspace.scope_key,
                ThreadWorkspace.scope_kind,
                ThreadWorkspace.scope_label,
                ThreadWorkspace.scope_metadata_json,
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
            .where(ThreadWorkspace.scope_key.is_not(None))
            .group_by(
                ThreadWorkspace.scope_key,
                ThreadWorkspace.scope_kind,
                ThreadWorkspace.scope_label,
                ThreadWorkspace.scope_metadata_json,
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
                ThreadWorkspace.scope_key,
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
            .where(ThreadWorkspace.scope_key.is_not(None))
            .group_by(
                ThreadWorkspace.scope_key,
                LLMUsageEvent.model_name,
                LLMUsageEvent.provider_name
            )
        )

        async with session:
            breakdown_result = await session.execute(breakdown_stmt)
            breakdown_rows = breakdown_result.all()

        breakdowns = {}
        for r in breakdown_rows:
            key = r.scope_key
            if key not in breakdowns:
                breakdowns[key] = []
            breakdowns[key].append({
                "model": r.model_name,
                "provider": r.provider_name,
                "total_tokens": int(r.total_tokens or 0)
            })

        workspaces = []
        for row in workspace_rows:
            key = row.scope_key
            total = int(row.total_tokens or 0)

            model_details = breakdowns.get(key, [])
            for md in model_details:
                md["percentage"] = round((md["total_tokens"] / total * 100), 1) if total > 0 else 0.0
            model_details.sort(key=lambda x: x["total_tokens"], reverse=True)

            workspaces.append({
                "key": row.scope_key,
                "kind": row.scope_kind,
                "label": row.scope_label or row.scope_key,
                "metadata": _json_dict(row.scope_metadata_json),
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
        from agent.modules.conversations import ConversationThread

        session = await get_async_session()
        thread_id_expr = _root_thread_expr()
        stmt = (
            select(
                thread_id_expr.label("thread_id"),
                ConversationThread.title,
                ConversationThread.agent_name,
                func.count(distinct(LLMUsageEvent.thread_id)).label("thread_count"),
                func.count(LLMUsageEvent.id).label("event_count"),
                func.coalesce(func.sum(LLMUsageEvent.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0).label("total_tokens"),
                func.max(LLMUsageEvent.created_at).label("last_used_at"),
            )
            .outerjoin(ConversationThread, thread_id_expr == ConversationThread.thread_id)
            .where(*_where_clauses(query))
            .group_by(
                thread_id_expr,
                ConversationThread.title,
                ConversationThread.agent_name,
            )
            .order_by(
                func.max(LLMUsageEvent.created_at).desc(),
                func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0).desc(),
            )
        )

        async with session:
            result = await session.execute(stmt)
            thread_rows = result.all()

        if not thread_rows:
            return []

        breakdown_stmt = (
            select(
                thread_id_expr.label("thread_id"),
                LLMUsageEvent.model_name,
                LLMUsageEvent.provider_name,
                func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0).label("total_tokens"),
            )
            .where(*_where_clauses(query))
            .group_by(
                thread_id_expr,
                LLMUsageEvent.model_name,
                LLMUsageEvent.provider_name,
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
                "thread_count": int(row.thread_count or 0),
                "event_count": int(row.event_count or 0),
                "input_tokens": int(row.input_tokens or 0),
                "output_tokens": int(row.output_tokens or 0),
                "total_tokens": total,
                "last_used_at": _iso(row.last_used_at),
                "models": model_details,
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
    if query.call_kind:
        clauses.append(LLMUsageEvent.call_kind == query.call_kind)
    if query.internal is not None:
        clauses.append(LLMUsageEvent.internal.is_(query.internal))
    return clauses


async def _distinct_strings(session: Any, column: Any, clauses: list[Any]) -> list[str]:
    result = await session.execute(
        select(column)
        .distinct()
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
