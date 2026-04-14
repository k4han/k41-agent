from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from apscheduler.job import Job
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from agent.modules.channels.public import (
    ChannelManager,
    get_channel_status,
    list_channel_statuses,
    start_all_channels,
    start_channel,
    stop_all_channels,
    stop_channel,
)
from agent.modules.admin_auth.public import get_current_admin
from agent.modules.scheduler.public import (
    TriggerType,
    execute_scheduled_task,
    get_scheduler,
    normalize_trigger,
)
from agent.modules.users.public import get_pairing_service
from agent.shared.config import ConfigService, is_runtime_key

router = APIRouter(tags=["dashboard"], dependencies=[Depends(get_current_admin)])
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# --- helpers ----------------------------------------------------------


def _get_channel_manager(request: Request) -> ChannelManager:
    channel_manager = getattr(request.app.state, "channel_manager", None)
    if channel_manager is None:
        raise HTTPException(status_code=503, detail="Channel manager is not available.")
    return channel_manager


def _get_config_service(request: Request) -> ConfigService:
    service = getattr(request.app.state, "config_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Config service is not available.")
    return service


def _get_scheduler() -> Any:
    try:
        return get_scheduler()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _group_settings_by_category(
    settings: dict[str, dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for key, info in settings.items():
        category = info.get("category", "general")
        grouped.setdefault(category, {})[key] = info
    return grouped


def _ensure_runtime_keys(keys: list[str]) -> None:
    if invalid_keys := sorted(k for k in keys if not is_runtime_key(k)):
        suffix = "" if len(invalid_keys) == 1 else "s"
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported runtime setting{suffix}: {', '.join(invalid_keys)}.",
        )


# --- views -------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def dashboard_index(request: Request) -> HTMLResponse:
    channel_manager = _get_channel_manager(request)
    services = list_channel_statuses(channel_manager)
    return templates.TemplateResponse(
        request=request, name="index.html", context={"services": services}
    )


@router.get("/channels", response_class=HTMLResponse)
async def dashboard_channels(request: Request) -> HTMLResponse:
    pairing_service = get_pairing_service()
    identities = await pairing_service.list_paired_identities()
    return templates.TemplateResponse(
        request=request,
        name="channels.html",
        context={"request": request, "identities": identities},
    )


@router.post("/channels/pair")
async def generate_pairing_code(request: Request) -> dict[str, str]:
    pairing_service = get_pairing_service()
    code, user_id = await pairing_service.create_pairing_root_user_and_code()
    return {"code": code, "user_id": str(user_id)}


@router.delete("/channels/identities/{identity_id}")
async def unpair_identity(identity_id: int) -> dict[str, str]:
    pairing_service = get_pairing_service()
    await pairing_service.unpair_identity(identity_id)
    return {"status": "success"}


@router.get("/config", response_class=HTMLResponse)
async def dashboard_config(request: Request) -> HTMLResponse:
    service = _get_config_service(request)
    settings = service.get_settings_overview()
    settings_sources = service.get_settings_sources()
    by_category = _group_settings_by_category(settings)

    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context={
            "settings": settings,
            "by_category": by_category,
            "settings_sources": settings_sources,
        },
    )


@router.get("/services")
async def get_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    channel_manager = _get_channel_manager(request)
    return {"services": list_channel_statuses(channel_manager)}


@router.get("/services/{name}")
async def get_service(name: str, request: Request) -> dict[str, str | None]:
    channel_manager = _get_channel_manager(request)
    try:
        return get_channel_status(channel_manager, name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/services/{name}/start")
async def start_service(name: str, request: Request) -> dict[str, str | None]:
    channel_manager = _get_channel_manager(request)
    try:
        status = await start_channel(channel_manager, name)
        return {"message": f"'{name}' is starting.", **status}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/services/{name}/stop")
async def stop_service(name: str, request: Request) -> dict[str, str | None]:
    channel_manager = _get_channel_manager(request)
    try:
        status = await stop_channel(channel_manager, name)
        return {"message": f"'{name}' stopped.", **status}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/services/start-all")
async def start_all_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    channel_manager = _get_channel_manager(request)
    services = await start_all_channels(channel_manager)
    return {"services": services}


@router.post("/services/stop-all")
async def stop_all_services(request: Request) -> dict[str, list[dict[str, str | None]]]:
    channel_manager = _get_channel_manager(request)
    services = await stop_all_channels(channel_manager)
    return {"services": services}


# --- settings endpoints -----------------------------------------------


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, dict[str, Any]]:
    """Return all effective settings with their source."""
    service = _get_config_service(request)
    return {"settings": service.get_settings_overview()}


@router.get("/settings/sources")
async def get_settings_sources(request: Request) -> dict[str, dict[str, Any]]:
    """Return all values from all sources, grouped by key."""
    service = _get_config_service(request)
    return {"sources": service.get_settings_sources()}


class UpdateSettingBody(BaseModel):
    value: Any | None


class UpdateSettingsBody(BaseModel):
    values: dict[str, Any | None]


@router.put("/settings/{key:path}")
async def update_setting(
    key: str,
    body: UpdateSettingBody,
    request: Request,
) -> dict[str, Any | None]:
    """Update a runtime setting and persist it to yaml."""
    service = _get_config_service(request)
    _ensure_runtime_keys([key])

    service.update_setting(key, body.value)
    return {"status": "success", "key": key, "value": body.value}


