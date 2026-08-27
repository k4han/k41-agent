from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse

from agent.modules.admin_auth import get_current_admin
from agent.delivery.http.dashboard.routes.helpers.agents import serialize_agent_config
from agent.delivery.http.dashboard.routes.helpers.deps import (
    get_channel_manager,
    get_request_config_service,
)
from agent.delivery.http.dashboard.routes.helpers.identities import paired_identities
from agent.delivery.http.dashboard.routes.helpers.providers import (
    provider_entries_from_flat_config,
)
from agent.delivery.http.dashboard.routes.helpers.scheduler import (
    list_all_jobs,
    scheduler_timezone_label,
)
from agent.delivery.http.dashboard.routes.helpers.sse import (
    SSE_HEARTBEAT_SECONDS,
    sse_event,
)
from agent.modules.agent_runtime import (
    get_active_session_registry,
    get_background_task_manager,
    get_chat_stream_manager,
)
from agent.modules.agents import get_catalog_service
from agent.modules.conversations import get_conversation_thread_repository
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
logger = logging.getLogger(__name__)


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
    providers = provider_entries_from_flat_config(flat_config)
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
    """Check if the current session is authenticated."""
    return {"authenticated": True, "admin_id": current_admin}


@router.get("/dashboard-api/home")
async def get_dashboard_home(request: Request) -> dict[str, Any]:
    """Get dashboard home data including system status, counters, recent items, and onboarding state."""
    started = time.perf_counter()
    last_timing = started
    timings: list[str] = []

    def record_timing(name: str) -> None:
        nonlocal last_timing
        now = time.perf_counter()
        timings.append(f"{name}={(now - last_timing) * 1000:.1f}ms")
        last_timing = now

    channel_manager = get_channel_manager(request)
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
    record_timing("runtime")

    try:
        jobs = list_all_jobs()
        scheduler = get_scheduler()
        scheduler_timezone = scheduler_timezone_label(scheduler)
    except RuntimeError:
        jobs = []
        scheduler_timezone = "local time"
    upcoming_jobs = _build_upcoming_jobs(jobs)
    record_timing("scheduler")

    catalog = get_catalog_service()
    agents = catalog.list_agents()
    agents_total = len(agents)
    record_timing("agents")

    config_service = get_request_config_service(request)
    flat_config = config_service.get_all()
    providers_health = _build_providers_health(flat_config)
    ready_providers = sum(1 for p in providers_health if p.get("ready"))
    record_timing("providers")

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
    record_timing("mcp")

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
    record_timing("recent_threads")

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

    payload = {
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
    logger.debug(
        "Dashboard home loaded in %.1fms (%s)",
        (time.perf_counter() - started) * 1000,
        ", ".join(timings),
    )
    return payload


@router.get("/dashboard-api/channels")
async def get_dashboard_channels(request: Request) -> dict[str, Any]:
    """Get channel settings, runtime statuses, and paired identities."""
    service = get_request_config_service(request)
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
    # Enrich agent picker fields with dropdown options from the agent catalog so
    # the frontend can render them as <select> controls even without a custom
    # override. This keeps the API self-describing and supports generic
    # SettingControl rendering for update_mode and agent fields.
    try:
        from agent.modules.agents import get_catalog_service

        agent_names = sorted(
            card.name for card in get_catalog_service().list_agent_cards() if card.valid
        )
        for key, info in list(settings.items()):
            suffix = key.rsplit(".", 1)[-1] if "." in key else ""
            if suffix in ("default_agent", "code_agent", "research_agent"):
                # Provide sorted agent names plus an empty choice for "use default"
                info["options"] = ["", *agent_names]
                info["input_type"] = "select"
            elif suffix == "update_mode":
                info["options"] = ["polling", "webhook"]
                info["input_type"] = "select"
        # Re-group after enrichment so by_channel reflects injected options/input_type
        by_channel = _group_channel_settings(settings)
    except Exception:
        pass
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
        "identities": await paired_identities(),
        "settings": settings,
        "by_channel": by_channel,
        "settings_sources": settings_sources,
        "runtimes": runtimes,
    }


