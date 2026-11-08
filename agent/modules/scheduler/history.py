"""Persistence for scheduled job run history."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update

from agent.modules.scheduler.models import SchedulerJobRun
from agent.shared.infrastructure.db import utcnow
from agent.shared.infrastructure.db.session import get_async_session
from agent.shared.timezone import ensure_aware_utc, resolve_display_timezone

RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_INTERRUPTED = "interrupted"

TRIGGER_SOURCE_SCHEDULE = "schedule"
TRIGGER_SOURCE_MANUAL = "manual"

MAX_RUNS_KEPT = 1000
MAX_STORED_TEXT_LEN = 20_000
DEFAULT_RUN_LIST_LIMIT = 50
MAX_RUN_LIST_LIMIT = 200

logger = logging.getLogger(__name__)


def _trim(value: object, max_length: int) -> str:
    text = str(value or "")
    return text if len(text) <= max_length else text[:max_length]


def _localize(value: datetime | None, display_zone: Any) -> str | None:
    if value is None:
        return None
    return ensure_aware_utc(value).astimezone(display_zone).isoformat()


def serialize_run(record: SchedulerJobRun, display_zone: Any | None = None) -> dict[str, Any]:
    if display_zone is None:
        _, display_zone = resolve_display_timezone()
    duration_ms = None
    if record.finished_at is not None and record.started_at is not None:
        elapsed = ensure_aware_utc(record.finished_at) - ensure_aware_utc(record.started_at)
        duration_ms = max(0, int(elapsed.total_seconds() * 1000))
    return {
        "run_id": record.run_id,
        "job_id": record.job_id,
        "task": record.task,
        "platform": record.platform,
        "user_id": record.user_id,
        "trigger_source": record.trigger_source,
        "status": record.status,
        "thread_id": record.thread_id,
        "agent_name": record.agent_name,
        "provider": record.provider,
        "model": record.model,
        "response": record.response,
        "error": record.error,
        "started_at": _localize(record.started_at, display_zone),
        "finished_at": _localize(record.finished_at, display_zone),
        "duration_ms": duration_ms,
    }


async def start_run(
    *,
    run_id: str,
    job_id: str,
    task: str,
    platform: str,
    user_id: str,
    trigger_source: str,
    thread_id: str,
    agent_name: str,
    provider: str,
    model: str,
) -> None:
    record = SchedulerJobRun(
        run_id=_trim(run_id, 64),
        job_id=_trim(job_id, 191),
        task=_trim(task, MAX_STORED_TEXT_LEN),
        platform=_trim(platform, 50),
        user_id=_trim(user_id, 255),
        trigger_source=_trim(trigger_source, 32),
        status=RUN_STATUS_RUNNING,
        thread_id=_trim(thread_id, 512),
        agent_name=_trim(agent_name, 255),
        provider=_trim(provider, 255),
        model=_trim(model, 255),
        started_at=utcnow(),
    )
    session = await get_async_session()
    async with session:
        session.add(record)
        await session.commit()


async def finish_run(
    run_id: str,
    *,
    status: str,
    response: str = "",
    error: str = "",
) -> None:
    session = await get_async_session()
    async with session:
        await session.execute(
            update(SchedulerJobRun)
            .where(SchedulerJobRun.run_id == run_id)
            .values(
                status=status,
                response=_trim(response, MAX_STORED_TEXT_LEN),
                error=_trim(error, MAX_STORED_TEXT_LEN),
                finished_at=utcnow(),
            )
        )
        await session.commit()
    await prune_runs()


async def prune_runs(max_kept: int = MAX_RUNS_KEPT) -> int:
    """Delete the oldest finished runs beyond the retention cap."""
    session = await get_async_session()
    async with session:
        cutoff = (
            await session.execute(
                select(SchedulerJobRun.id)
                .order_by(SchedulerJobRun.id.desc())
                .offset(max_kept)
                .limit(1)
            )
        ).scalar_one_or_none()
        if cutoff is None:
            return 0
        result = await session.execute(
            delete(SchedulerJobRun).where(
                SchedulerJobRun.id <= cutoff,
                SchedulerJobRun.status != RUN_STATUS_RUNNING,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


async def mark_interrupted_runs() -> int:
    """Close runs left in ``running`` state by a previous process."""
    session = await get_async_session()
    async with session:
        result = await session.execute(
            update(SchedulerJobRun)
            .where(SchedulerJobRun.status == RUN_STATUS_RUNNING)
            .values(
                status=RUN_STATUS_INTERRUPTED,
                error="The application stopped before this run finished.",
                finished_at=utcnow(),
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


async def _attach_used_models(runs: list[dict[str, Any]]) -> None:
    """Attach the models actually recorded by usage tracking for each run."""
    try:
        from agent.modules.usage import get_usage_service

        by_thread = await get_usage_service().get_models_by_threads(
            [run["thread_id"] for run in runs]
        )
    except Exception as exc:
        logger.debug("Could not load usage for scheduler runs: %s", exc)
        by_thread = {}
    for run in runs:
        models = by_thread.get(run["thread_id"], [])
        run["models_used"] = models
        run["total_tokens"] = sum(item["total_tokens"] for item in models)


async def list_runs(
    *,
    job_id: str | None = None,
    limit: int = DEFAULT_RUN_LIST_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_RUN_LIST_LIMIT))
    offset = max(0, int(offset))
    filters = [SchedulerJobRun.job_id == job_id] if job_id else []
    session = await get_async_session()
    async with session:
        total = (
            await session.execute(select(func.count(SchedulerJobRun.id)).where(*filters))
        ).scalar_one()
        records = (
            await session.execute(
                select(SchedulerJobRun)
                .where(*filters)
                .order_by(SchedulerJobRun.started_at.desc(), SchedulerJobRun.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    _, display_zone = resolve_display_timezone()
    runs = [serialize_run(record, display_zone) for record in records]
    await _attach_used_models(runs)
    return {"runs": runs, "total": int(total or 0), "limit": limit, "offset": offset}


async def delete_run(run_id: str) -> bool:
    """Delete one finished run, rejecting executions that are still running."""
    session = await get_async_session()
    async with session:
        result = await session.execute(
            delete(SchedulerJobRun).where(
                SchedulerJobRun.run_id == run_id,
                SchedulerJobRun.status != RUN_STATUS_RUNNING,
            )
        )
        if result.rowcount:
            await session.commit()
            return True
        status = (
            await session.execute(
                select(SchedulerJobRun.status).where(SchedulerJobRun.run_id == run_id)
            )
        ).scalar_one_or_none()
        if status == RUN_STATUS_RUNNING:
            raise ValueError("Cannot delete a run while it is running.")
        return False


async def delete_runs(*, job_id: str | None = None) -> int:
    filters = [SchedulerJobRun.status != RUN_STATUS_RUNNING]
    if job_id:
        filters.append(SchedulerJobRun.job_id == job_id)
    session = await get_async_session()
    async with session:
        result = await session.execute(delete(SchedulerJobRun).where(*filters))
        await session.commit()
        return int(result.rowcount or 0)


__all__ = [
    "RUN_STATUS_COMPLETED",
    "RUN_STATUS_FAILED",
    "RUN_STATUS_INTERRUPTED",
    "RUN_STATUS_RUNNING",
    "TRIGGER_SOURCE_MANUAL",
    "TRIGGER_SOURCE_SCHEDULE",
    "delete_run",
    "delete_runs",
    "finish_run",
    "list_runs",
    "mark_interrupted_runs",
    "prune_runs",
    "serialize_run",
    "start_run",
]
