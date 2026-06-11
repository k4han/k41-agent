from __future__ import annotations

from datetime import datetime
from typing import Any

from apscheduler.job import Job
from fastapi import HTTPException

from agent.modules.scheduler import get_scheduler
from agent.shared.timezone import resolve_display_timezone


def get_scheduler_or_503() -> Any:
    try:
        return get_scheduler()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def serialize_job(job: Job) -> dict[str, Any]:
    trigger_type = type(job.trigger).__name__.lower().replace("trigger", "")
    trigger_args: dict[str, Any] = {}
    trigger = job.trigger
    _, display_zone = resolve_display_timezone()
    for attr in ["run_date", "weeks", "days", "hours", "minutes", "seconds",
                 "minute", "hour", "day", "month", "day_of_week"]:
        if hasattr(trigger, attr):
            val = getattr(trigger, attr)
            if val is not None:
                trigger_args[attr] = str(val) if not isinstance(val, (int, float, str)) else val
    if trigger_type == "date" and hasattr(trigger, "run_date"):
        run_date = getattr(trigger, "run_date")
        if isinstance(run_date, datetime):
            trigger_args["run_date"] = run_date.astimezone(display_zone).strftime("%Y-%m-%dT%H:%M")
    return {
        "id": job.id,
        "task": job.kwargs.get("task", "Unknown"),
        "platform": job.kwargs.get("platform", "-"),
        "user_id": job.kwargs.get("user_id", "-"),
        "trigger_type": trigger_type,
        "trigger_args": trigger_args,
        "next_run_time": (
            job.next_run_time.astimezone(display_zone).strftime("%Y-%m-%d %H:%M:%S %Z")
            if job.next_run_time
            else None
        ),
        "paused": job.next_run_time is None,
    }


def list_all_jobs() -> list[dict[str, Any]]:
    scheduler = get_scheduler()
    return [serialize_job(j) for j in scheduler.get_jobs()]


def scheduler_timezone_label(scheduler: Any) -> str:
    timezone = getattr(scheduler, "timezone", None)
    if timezone is None:
        return "local time"
    return getattr(timezone, "key", None) or str(timezone)


def get_job_or_404(job_id: str) -> Job:
    scheduler = get_scheduler_or_503()
    job = scheduler.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job
