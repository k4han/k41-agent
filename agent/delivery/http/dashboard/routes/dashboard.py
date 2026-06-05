from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse

from agent.modules.admin_auth import get_current_admin
from agent.delivery.http.dashboard.routes.shared import (
    _agent_card_options,
    _get_channel_manager,
    _get_config_service,
    _list_all_jobs,
    _paired_identities,
    _provider_entries_from_flat_config,
    _scheduler_timezone_label,
    _serialize_agent_config,
)
from agent.modules.agent_runtime import (
    get_active_session_registry,
    get_background_task_manager,
)
from agent.modules.agents import get_catalog_service
from agent.modules.channels import (
    get_registered_channel_catalog,
    list_channel_statuses,
)
from agent.modules.mcp import list_mcp_server_status
from agent.modules.scheduler import get_scheduler

# Mirrors BACKGROUND_TASK_ACTIVE_STATUSES from agent.modules.agent_runtime.background_tasks
# (kept inline so we don't reach into the agent_runtime internals).
BACKGROUND_TASK_ACTIVE_STATUSES = {"pending", "running"}


router = APIRouter()


HOME_RECENT_THREADS_LIMIT = 8
HOME_RECENT_TASKS_LIMIT = 5
HOME_UPCOMING_JOBS_LIMIT = 5
HOME_PROVIDERS_LIMIT = 8


def _channel_name_from_key(key: str) -> str:
    parts = key.split(".")
    if len(parts) >= 3 and parts[0] == "channels":
        return parts[1]
    return "other"