@router.get("/dashboard-api/tasks")
async def get_dashboard_tasks() -> dict[str, Any]:
    """Get background tasks with agent and identity context."""
    manager = get_background_task_manager()
    catalog = get_catalog_service()
    tasks = manager.list_all()

    thread_ids = [t["thread_id"] for t in tasks if t.get("thread_id")]
    repo = get_conversation_thread_repository()
    active_threads = await repo.list_active_thread_ids(thread_ids)

    for task in tasks:
        tid = task.get("thread_id")
        task["thread_deleted"] = bool(tid) and tid not in active_threads

    return {
        "tasks": tasks,
        "agents": [serialize_agent_config(agent) for agent in catalog.list_agents()],
        "identities": await paired_identities(),
    }


@router.get("/dashboard-api/scheduler")
async def get_dashboard_scheduler() -> dict[str, Any]:
    """Get scheduled jobs with timezone information."""
    try:
        scheduler = get_scheduler()
        jobs = list_all_jobs()
        scheduler_timezone = scheduler_timezone_label(scheduler)
    except RuntimeError:
        jobs = []
        scheduler_timezone = "local time"

    return {
        "jobs": jobs,
        "identities": await paired_identities(),
        "scheduler_timezone": scheduler_timezone,
    }


@router.get("/dashboard-api/sessions")
async def get_dashboard_sessions() -> dict[str, Any]:
    """List currently active agent sessions."""
    registry = get_active_session_registry()
    sessions = registry.list_active()
    return {"sessions": sessions, "count": len(sessions)}


@router.post("/dashboard-api/sessions/stop")
async def stop_dashboard_session(payload: dict[str, Any]) -> dict[str, Any]:
    """Stop an active session by session ID or thread ID."""
    session_id = payload.get("session_id")
    thread_id = payload.get("thread_id")
    registry = get_active_session_registry()

    success = False
    # Resolve thread_id for chat stream cancellation when only session_id is provided.
    resolved_thread_id: str | None = None
    if session_id:
        info = registry.get(session_id)
        if info and isinstance(info.get("thread_id"), str):
            resolved_thread_id = str(info["thread_id"])
        success = registry.cancel_session(session_id)
    elif thread_id:
        success = registry.cancel_by_thread(thread_id)
        resolved_thread_id = str(thread_id)

    # Also cancel any active chat stream session for the same thread.
    # ChatStreamManager tasks survive client disconnects (F5) and share the
    # same underlying asyncio task as ActiveSessionRegistry, but there is a
    # race window where the active session is not yet registered. Cancelling
    # the chat stream session directly guarantees the LLM stream actually stops.
    target_thread_id = resolved_thread_id or (str(thread_id) if thread_id else None)
    if target_thread_id:
        try:
            manager = get_chat_stream_manager()
            session = await manager.get_session(target_thread_id)
            if session is not None:
                session.cancel()
                success = True
        except Exception:
            logger.debug("Failed to cancel chat stream session for thread %s", target_thread_id, exc_info=True)

    if not success:
        raise HTTPException(status_code=400, detail="No active session found to cancel.")

    return {"success": success}


@router.get("/dashboard-api/sessions/events")
async def stream_session_events() -> StreamingResponse:
    """Stream real-time session events (start, stop, status changes) via SSE."""
    registry = get_active_session_registry()
    queue = registry.subscribe()

    async def event_generator():
        try:
            # Yield initial snapshot first
            initial_sessions = registry.list_active()
            yield sse_event("snapshot", {"sessions": initial_sessions})

            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=SSE_HEARTBEAT_SECONDS,
                    )
                    yield sse_event(event["type"], event["data"])
                except asyncio.TimeoutError:
                    yield sse_event("heartbeat", {})
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