@router.put("/settings")
async def update_settings(body: UpdateSettingsBody, request: Request) -> dict[str, Any]:
    """Update multiple runtime settings and persist them to yaml."""
    if not body.values:
        return {"status": "success", "updated": []}

    _ensure_runtime_keys(list(body.values))

    service = _get_config_service(request)
    for key, value in body.values.items():
        service.update_setting(key, value)

    return {"status": "success", "updated": list(body.values.keys())}


# --- scheduler endpoints -----------------------------------------------


def _serialize_job(job: Job) -> dict[str, Any]:
    trigger_type = type(job.trigger).__name__.lower().replace("trigger", "")
    return {
        "id": job.id,
        "task": job.kwargs.get("task", "Unknown"),
        "platform": job.kwargs.get("platform", "—"),
        "user_id": job.kwargs.get("user_id", "—"),
        "trigger_type": trigger_type,
        "next_run_time": (
            job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z")
            if job.next_run_time
            else None
        ),
        "paused": job.next_run_time is None,
    }


def _list_all_jobs() -> list[dict[str, Any]]:
    scheduler = get_scheduler()
    return [_serialize_job(j) for j in scheduler.get_jobs()]


def _scheduler_timezone_label(scheduler: Any) -> str:
    timezone = getattr(scheduler, "timezone", None)
    if timezone is None:
        return "local time"
    return getattr(timezone, "key", None) or str(timezone)


def _get_job_or_404(job_id: str) -> Job:
    scheduler = _get_scheduler()
    job = scheduler.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


@router.get("/scheduler", response_class=HTMLResponse)
async def dashboard_scheduler(request: Request) -> HTMLResponse:
    try:
        scheduler = get_scheduler()
        jobs = _list_all_jobs()
        scheduler_timezone = _scheduler_timezone_label(scheduler)
    except RuntimeError:
        jobs = []
        scheduler_timezone = "local time"

    pairing_service = get_pairing_service()
    identities = await pairing_service.list_paired_identities()

    return templates.TemplateResponse(
        request=request,
        name="scheduler.html",
        context={
            "jobs": jobs,
            "identities": identities,
            "scheduler_timezone": scheduler_timezone,
        },
    )


@router.get("/scheduler/jobs")
async def list_scheduler_jobs() -> dict[str, list[dict[str, Any]]]:
    try:
        jobs = _list_all_jobs()
    except RuntimeError:
        jobs = []
    return {"jobs": jobs}


class CreateJobBody(BaseModel):
    task: str
    platform: str
    user_id: str
    trigger_type: TriggerType
    trigger_args: dict[str, Any]


class UpdateJobBody(BaseModel):
    task: str | None = None
    trigger_type: TriggerType | None = None
    trigger_args: dict[str, Any] | None = None


@router.post("/scheduler/jobs")
async def create_scheduler_job(body: CreateJobBody) -> dict[str, Any]:
    scheduler = _get_scheduler()

    try:
        trigger_type, trigger_args = normalize_trigger(
            body.trigger_type,
            body.trigger_args,
            scheduler,
        )
        job = scheduler.add_job(
            execute_scheduled_task,
            trigger=trigger_type,
            kwargs={"platform": body.platform, "user_id": body.user_id, "task": body.task},
            **trigger_args,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to create job: {exc}") from exc

    return {"status": "created", "job": _serialize_job(job)}


@router.put("/scheduler/jobs/{job_id}")
async def update_scheduler_job(job_id: str, body: UpdateJobBody) -> dict[str, Any]:
    scheduler = _get_scheduler()
    job = _get_job_or_404(job_id)

    if body.task is None and (body.trigger_type is None or body.trigger_args is None):
        raise HTTPException(status_code=400, detail="No fields to update.")

    try:
        if body.task is not None:
            new_kwargs = dict(job.kwargs)
            new_kwargs["task"] = body.task
            job.modify(kwargs=new_kwargs)

        if body.trigger_type is not None and body.trigger_args is not None:
            trigger_type, trigger_args = normalize_trigger(
                body.trigger_type,
                body.trigger_args,
                scheduler,
            )
            job.reschedule(trigger_type, **trigger_args)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to update job: {exc}") from exc

    return {"status": "updated", "job": _serialize_job(job)}


@router.delete("/scheduler/jobs/{job_id}")
async def delete_scheduler_job(job_id: str) -> dict[str, str]:
    job = _get_job_or_404(job_id)
    job.remove()
    return {"status": "deleted", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/pause")
async def pause_scheduler_job(job_id: str) -> dict[str, str]:
    job = _get_job_or_404(job_id)
    job.pause()
    return {"status": "paused", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/resume")
async def resume_scheduler_job(job_id: str) -> dict[str, str]:
    job = _get_job_or_404(job_id)
    job.resume()
    return {"status": "resumed", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/run")
async def run_scheduler_job_now(
    job_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    job = _get_job_or_404(job_id)
    platform = job.kwargs.get("platform")
    user_id = job.kwargs.get("user_id")
    task = job.kwargs.get("task")

    if not platform or not user_id or not task:
        raise HTTPException(
            status_code=400,
            detail=f"Job '{job_id}' is missing platform, user_id, or task.",
        )

    background_tasks.add_task(
        execute_scheduled_task,
        platform=platform,
        user_id=user_id,
        task=task,
    )
    return {"status": "queued", "job_id": job_id}