def _group_channel_settings(
    settings: dict[str, dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for key, info in settings.items():
        grouped.setdefault(_channel_name_from_key(key), {})[key] = info
    return grouped


def _add_channel_schema_settings(
    settings: dict[str, dict[str, Any]],
    settings_sources: dict[str, list[dict[str, Any]]],
) -> None:
    for channel in get_registered_channel_catalog():
        for field in channel.get("settings", []):
            if not isinstance(field, dict):
                continue
            key = str(field.get("key") or "")
            if not key or key in settings:
                continue
            value = field.get("default")
            settings[key] = {
                "value": value,
                "source": "default",
                "input_type": field.get("input_type", "text"),
                "description": field.get("description", ""),
                "category": "channels",
                "label": field.get("label", key),
            }
            settings_sources[key] = [{"value": value, "source": "default"}]


def _format_uptime(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _system_status(
    services: list[dict[str, str | None]],
    *,
    tasks_active: int,
    tasks_failed: int,
    jobs_total: int,
    sessions_active: int,
    providers_configured: int,
    mcp_connected: int,
) -> str:
    error_channels = sum(1 for service in services if service.get("status") == "error")
    if error_channels or tasks_failed > 0:
        return "degraded"
    if (
        providers_configured == 0
        or mcp_connected == 0
        or (jobs_total == 0 and tasks_active == 0 and sessions_active == 0)
    ):
        return "down"
    return "healthy"


def _is_provider_ready(entry: dict[str, Any]) -> bool:
    enabled = bool(entry.get("enabled", True))
    api_key = str(entry.get("api_key") or "").strip()
    base_url = str(entry.get("base_url") or "").strip()
    default_model = str(entry.get("default_model") or "").strip()
    provider_type = str(entry.get("type") or entry.get("provider") or "").strip()
    requires_base_url = provider_type == "openai_compatible"
    if not enabled:
        return False
    if not api_key:
        return False
    if not default_model:
        return False
    if requires_base_url and not base_url:
        return False
    return True


def _build_providers_health(
    flat_config: dict[str, Any],
) -> list[dict[str, Any]]:
    providers = _provider_entries_from_flat_config(flat_config)
    rows: list[dict[str, Any]] = []
    for entry in providers.values():
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        provider_type = str(
            entry.get("type") or entry.get("provider") or ""
        ).strip()
        enabled = bool(entry.get("enabled", True))
        api_key = str(entry.get("api_key") or "").strip()
        default_model = str(entry.get("default_model") or "").strip()
        raw_models = entry.get("models")
        if isinstance(raw_models, list):
            model_count = len(raw_models)
        elif isinstance(raw_models, str) and raw_models.strip():
            model_count = len(
                [item for item in raw_models.replace("\n", ",").split(",") if item.strip()]
            )
        else:
            model_count = 0
        rows.append({
            "name": name,
            "type": provider_type,
            "enabled": enabled,
            "ready": enabled and _is_provider_ready(entry),
            "has_api_key": bool(api_key),
            "default_model": default_model,
            "model_count": model_count,
        })
    rows.sort(key=lambda row: (not row["ready"], not row["enabled"], row["name"]))
    return rows[:HOME_PROVIDERS_LIMIT]


def _build_onboarding(
    *,
    services: list[dict[str, str | None]],
    providers: list[dict[str, Any]],
    agents_total: int,
) -> dict[str, bool]:
    running_channels = sum(1 for s in services if s.get("status") == "running")
    ready_providers = sum(1 for p in providers if p.get("ready"))
    needs_provider = ready_providers == 0
    needs_channel = running_channels == 0
    needs_agent = agents_total == 0
    return {
        "show_checklist": needs_provider or needs_channel or needs_agent,
        "needs_provider": needs_provider,
        "needs_channel": needs_channel,
        "needs_agent": needs_agent,
    }


def _build_upcoming_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    upcoming = [job for job in jobs if not job.get("paused") and job.get("next_run_time")]
    upcoming.sort(key=lambda job: str(job.get("next_run_time") or ""))
    return upcoming[:HOME_UPCOMING_JOBS_LIMIT]


@router.get("/dashboard-api/session")
async def get_dashboard_session(current_admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    return {"authenticated": True, "admin_id": current_admin}


@router.get("/dashboard-api/home")
async def get_dashboard_home(request: Request) -> dict[str, Any]:
    channel_manager = _get_channel_manager(request)
    services = list_channel_statuses(channel_manager)

    task_manager = get_background_task_manager()
    all_tasks = task_manager.list_all()
    tasks_active = sum(
        1 for task in all_tasks if task.get("status") in BACKGROUND_TASK_ACTIVE_STATUSES
    )
    tasks_failed = sum(1 for task in all_tasks if task.get("status") == "failed")
    recent_tasks = all_tasks[:HOME_RECENT_TASKS_LIMIT]

    registry = get_active_session_registry()
    sessions_active = registry.count()
    active_sessions = registry.list_active()

    try:
        jobs = _list_all_jobs()
        scheduler = get_scheduler()
        scheduler_timezone = _scheduler_timezone_label(scheduler)
    except RuntimeError:
        jobs = []
        scheduler_timezone = "local time"
    upcoming_jobs = _build_upcoming_jobs(jobs)

    catalog = get_catalog_service()
    agents = catalog.list_agents()
    agents_total = len(agents)

    config_service = _get_config_service(request)
    flat_config = config_service.get_all()
    providers_health = _build_providers_health(flat_config)
    ready_providers = sum(1 for p in providers_health if p.get("ready"))

    try:
        mcp_statuses = await list_mcp_server_status()
    except Exception:
        mcp_statuses = []
    mcp_total = len(mcp_statuses)
    mcp_connected = sum(
        1
        for status in mcp_statuses
        if status.enabled and status.loaded and not status.error
    )

    try:
        from agent.modules.conversations import (
            THREAD_KIND_USER,
            list_conversation_threads,
        )
        recent_threads = await list_conversation_threads(
            limit=HOME_RECENT_THREADS_LIMIT,
            offset=0,
            kind=THREAD_KIND_USER,
        )
    except Exception:
        recent_threads = []

    started_at = float(getattr(request.app.state, "started_at", 0.0) or 0.0)
    uptime_seconds = max(0.0, time.time() - started_at) if started_at else 0.0
    system_status = _system_status(
        services,
        tasks_active=tasks_active,
        tasks_failed=tasks_failed,
        jobs_total=len(jobs),
        sessions_active=sessions_active,
        providers_configured=ready_providers,
        mcp_connected=mcp_connected,
    )

    onboarding = _build_onboarding(
        services=services,
        providers=providers_health,
        agents_total=agents_total,
    )

    return {
        "services": services,
        "system": {
            "status": system_status,
            "uptime_seconds": round(uptime_seconds, 1),
            "uptime_display": _format_uptime(uptime_seconds),
            "started_at": (
                datetime.fromtimestamp(started_at).isoformat() if started_at else None
            ),
            "version": getattr(request.app, "version", "") or "",
        },
        "counters": {
            "channels": {
                "total": len(services),
                "running": sum(1 for s in services if s.get("status") == "running"),
                "error": sum(1 for s in services if s.get("status") == "error"),
            },
            "agents": agents_total,
            "tasks": {
                "total": len(all_tasks),
                "active": tasks_active,
                "failed": tasks_failed,
            },
            "scheduler": {
                "total": len(jobs),
                "upcoming": len(upcoming_jobs),
            },
            "sessions_active": sessions_active,
            "providers": {
                "total": len(providers_health),
                "ready": ready_providers,
            },
            "mcp_servers": {
                "total": mcp_total,
                "connected": mcp_connected,
            },
        },
        "recent": {
            "tasks": recent_tasks,
            "threads": recent_threads,
            "upcoming_jobs": upcoming_jobs,
        },
        "active_sessions": active_sessions,
        "providers_health": providers_health,
        "scheduler_timezone": scheduler_timezone,
        "onboarding": onboarding,
    }


@router.get("/dashboard-api/channels")
async def get_dashboard_channels(request: Request) -> dict[str, Any]:
    service = _get_config_service(request)
    settings_raw, settings_sources_raw = service.get_settings_overview_and_sources()
    settings = {
        key: value
        for key, value in settings_raw.items()
        if key.startswith("channels.")
    }
    settings_sources = {
        key: value
        for key, value in settings_sources_raw.items()
        if key.startswith("channels.")
    }
    _add_channel_schema_settings(settings, settings_sources)
    by_channel = _group_channel_settings(settings)
    channel_manager = getattr(request.app.state, "channel_manager", None)
    runtime_map: dict[str, dict[str, Any]] = {}
    if channel_manager is not None:
        runtime_map = {
            info["name"]: info
            for info in list_channel_statuses(channel_manager)
        }
    catalog_names = {
        str(item.get("name") or "")
        for item in get_registered_channel_catalog()
        if item.get("name")
    }
    channel_names = sorted(set(by_channel) | set(runtime_map) | catalog_names)
    runtimes = {
        name: {
            "name": name,
            "status": (runtime_map.get(name) or {}).get("status", "unregistered"),
            "error": (runtime_map.get(name) or {}).get("error"),
            "registered": name in runtime_map,
        }
        for name in channel_names
    }
    return {
        "identities": await _paired_identities(),
        "settings": settings,
        "by_channel": by_channel,
        "settings_sources": settings_sources,
        "runtimes": runtimes,
    }


@router.get("/dashboard-api/agents")
async def get_dashboard_agents() -> dict[str, Any]:
    return await _agent_card_options()


@router.get("/dashboard-api/tasks")
async def get_dashboard_tasks() -> dict[str, Any]:
    manager = get_background_task_manager()
    catalog = get_catalog_service()
    return {
        "tasks": manager.list_all(),
        "agents": [_serialize_agent_config(agent) for agent in catalog.list_agents()],
        "identities": await _paired_identities(),
    }


@router.get("/dashboard-api/scheduler")
async def get_dashboard_scheduler() -> dict[str, Any]:
    try:
        scheduler = get_scheduler()
        jobs = _list_all_jobs()
        scheduler_timezone = _scheduler_timezone_label(scheduler)
    except RuntimeError:
        jobs = []
        scheduler_timezone = "local time"

    return {
        "jobs": jobs,
        "identities": await _paired_identities(),
        "scheduler_timezone": scheduler_timezone,
    }


@router.get("/dashboard-api/sessions")
async def get_dashboard_sessions() -> dict[str, Any]:
    registry = get_active_session_registry()
    sessions = registry.list_active()
    return {"sessions": sessions, "count": len(sessions)}


@router.post("/dashboard-api/sessions/stop")
async def stop_dashboard_session(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = payload.get("session_id")
    thread_id = payload.get("thread_id")
    registry = get_active_session_registry()
    
    success = False
    if session_id:
        success = registry.cancel_session(session_id)
    elif thread_id:
        success = registry.cancel_by_thread(thread_id)
        
    if not success:
        raise HTTPException(status_code=400, detail="No active session found to cancel.")
        
    return {"success": success}


@router.get("/dashboard-api/sessions/events")
async def stream_session_events() -> StreamingResponse:
    from fastapi.responses import StreamingResponse
    from agent.delivery.http.dashboard.routes.shared import _sse_event, SSE_HEARTBEAT_SECONDS
    import asyncio
    
    registry = get_active_session_registry()
    queue = registry.subscribe()
    
    async def event_generator():
        try:
            # Yield initial snapshot first
            initial_sessions = registry.list_active()
            yield _sse_event("snapshot", {"sessions": initial_sessions})
            
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=SSE_HEARTBEAT_SECONDS,
                    )
                    yield _sse_event(event["type"], event["data"])
                except asyncio.TimeoutError:
                    yield _sse_event("heartbeat", {})
        finally:
            registry.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
