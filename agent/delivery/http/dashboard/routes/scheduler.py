from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from agent.modules.scheduler import TriggerType
from agent.delivery.http.dashboard.routes.helpers.scheduler import (
    get_job_or_404,
    get_scheduler_or_503,
    list_all_jobs,
    serialize_job,
)
from agent.modules.scheduler import (
    DEFAULT_RUN_LIST_LIMIT,
    MAX_RUN_LIST_LIMIT,
    TRIGGER_SOURCE_MANUAL,
    add_scheduled_task_job,
    delete_run,
    delete_runs,
    execute_scheduled_task,
    list_runs,
    normalize_trigger,
    resolve_scheduled_project,
)
from agent.modules.projects import ProjectWorkspaceError
from agent.delivery.http.dashboard.routes.helpers.workspace import workspace_http_error


router = APIRouter()


@router.get("/scheduler/jobs")
async def list_scheduler_jobs() -> dict[str, list[dict[str, Any]]]:
    """List all scheduled jobs with their triggers and status."""
    try:
        jobs = list_all_jobs()
    except RuntimeError:
        jobs = []
    return {"jobs": jobs}


@router.get("/scheduler/runs")
async def list_scheduler_runs(
    job_id: str | None = Query(default=None, description="Only return runs of this job."),
    limit: int = Query(default=DEFAULT_RUN_LIST_LIMIT, ge=1, le=MAX_RUN_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List past executions of scheduled jobs, newest first."""
    return await list_runs(job_id=job_id, limit=limit, offset=offset)


@router.delete("/scheduler/runs")
async def clear_scheduler_runs(
    job_id: str | None = Query(default=None, description="Only clear runs of this job."),
) -> dict[str, Any]:
    """Delete finished run history entries."""
    deleted = await delete_runs(job_id=job_id)
    return {"status": "deleted", "deleted": deleted}


@router.delete("/scheduler/runs/{run_id}")
async def delete_scheduler_run(run_id: str) -> dict[str, str]:
    """Delete a single finished execution from run history."""
    try:
        deleted = await delete_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return {"status": "deleted", "run_id": run_id}


class CreateJobBody(BaseModel):
    """Request body for creating a new scheduled job."""

    task: str = Field(..., description="Task instruction or prompt for the agent.")
    platform: str = Field(..., description="Platform identifier (e.g. 'api', 'telegram').")
    user_id: str = Field(..., description="User ID to execute the task as.")
    trigger_type: TriggerType = Field(..., description="APScheduler trigger type (e.g. 'cron', 'interval', 'date').")
    trigger_args: dict[str, Any] = Field(..., description="Trigger arguments (e.g. for cron: hour, minute, day_of_week).")
    project_id: int | None = Field(default=None, ge=1, description="Explicit project selection; otherwise each run uses No workspace.")


class UpdateJobBody(BaseModel):
    """Request body for updating an existing scheduled job. Only provided fields are updated."""

    task: str | None = Field(default=None, description="New task instruction.")
    platform: str | None = Field(default=None, description="New platform identifier.")
    user_id: str | None = Field(default=None, description="New user ID.")
    trigger_type: TriggerType | None = Field(default=None, description="New trigger type.")
    trigger_args: dict[str, Any] | None = Field(default=None, description="New trigger arguments.")
    project_id: int | None = Field(default=None, ge=1, description="Project selection; null resets to No workspace.")


@router.post("/scheduler/jobs")
async def create_scheduler_job(body: CreateJobBody) -> dict[str, Any]:
    """Create a new scheduled job with the given trigger configuration."""
    scheduler = get_scheduler_or_503()

    try:
        await resolve_scheduled_project(body.project_id)
        trigger_type, trigger_args = normalize_trigger(
            body.trigger_type,
            body.trigger_args,
            scheduler,
        )
        job = add_scheduled_task_job(
            scheduler,
            platform=body.platform,
            user_id=body.user_id,
            task=body.task,
            trigger_type=trigger_type,
            trigger_args=trigger_args,
            project_id=body.project_id,
        )
    except (KeyError, ProjectWorkspaceError) as exc:
        raise workspace_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to create job: {exc}") from exc

    return {"status": "created", "job": serialize_job(job)}


@router.put("/scheduler/jobs/{job_id}")
async def update_scheduler_job(job_id: str, body: UpdateJobBody) -> dict[str, Any]:
    """Update an existing scheduled job's task, platform, user, or trigger."""
    job = get_job_or_404(job_id)

    project_changed = "project_id" in body.model_fields_set
    if body.task is None and body.platform is None and body.user_id is None and body.trigger_type is None and not project_changed:
        raise HTTPException(status_code=400, detail="No fields to update.")

    try:
        if project_changed:
            await resolve_scheduled_project(body.project_id)
        if body.task is not None or body.platform is not None or body.user_id is not None or project_changed:
            new_kwargs = dict(job.kwargs)
            if body.task is not None:
                new_kwargs["task"] = body.task
            if body.platform is not None:
                new_kwargs["platform"] = body.platform
            if body.user_id is not None:
                new_kwargs["user_id"] = body.user_id
            if project_changed:
                new_kwargs["project_id"] = body.project_id
            job.modify(kwargs=new_kwargs)

        if body.trigger_type is not None and body.trigger_args is not None:
            scheduler = get_scheduler_or_503()
            trigger_type, trigger_args = normalize_trigger(
                body.trigger_type,
                body.trigger_args,
                scheduler,
            )
            job.reschedule(trigger_type, **trigger_args)
    except HTTPException:
        raise
    except (KeyError, ProjectWorkspaceError) as exc:
        raise workspace_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to update job: {exc}") from exc

    return {"status": "updated", "job": serialize_job(job)}


@router.delete("/scheduler/jobs/{job_id}")
async def delete_scheduler_job(job_id: str) -> dict[str, str]:
    """Delete a scheduled job permanently."""
    job = get_job_or_404(job_id)
    job.remove()
    return {"status": "deleted", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/pause")
async def pause_scheduler_job(job_id: str) -> dict[str, str]:
    """Pause a scheduled job so it stops firing until resumed."""
    job = get_job_or_404(job_id)
    job.pause()
    return {"status": "paused", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/resume")
async def resume_scheduler_job(job_id: str) -> dict[str, str]:
    """Resume a previously paused scheduled job."""
    job = get_job_or_404(job_id)
    job.resume()
    return {"status": "resumed", "job_id": job_id}


@router.post("/scheduler/jobs/{job_id}/run")
async def run_scheduler_job_now(
    job_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Trigger immediate execution of a scheduled job."""
    job = get_job_or_404(job_id)
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
        scheduler_job_id=job.id,
        trigger_source=TRIGGER_SOURCE_MANUAL,
        project_id=job.kwargs.get("project_id"),
    )
    return {"status": "queued", "job_id": job_id}
