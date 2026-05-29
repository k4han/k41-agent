from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, HTTPException

from agent.modules.admin_auth import get_current_admin
from agent.delivery.http.dashboard.routes.shared import (
    _agent_card_options,
    _get_channel_manager,
    _list_all_jobs,
    _paired_identities,
    _scheduler_timezone_label,
    _serialize_agent_config,
)
from agent.modules.agent_runtime import (
    get_active_session_registry,
    get_background_task_manager,
)
from agent.modules.agents import get_catalog_service
from agent.modules.channels import list_channel_statuses
from agent.modules.scheduler import get_scheduler


router = APIRouter()


@router.get("/dashboard-api/session")
async def get_dashboard_session(current_admin: str = Depends(get_current_admin)) -> dict[str, Any]:
    return {"authenticated": True, "admin_id": current_admin}


@router.get("/dashboard-api/overview")
async def get_dashboard_overview(request: Request) -> dict[str, Any]:
    channel_manager = _get_channel_manager(request)
    return {"services": list_channel_statuses(channel_manager)}


@router.get("/dashboard-api/channels")
async def get_dashboard_channels() -> dict[str, Any]:
    return {"identities": await _paired_identities()}


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
