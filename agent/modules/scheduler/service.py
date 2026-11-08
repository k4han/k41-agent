import logging
import pickle
import uuid
from html import escape as escape_html
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from agent.modules.agent_runtime import SessionManager, run_agent_full
from agent.modules.notifications import send_notification as _send_notification
from agent.modules.workspaces import WorkspaceBinding
from agent.shared.infrastructure.db.engine import (
    _normalize_url_to_sync,
)
from agent.shared.timezone import resolve_display_timezone

AGENT_NAME = "scheduler-executor"
BACKGROUND_THREAD_PREFIX = "bg"
TASK_TITLE_MAX_LEN = 80
SCHEDULER_SHUTDOWN_TIMEOUT = 10
APSCHEDULER_JOBS_TABLE = "apscheduler_jobs"
LEGACY_EXECUTE_TASK_REF = (
    "agent.modules.scheduler.infrastructure.apscheduler_service:execute_scheduled_task"
)
CURRENT_EXECUTE_TASK_REF = "agent.modules.scheduler.service:execute_scheduled_task"

logger = logging.getLogger(__name__)


def _task_title(task: str) -> str:
    """Return the first line of ``task``, shortened for notification headers."""
    first_line = next((line.strip() for line in task.splitlines() if line.strip()), "")
    if len(first_line) <= TASK_TITLE_MAX_LEN:
        return first_line
    return first_line[: TASK_TITLE_MAX_LEN - 1].rstrip() + "…"


def describe_executor_model() -> dict[str, str]:
    """Return the agent and provider/model that scheduled runs are configured to use."""
    info = {"agent_name": AGENT_NAME, "provider": "", "model": "", "configured": "", "error": ""}
    try:
        from agent.modules.agents import get_catalog_service

        card = get_catalog_service().get_agent_card(AGENT_NAME)
        raw_model = getattr(card, "model", "") or ""
        if isinstance(raw_model, dict):
            raw_model = raw_model.get("id", "") or ""
        card_provider = str(getattr(card, "provider", "") or "default")
        card_model = str(raw_model)
        info["configured"] = f"{card_provider}/{card_model}" if card_model else card_provider
    except Exception as exc:
        info["error"] = f"Could not load agent '{AGENT_NAME}': {exc}"
        return info
    try:
        from agent.modules.providers import get_chat_model_selection

        provider, model = get_chat_model_selection(provider_name=card_provider, model=card_model)
        info["provider"] = provider
        info["model"] = model
    except Exception as exc:
        info["error"] = str(exc)
    return info


def add_scheduled_task_job(
    scheduler: AsyncIOScheduler,
    *,
    platform: str,
    user_id: str,
    task: str,
    trigger_type: str,
    trigger_args: dict[str, Any],
    project_id: int | None = None,
):
    """Add a job that runs ``task`` and remembers its own ID for run history."""
    job_id = uuid.uuid4().hex
    return scheduler.add_job(
        execute_scheduled_task,
        trigger=trigger_type,
        id=job_id,
        kwargs={
            "platform": platform,
            "user_id": user_id,
            "task": task,
            "scheduler_job_id": job_id,
            "project_id": project_id,
        },
        **trigger_args,
    )


async def resolve_scheduled_project(project_id: int | None) -> WorkspaceBinding | None:
    """Resolve an explicitly selected, active project without starting execution."""
    if project_id is None:
        return None
    from agent.modules.projects import ProjectWorkspaceError, get_project_workspace_service

    projects = get_project_workspace_service()
    project, _, _ = await projects.rows(project_id)
    if project.status != "active":
        raise ValueError("The scheduled project is not active.")
    if project.migration_status != "ready":
        raise ProjectWorkspaceError("Choose the project workspace before scheduling tasks.", "migration_required")
    binding = await projects.find_binding(project_id=project_id)
    if binding is None:
        raise ProjectWorkspaceError("The scheduled project workspace is unavailable.")
    return binding


def _backfill_job_ids(scheduler: AsyncIOScheduler) -> int:
    """Store the job ID in kwargs of jobs created before run history existed."""
    updated = 0
    for job in scheduler.get_jobs():
        if job.kwargs.get("scheduler_job_id") == job.id:
            continue
        try:
            job.modify(kwargs={**job.kwargs, "scheduler_job_id": job.id})
            updated += 1
        except Exception as exc:
            logger.warning("Could not backfill scheduler job id for '%s': %s", job.id, exc)
    return updated


async def _record_run_start(**kwargs: Any) -> bool:
    try:
        from agent.modules.scheduler.history import start_run

        await start_run(**kwargs)
        return True
    except Exception as exc:
        logger.warning("Could not record scheduler run start: %s", exc)
        return False


async def _record_run_finish(run_id: str, **kwargs: Any) -> None:
    try:
        from agent.modules.scheduler.history import finish_run

        await finish_run(run_id, **kwargs)
    except Exception as exc:
        logger.warning("Could not record scheduler run result: %s", exc)


