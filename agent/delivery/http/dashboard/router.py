from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from apscheduler.job import Job
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from agent.modules.channels import (
    ChannelManager,
    get_channel_status,
    list_channel_statuses,
    start_all_channels,
    start_channel,
    stop_all_channels,
    stop_channel,
)
from agent.modules.admin_auth import get_current_admin
from agent.modules.scheduler import (
    TriggerType,
    execute_scheduled_task,
    get_scheduler,
    normalize_trigger,
)
from agent.modules.agent_runtime import get_active_session_registry, get_background_task_manager, NotifyChannel
from agent.modules.agents import get_catalog_service
from agent.modules.users import get_pairing_service
from agent.shared.config import (
    ConfigService,
    PROVIDER_SETTING_FIELD_ORDER,
    get_setting_metadata,
    is_runtime_key,
    parse_provider_key,
)

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


def _is_provider_setting_key(key: str) -> bool:
    if key.startswith("llm.providers."):
        return True
    return key.startswith("llm.")


def _filter_settings[T](
    settings: dict[str, T],
    *,
    include_provider_settings: bool,
) -> dict[str, T]:
    return {
        key: value
        for key, value in settings.items()
        if _is_provider_setting_key(key) == include_provider_settings
    }


def _split_provider_settings(
    settings: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    global_settings: dict[str, dict[str, Any]] = {}
    provider_settings: dict[str, dict[str, Any]] = {}
    for key, info in settings.items():
        if key.startswith("llm.providers."):
            provider_settings[key] = info
            continue
        global_settings[key] = info
    return global_settings, provider_settings


def _build_provider_rows(
    settings: dict[str, dict[str, Any]],
    expected_fields: list[str],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for key, info in settings.items():
        parsed = parse_provider_key(key)
        if parsed is None:
            continue

        provider_name, field_name = parsed
        grouped.setdefault(provider_name, {})[field_name] = {
            "key": key,
            "info": info,
        }

    provider_rows: list[dict[str, Any]] = []
    for provider_name in sorted(grouped):
        fields = grouped[provider_name]

        if "provider" not in fields and "type" in fields:
            fields["provider"] = fields["type"]

        for field_name in expected_fields:
            if field_name in fields:
                continue

            synthetic_key = f"llm.providers.{provider_name}.{field_name}"
            metadata = get_setting_metadata(synthetic_key)
            synthetic_info: dict[str, Any] = {
                "value": None,
                "source": "default",
                "input_type": metadata["type"],
                "description": metadata["description"],
                "category": metadata["category"],
                "label": metadata["label"],
            }
            for optional_key in ("min", "max", "step"):
                if optional_key in metadata:
                    synthetic_info[optional_key] = metadata[optional_key]

            fields[field_name] = {
                "key": synthetic_key,
                "info": synthetic_info,
            }

        provider_rows.append({
            "name": provider_name,
            "fields": fields,
        })

    return provider_rows


def _remove_hidden_provider_global_keys(
    settings: dict[str, Any],
) -> dict[str, Any]:
    hidden_keys = {
        "llm.base_url",
        "llm.temperature",
    }
    return {
        key: value
        for key, value in settings.items()
        if key not in hidden_keys
    }


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
    settings_raw, settings_sources_raw = service.get_settings_overview_and_sources()
    settings = _filter_settings(settings_raw, include_provider_settings=False)
    settings_sources = _filter_settings(settings_sources_raw, include_provider_settings=False)
    by_category = _group_settings_by_category(settings)

    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context={
            "active_nav": "config",
            "page_title": "Runtime Configuration",
            "page_subtitle": "Manage channels, database, and security runtime settings.",
            "settings": settings,
            "by_category": by_category,
            "settings_sources": settings_sources,
        },
    )


@router.get("/providers", response_class=HTMLResponse)
async def dashboard_providers(request: Request) -> HTMLResponse:
    service = _get_config_service(request)
    settings_raw, settings_sources_raw = service.get_settings_overview_and_sources()
    settings = _remove_hidden_provider_global_keys(_filter_settings(
        settings_raw,
        include_provider_settings=True,
    ))
    settings_sources = _remove_hidden_provider_global_keys(_filter_settings(
        settings_sources_raw,
        include_provider_settings=True,
    ))
    global_settings, provider_settings = _split_provider_settings(settings)
    by_category = _group_settings_by_category(global_settings)
    provider_rows = _build_provider_rows(provider_settings, PROVIDER_SETTING_FIELD_ORDER)
    provider_name_options = [row["name"] for row in provider_rows]

    return templates.TemplateResponse(
        request=request,
        name="config.html",
        context={
            "active_nav": "providers",
            "page_title": "Provider Configuration",
            "page_subtitle": "Manage default provider and per-provider LLM credentials/models.",
            "settings": settings,
            "by_category": by_category,
            "settings_sources": settings_sources,
            "provider_rows": provider_rows,
            "provider_name_options": provider_name_options,
            "provider_field_order": PROVIDER_SETTING_FIELD_ORDER,
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
    for source in service._sources:
        if hasattr(source, "update_settings"):
            source.update_settings(body.values)
    service.reload()

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


# --- active sessions endpoints ------------------------------------------


@router.get("/sessions", response_class=HTMLResponse)
async def dashboard_sessions(request: Request) -> HTMLResponse:
    registry = get_active_session_registry()
    sessions = registry.list_active()
    return templates.TemplateResponse(
        request=request,
        name="sessions.html",
        context={"sessions": sessions, "count": len(sessions)},
    )


@router.get("/sessions/active")
async def list_active_sessions() -> dict[str, Any]:
    registry = get_active_session_registry()
    sessions = registry.list_active()
    return {"sessions": sessions, "count": len(sessions)}


# --- background tasks endpoints -----------------------------------------


class SubmitTaskBody(BaseModel):
    request: str
    agent_name: str = "default"
    notify_platform: str | None = None
    notify_external_id: str | None = None
    notify_channel_id: str | None = None


@router.get("/tasks", response_class=HTMLResponse)
async def dashboard_tasks(request: Request) -> HTMLResponse:
    manager = get_background_task_manager()
    tasks = manager.list_all()

    catalog = get_catalog_service()
    agents = catalog.list_agents()

    pairing_service = get_pairing_service()
    identities = await pairing_service.list_paired_identities()

    return templates.TemplateResponse(
        request=request,
        name="tasks.html",
        context={"tasks": tasks, "agents": agents, "identities": identities},
    )


@router.get("/tasks/list")
async def list_background_tasks() -> dict[str, Any]:
    manager = get_background_task_manager()
    tasks = manager.list_all()
    return {"tasks": tasks}


@router.post("/tasks")
async def submit_background_task(body: SubmitTaskBody) -> dict[str, Any]:
    if not body.request.strip():
        raise HTTPException(status_code=400, detail="Request cannot be empty.")

    notify_channel = None
    if body.notify_platform and body.notify_external_id:
        notify_channel = NotifyChannel(
            platform=body.notify_platform,
            external_id=body.notify_external_id,
            channel_id=body.notify_channel_id or body.notify_external_id,
        )

    manager = get_background_task_manager()
    task_id = await manager.submit(
        request=body.request.strip(),
        agent_name=body.agent_name,
        notify_channel=notify_channel,
    )
    return {"status": "submitted", "task_id": task_id}


@router.post("/tasks/{task_id}/cancel")
async def cancel_background_task(task_id: str) -> dict[str, str]:
    manager = get_background_task_manager()
    result = manager.cancel(task_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    if result == "not_running":
        raise HTTPException(status_code=400, detail="Task is not running.")
    return {"status": "cancelled", "task_id": task_id}


@router.delete("/tasks/{task_id}")
async def remove_background_task(task_id: str) -> dict[str, str]:
    manager = get_background_task_manager()
    removed = manager.remove(task_id)
    if not removed:
        raise HTTPException(
            status_code=400,
            detail=f"Task '{task_id}' not found or still running.",
        )
    return {"status": "removed", "task_id": task_id}
