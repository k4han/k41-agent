import logging
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from agent.modules.agent_runtime import SessionManager
from agent.modules.scheduler import (
    TriggerType,
    execute_scheduled_task,
    get_scheduler,
    normalize_trigger,
)
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode

logger = logging.getLogger(__name__)


def _parse_runtime_thread_id(runtime: ToolRuntime[Any, Any]) -> tuple[str, str]:
    """Extract (platform, user_id) from the runtime's thread_id.

    Raises ``ToolError`` when the thread_id is missing or malformed.
    """
    configurable = runtime.config.get("configurable", {})
    thread_id = str(configurable.get("thread_id", "") or "").strip()

    if not thread_id:
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            "Could not determine user session from thread_id.",
        )

    try:
        platform, user_id, _ = SessionManager.parse_thread_id(thread_id)
    except ValueError as exc:
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            f"Unsupported thread_id format '{thread_id}'.",
        ) from exc

    return platform, user_id


class ScheduleTaskInput(BaseModel):
    task_description: str = Field(
        ...,
        description="Detailed description of the task for the AI to perform at the scheduled time.",
    )
    trigger_type: TriggerType = Field(
        ...,
        description=(
            "The type of schedule trigger. Use 'relative' for one-time delays like "
            "'in 2 minutes', 'date' for a specific one-time clock time, 'interval' "
            "for recurring periods, or 'cron' for cron-like recurring schedules."
        ),
    )
    trigger_args: dict = Field(
        ...,
        description="""Arguments for the chosen trigger type.
- For 'date': {"run_date": "YYYY-MM-DD HH:MM:SS"} (Configured display timezone).
- For 'relative': {"minutes": 2}, {"hours": 1}, {"days": 1} for one-time delays from now.
- For 'interval': e.g., {"minutes": 10}, {"hours": 1}, {"days": 1} for recurring schedules.
- For 'cron': e.g., {"hour": "2", "minute": "0"} for 2:00 AM daily, {"day_of_week": "mon-fri", "hour": "9"} (Configured display timezone).""",
    )


@register_tool(
    category=ToolCategory.SCHEDULE,
    capabilities=[ToolCapability.REQUIRES_THREAD],
    tags=["scheduler"],
)
@tool(args_schema=ScheduleTaskInput)
def schedule_task(
    task_description: str,
    trigger_type: TriggerType,
    trigger_args: dict[str, Any],
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Schedule an AI task to run in the future or periodically."""
    platform, user_id = _parse_runtime_thread_id(runtime)

    try:
        scheduler = get_scheduler()
        normalized_trigger_type, normalized_trigger_args = normalize_trigger(
            trigger_type,
            trigger_args,
            scheduler,
        )

        job = scheduler.add_job(
            execute_scheduled_task,
            trigger=normalized_trigger_type,
            kwargs={"platform": platform, "user_id": user_id, "task": task_description},
            **normalized_trigger_args,
        )
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc

    next_run = (
        job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z")
        if job.next_run_time
        else "Unknown"
    )
    return (
        f"Successfully scheduled task '{task_description}'. "
        f"Job ID: {job.id}. Next run time: {next_run}"
    )


@register_tool(
    category=ToolCategory.SCHEDULE,
    capabilities=[ToolCapability.REQUIRES_THREAD],
    tags=["scheduler"],
)
@tool
def list_scheduled_tasks(
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Lists all scheduled tasks for the current user."""
    platform, user_id = _parse_runtime_thread_id(runtime)

    scheduler = get_scheduler()
    all_jobs = scheduler.get_jobs()
    user_jobs = [
        j for j in all_jobs
        if j.kwargs.get("platform") == platform and j.kwargs.get("user_id") == user_id
    ]

    if not user_jobs:
        return "You have no scheduled tasks."

    lines = []
    for job in user_jobs:
        next_run = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S %Z') if job.next_run_time else 'Unknown'
        task_desc = job.kwargs.get("task", "Unknown Task")
        lines.append(f"- ID: {job.id} | Task: {task_desc} | Next run: {next_run}")

    return "Your scheduled tasks:\n" + "\n".join(lines)


class DeleteTaskInput(BaseModel):
    job_id: str = Field(..., description="The ID of the scheduled task to delete.")


@register_tool(
    category=ToolCategory.SCHEDULE,
    capabilities=[ToolCapability.REQUIRES_THREAD],
    tags=["scheduler"],
)
@tool(args_schema=DeleteTaskInput)
def delete_scheduled_task(
    job_id: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    """Deletes a scheduled task by its ID."""
    platform, user_id = _parse_runtime_thread_id(runtime)

    scheduler = get_scheduler()
    job = scheduler.get_job(job_id)

    if not job:
        raise ToolError(
            ToolErrorCode.NOT_FOUND,
            f"No scheduled task found with ID: {job_id}",
        )

    if job.kwargs.get("platform") != platform or job.kwargs.get("user_id") != user_id:
        raise ToolError(
            ToolErrorCode.PERMISSION_DENIED,
            "You do not have permission to delete this task.",
        )

    scheduler.remove_job(job_id)
    return f"Successfully deleted task {job_id}."