async def execute_scheduled_task(
    platform: str,
    user_id: str,
    task: str,
    scheduler_job_id: str = "",
    trigger_source: str = "schedule",
    project_id: int | None = None,
):
    """Execute a scheduled task in its own conversation and notify its owner."""
    from agent.modules.scheduler.history import RUN_STATUS_COMPLETED, RUN_STATUS_FAILED

    run_id = str(uuid.uuid4())
    background_thread_id = SessionManager.make_thread_id(BACKGROUND_THREAD_PREFIX, user_id, run_id)

    logger.info("Executing scheduled task in %s: %s", background_thread_id, task)

    executor = describe_executor_model()
    recorded = await _record_run_start(
        run_id=run_id,
        job_id=scheduler_job_id,
        task=task,
        platform=platform,
        user_id=user_id,
        trigger_source=trigger_source,
        thread_id=background_thread_id,
        agent_name=AGENT_NAME,
        provider=executor["provider"],
        model=executor["model"],
    )
    agent_finished = False

    try:
        from agent.modules.conversations import (
            THREAD_KIND_SCHEDULED, upsert_conversation_thread,
        )
        from agent.modules.workspaces import create_no_workspace, remember_thread_workspace_ref

        await upsert_conversation_thread(
            thread_id=background_thread_id,
            agent_name=AGENT_NAME,
            kind=THREAD_KIND_SCHEDULED,
            platform=BACKGROUND_THREAD_PREFIX,
            user_id=user_id,
            channel_id=run_id,
            title=task,
        )
        workspace = await resolve_scheduled_project(project_id)
        if workspace is None:
            workspace = await create_no_workspace(background_thread_id)
        await remember_thread_workspace_ref(background_thread_id, workspace)
        response_text = await run_agent_full(
            user_input=task,
            thread_id=background_thread_id,
            agent_name=AGENT_NAME,
            workspace=workspace,
            usage_context={"platform": BACKGROUND_THREAD_PREFIX, "user_id": user_id, "channel_id": run_id},
        )
        agent_finished = True
        if recorded:
            await _record_run_finish(run_id, status=RUN_STATUS_COMPLETED, response=response_text)

        # The user wrote the task; only a short title is needed to tell runs apart.
        notification = f"**Scheduled task:** {_task_title(task)}\n\n{response_text}"
        await _send_notification(platform, user_id, notification, mode="markdown")

    except Exception as e:
        logger.error("Failed to execute scheduled task '%s' in %s: %s", task, background_thread_id, e, exc_info=True)
        if recorded and not agent_finished:
            await _record_run_finish(run_id, status=RUN_STATUS_FAILED, error=str(e) or type(e).__name__)
        error_msg = (
            f"<b>Scheduled task failed:</b>\n{escape_html(task)}\n\n"
            f"Error: {escape_html(str(e))}"
        )
        await _send_notification(platform, user_id, error_msg)


def get_scheduler(container=None) -> AsyncIOScheduler:
    """Return the scheduler with the current configured default timezone."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container(container)
    if active._scheduler is None:
        raise RuntimeError("Scheduler not initialized. Call initialize_scheduler() first.")

    # Refresh defaults without restarting or changing existing job triggers.
    _, scheduler_tz = resolve_display_timezone()
    active._scheduler.timezone = scheduler_tz
    return active._scheduler


def _migrate_legacy_job_references(engine: Any) -> int:
    """Rewrite persisted APScheduler callable refs from pre-refactor imports."""
    migrated = 0
    try:
        with engine.begin() as conn:
            legacy_count = conn.execute(
                text(f"SELECT COUNT(*) as cnt FROM {APSCHEDULER_JOBS_TABLE}")
            ).scalar()

            if legacy_count == 0:
                return 0

            rows = conn.execute(
                text(f"SELECT id, job_state FROM {APSCHEDULER_JOBS_TABLE}")
            ).mappings()

            for row in rows:
                job_id = row["id"]
                try:
                    state = pickle.loads(bytes(row["job_state"]))
                except Exception as exc:
                    logger.warning(
                        "Could not decode APScheduler job '%s' during migration: %s",
                        job_id,
                        exc,
                    )
                    continue

                if not isinstance(state, dict):
                    continue
                if state.get("func") != LEGACY_EXECUTE_TASK_REF:
                    continue

                state["func"] = CURRENT_EXECUTE_TASK_REF
                conn.execute(
                    text(
                        f"UPDATE {APSCHEDULER_JOBS_TABLE} "
                        "SET job_state = :job_state WHERE id = :job_id"
                    ),
                    {
                        "job_state": pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL),
                        "job_id": job_id,
                    },
                )
                migrated += 1
    except SQLAlchemyError as exc:
        logger.warning("Could not migrate APScheduler job references: %s", exc)
        return migrated

    if migrated:
        logger.info("Migrated %d APScheduler job reference(s).", migrated)
    return migrated


async def initialize_scheduler(container=None):
    """Initialize container-scoped scheduler idempotently."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container(container)
    if active._scheduler is not None:
        return active._scheduler

    # Close stale runs before starting, so jobs fired on start are not marked interrupted.
    try:
        from agent.modules.scheduler.history import mark_interrupted_runs

        await mark_interrupted_runs()
    except Exception as exc:
        logger.warning("Could not close interrupted scheduler runs: %s", exc)
    if active._scheduler is not None:
        return active._scheduler

    sync_url = _normalize_url_to_sync(active.database_url)
    timezone_name, scheduler_tz = resolve_display_timezone()

    sync_engine = create_engine(sync_url, echo=False, pool_size=2, max_overflow=0)
    _migrate_legacy_job_references(sync_engine)
    scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(engine=sync_engine)},
        timezone=scheduler_tz,
    )
    scheduler.start()
    _backfill_job_ids(scheduler)
    active._scheduler = scheduler
    active._scheduler_sync_engine = sync_engine
    logger.info(
        "Background scheduler initialized and started. Timezone: %s",
        timezone_name,
    )
    return scheduler


async def stop_scheduler(container=None):
    """Stop container-scoped scheduler idempotently."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container(container)
    scheduler = active._scheduler
    sync_engine = active._scheduler_sync_engine
    if scheduler is None and sync_engine is None:
        return
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=True)
        finally:
            active._scheduler = None
    if sync_engine is not None:
        try:
            sync_engine.dispose()
        finally:
            active._scheduler_sync_engine = None
    logger.info("Background scheduler stopped.")
