from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from agent.modules.usage.repository import (
    LLMUsageRepository,
    UsageEventInput,
    UsageQuery,
)
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.timezone import (
    ensure_aware_utc,
    localize_iso_datetime,
    resolve_display_timezone,
)

logger = logging.getLogger(__name__)

USAGE_RETENTION_DAYS = 90
DEFAULT_USAGE_LIMIT = 50
MAX_USAGE_LIMIT = 200
USAGE_CONTEXT_METADATA_KEY = "usage_context"
USAGE_DASHBOARD_VIEWS = {"all", "users", "workspaces", "threads"}


@dataclass(frozen=True, slots=True)
class UsageContext:
    thread_id: str
    root_thread_id: str
    platform: str
    user_id: str
    channel_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "thread_id": self.thread_id,
            "root_thread_id": self.root_thread_id,
            "platform": self.platform,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
        }


def root_thread_id(thread_id: str) -> str:
    return str(thread_id or "").split(":sub:", 1)[0].strip()


def build_usage_context(
    thread_id: str,
    explicit: dict[str, Any] | None = None,
) -> UsageContext:
    explicit = explicit or {}
    normalized_thread_id = str(explicit.get("thread_id") or thread_id or "").strip()
    normalized_root = str(
        explicit.get("root_thread_id") or root_thread_id(normalized_thread_id)
    ).strip()

    platform = str(explicit.get("platform") or "").strip()
    user_id = str(explicit.get("user_id") or "").strip()
    channel_id = str(explicit.get("channel_id") or "").strip()

    if not platform or not user_id or not channel_id:
        parsed = _parse_thread_id(normalized_root)
        if parsed is None:
            platform = platform or "unknown"
            user_id = user_id or "unknown"
        else:
            parsed_platform, parsed_user_id, parsed_channel_id = parsed
            platform = platform or parsed_platform
            user_id = user_id or parsed_user_id
            channel_id = channel_id or parsed_channel_id

    return UsageContext(
        thread_id=normalized_thread_id,
        root_thread_id=normalized_root,
        platform=platform or "unknown",
        user_id=user_id or "unknown",
        channel_id=channel_id,
    )


def attach_usage_context(config: dict[str, Any], context: UsageContext) -> dict[str, Any]:
    next_config = dict(config or {})
    metadata = dict(next_config.get("metadata") or {})
    metadata[USAGE_CONTEXT_METADATA_KEY] = context.to_dict()
    next_config["metadata"] = metadata
    return next_config


def usage_context_from_config(config: dict[str, Any] | None) -> UsageContext:
    config = config or {}
    metadata = config.get("metadata") if isinstance(config, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    explicit = metadata.get(USAGE_CONTEXT_METADATA_KEY)
    if not isinstance(explicit, dict):
        explicit = {}
    configurable = config.get("configurable") if isinstance(config, dict) else {}
    thread_id = ""
    if isinstance(configurable, dict):
        thread_id = str(configurable.get("thread_id") or "")
    return build_usage_context(thread_id, explicit)


def normalize_usage_query(
    *,
    start: datetime,
    end: datetime,
    platform: str = "",
    user_id: str = "",
    channel_id: str = "",
    agent_name: str = "",
    provider_name: str = "",
    model_name: str = "",
    call_kind: str = "",
    internal: bool | None = None,
    limit: int = DEFAULT_USAGE_LIMIT,
    offset: int = 0,
) -> UsageQuery:
    return UsageQuery(
        start=ensure_aware_utc(start),
        end=ensure_aware_utc(end),
        platform=platform.strip(),
        user_id=user_id.strip(),
        channel_id=channel_id.strip(),
        agent_name=agent_name.strip(),
        provider_name=provider_name.strip(),
        model_name=model_name.strip(),
        call_kind=call_kind.strip(),
        internal=internal,
        limit=max(1, min(int(limit or DEFAULT_USAGE_LIMIT), MAX_USAGE_LIMIT)),
        offset=max(0, int(offset or 0)),
    )


def _parse_thread_id(thread_id: str) -> tuple[str, str, str] | None:
    parts = str(thread_id or "").split("_", 2)
    if len(parts) < 2:
        return None
    return parts[0], parts[1], parts[2] if len(parts) == 3 else ""


def _localize_last_used(rows: list[dict[str, Any]], tz: Any) -> None:
    for row in rows:
        if "last_used_at" in row:
            row["last_used_at"] = localize_iso_datetime(row.get("last_used_at"), tz)


class UsageService:
    def __init__(self, repository: LLMUsageRepository | None = None) -> None:
        self._repository = repository or LLMUsageRepository()

    async def record_event(self, event: UsageEventInput) -> None:
        try:
            await self._repository.record(event)
        except Exception as exc:
            logger.debug("Failed to record LLM usage event: %s", exc)

    async def dashboard_payload(self, query: UsageQuery, view: str = "all") -> dict[str, Any]:
        await self.prune_old_events()
        normalized_view = view if view in USAGE_DASHBOARD_VIEWS else "all"
        display_timezone, display_zone = resolve_display_timezone()
        summary = await self._repository.summary(query)
        if normalized_view in {"all", "users"}:
            rows, total = await self._repository.grouped_by_identity(query)
        else:
            rows, total = [], 0
        workspaces = (
            await self._repository.aggregate_workspaces(query)
            if normalized_view in {"all", "workspaces"}
            else []
        )
        threads = (
            await self._repository.aggregate_threads(query)
            if normalized_view in {"all", "threads"}
            else []
        )
        _localize_last_used(rows, display_zone)
        _localize_last_used(workspaces, display_zone)
        _localize_last_used(threads, display_zone)
        filters = await self._repository.filter_options(query)
        return {
            "summary": summary,
            "rows": rows,
            "workspaces": workspaces,
            "threads": threads,
            "view": normalized_view,
            "display_timezone": display_timezone,
            "filters": filters,
            "pagination": {
                "limit": query.limit,
                "offset": query.offset,
                "total": total,
                "has_more": query.offset + query.limit < total,
                "next_offset": query.offset + query.limit if query.offset + query.limit < total else None,
            },
            "range": {
                "start": query.start.isoformat(),
                "end": query.end.isoformat(),
            },
        }

    async def prune_old_events(self) -> int:
        cutoff = utcnow() - timedelta(days=USAGE_RETENTION_DAYS)
        try:
            return await self._repository.prune_before(cutoff)
        except Exception as exc:
            logger.debug("Failed to prune old LLM usage events: %s", exc)
            return 0

    async def get_thread_usage(self, thread_id: str) -> dict[str, Any]:
        return await self._repository.aggregate_by_thread(thread_id)

    async def get_workspace_usage(self, backend: str, locator: str) -> dict[str, Any]:
        return await self._repository.aggregate_by_workspace(backend, locator)


_service: UsageService | None = None


def get_usage_service() -> UsageService:
    global _service
    if _service is None:
        _service = UsageService()
    return _service


async def prune_usage_events() -> int:
    return await get_usage_service().prune_old_events()


__all__ = [
    "DEFAULT_USAGE_LIMIT",
    "UsageContext",
    "UsageService",
    "attach_usage_context",
    "build_usage_context",
    "get_usage_service",
    "normalize_usage_query",
    "prune_usage_events",
    "root_thread_id",
    "usage_context_from_config",
]
