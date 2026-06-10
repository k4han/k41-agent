from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from agent.modules.scheduler import TriggerType
from agent.delivery.http.dashboard.routes.shared import (
    _get_job_or_404,
    _get_scheduler,
    _list_all_jobs,
    _serialize_job,
)
from agent.modules.scheduler import (
    execute_scheduled_task,
    normalize_trigger,
)


router = APIRouter()


@router.get("/scheduler/jobs")
async def list_scheduler_jobs() -> dict[str, list[dict[str, Any]]]:
    """List all scheduled jobs with their triggers and status."""
    try:
        jobs = _list_all_jobs()
    except RuntimeError:
        jobs = []
    return {"jobs": jobs}


class CreateJobBody(BaseModel):
    """Request body for creating a new scheduled job."""

    task: str = Field(..., description="Task instruction or prompt for the agent.")
    platform: str = Field(..., description="Platform identifier (e.g. 'api', 'telegram').")
    user_id: str = Field(..., description="User ID to execute the task as.")
    trigger_type: TriggerType = Field(..., description="APScheduler trigger type (e.g. 'cron', 'interval', 'date').")
    trigger_args: dict[str, Any] = Field(..., description="Trigger arguments (e.g. for cron: hour, minute, day_of_week).")


class UpdateJobBody(BaseModel):
    """Request body for updating an existing scheduled job. Only provided fields are updated."""

    task: str | None = Field(default=None, description="New task instruction.")
    platform: str | None = Field(default=None, description="New platform identifier.")
    user_id: str | None = Field(default=None, description="New user ID.")
    trigger_type: TriggerType | None = Field(default=None, description="New trigger type.")
    trigger_args: dict[str, Any] | None = Field(default=None, description="New trigger arguments.")


@router.post("/scheduler/jobs")
async def create_scheduler_job(body: CreateJobBody) -> dict[str, Any]:
    """Create a new scheduled job with the given trigger configuration."""
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
    """Update an existing scheduled job's task, platform, user, or trigger."""
    job = _get_job_or_404(job_id)

    if body.task is None and body.platform is None and body.user_id is None and body.trigger_type is None:
        raise HTTPException(status_code=400, detail="No fields to update.")

    try:
        if body.task is not None or body.platform is not None or body.user_id is not None:
            new_kwargs = dict(job.kwargs)
            if body.task is not None:
                new_kwargs["task"] = body.task
            if body.platform is not None:
                new_kwargs["platform"] = body.platform
            if body.user_id is not None:
                new_kwargs["user_id"] = body.user_id
            job.modify(kwargs=new_kwargs)

        if body.trigger_type is not None and body.trigger_args is not None:
            scheduler = _get_scheduler()
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
    """Delete a scheduled job permanently."""
    job = _get_job_or_404(job_id)
    job.remove()
    return {"status": "deleted", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/pause")
async def pause_scheduler_job(job_id: str) -> dict[str, str]:
    """Pause a scheduled job so it stops firing until resumed."""
    job = _get_job_or_404(job_id)
    job.pause()
    return {"status": "paused", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/resume")
async def resume_scheduler_job(job_id: str) -> dict[str, str]:
    """Resume a previously paused scheduled job."""
    job = _get_job_or_404(job_id)
    job.resume()
    return {"status": "resumed", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/run")
async def run_scheduler_job_now(
    job_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Trigger immediate execution of a scheduled job."""
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
