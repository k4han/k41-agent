from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from agent.delivery.http.dashboard.routes.helpers.identities import paired_identities
from agent.modules.usage import DEFAULT_USAGE_LIMIT, get_usage_service, normalize_usage_query
from agent.shared.timezone import resolve_display_timezone


router = APIRouter()


def _parse_datetime(
    value: str | None,
    default: datetime,
    display_zone: ZoneInfo,
    *,
    end_of_day: bool = False,
) -> datetime:
    if not value:
        return default
    if len(value) == 10:
        hour, minute, second, microsecond = (23, 59, 59, 999000) if end_of_day else (0, 0, 0, 0)
        parsed = datetime.fromisoformat(value).replace(
            hour=hour,
            minute=minute,
            second=second,
            microsecond=microsecond,
            tzinfo=display_zone,
        )
        return parsed.astimezone(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _identity_map(identities: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(identity.get("platform") or ""), str(identity.get("external_id") or "")): identity
        for identity in identities
    }


def _identity_label(
    *,
    platform: str,
    user_id: str,
    channel_id: str = "",
    identities: dict[tuple[str, str], dict[str, Any]],
) -> str:
    identity = identities.get((platform, user_id))
    if identity and identity.get("user_id") is not None:
        label = f"User #{identity['user_id']} · {platform}:{user_id}"
    elif platform == "unknown" and user_id == "unknown":
        label = "Unknown"
    else:
        label = f"{platform}:{user_id}" if user_id else platform or "Unknown"

    if channel_id and channel_id != user_id:
        label = f"{label} · channel {channel_id}"
    return label


def _enrich_usage_payload(payload: dict[str, Any], identities: list[dict[str, Any]]) -> dict[str, Any]:
    by_identity = _identity_map(identities)
    rows = []
    for row in payload["rows"]:
        rows.append(
            {
                **row,
                "identity_label": _identity_label(
                    platform=row["platform"],
                    user_id=row["user_id"],
                    channel_id=row.get("channel_id") or "",
                    identities=by_identity,
                ),
            }
        )
    filters = dict(payload["filters"])
    filters["users"] = [
        {
            **item,
            "label": _identity_label(
                platform=item["platform"],
                user_id=item["user_id"],
                identities=by_identity,
            ),
        }
        for item in filters.get("users", [])
    ]
    filters["channels"] = [
        {
            **item,
            "label": _identity_label(
                platform=item["platform"],
                user_id=item["user_id"],
                channel_id=item["channel_id"],
                identities=by_identity,
            ),
        }
        for item in filters.get("channels", [])
    ]
    return {**payload, "rows": rows, "filters": filters}


@router.get("/dashboard-api/usage")
async def get_dashboard_usage(
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    platform: str = "",
    user_id: str = "",
    channel_id: str = "",
    agent: str = "",
    provider: str = "",
    model: str = "",
    call_kind: str = "",
    internal: bool | None = Query(default=None),
    view: str = "all",
    limit: int = DEFAULT_USAGE_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    _, display_zone = resolve_display_timezone()
    now = datetime.now(timezone.utc)
    default_start = now - timedelta(days=7)
    query = normalize_usage_query(
        start=_parse_datetime(start, default_start, display_zone),
        end=_parse_datetime(end, now, display_zone, end_of_day=True),
        platform=platform,
        user_id=user_id,
        channel_id=channel_id,
        agent_name=agent,
        provider_name=provider,
        model_name=model,
        call_kind=call_kind,
        internal=internal,
        limit=limit,
        offset=offset,
    )
    payload = await get_usage_service().dashboard_payload(query, view=view)
    return _enrich_usage_payload(payload, await paired_identities())


@router.get("/dashboard-api/usage/thread/{thread_id:path}")
async def get_thread_usage(thread_id: str) -> dict[str, Any]:
    return await get_usage_service().get_thread_usage(thread_id)


@router.get("/dashboard-api/usage/workspace")
async def get_workspace_usage(
    backend: str = Query(default="local"),
    locator: str = Query(default=""),
) -> dict[str, Any]:
    return await get_usage_service().get_workspace_usage(backend, locator)


__all__ = ["router"]
