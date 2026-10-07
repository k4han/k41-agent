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
from agent.shared.infrastructure.db.engine import (
    _normalize_url_to_sync,
)
from agent.shared.timezone import resolve_display_timezone

AGENT_NAME = "scheduler-executor"
BACKGROUND_THREAD_PREFIX = "bg"
TASK_DESCRIPTION_MAX_LEN = 16
SCHEDULER_SHUTDOWN_TIMEOUT = 10
APSCHEDULER_JOBS_TABLE = "apscheduler_jobs"
LEGACY_EXECUTE_TASK_REF = (
    "agent.modules.scheduler.infrastructure.apscheduler_service:execute_scheduled_task"
)
CURRENT_EXECUTE_TASK_REF = "agent.modules.scheduler.service:execute_scheduled_task"

logger = logging.getLogger(__name__)


async def execute_scheduled_task(platform: str, user_id: str, task: str):
    """Execute a scheduled task: run the agent, inject results into user thread, and notify."""
    job_id = str(uuid.uuid4())
    background_thread_id = SessionManager.make_thread_id(BACKGROUND_THREAD_PREFIX, user_id, job_id)
    user_thread_id = SessionManager.make_thread_id(platform, user_id, user_id)

    logger.info(f"Executing scheduled task for {user_thread_id}: {task}")

    try:
        from agent.modules.conversations import (
            THREAD_KIND_SCHEDULED, inject_agent_message_pair, upsert_conversation_thread,
        )

        await upsert_conversation_thread(
            thread_id=background_thread_id,
            agent_name=AGENT_NAME,
            kind=THREAD_KIND_SCHEDULED,
            platform=BACKGROUND_THREAD_PREFIX,
            user_id=user_id,
            channel_id=job_id,
            title=task,
        )
        response_text = await run_agent_full(
            user_input=task,
            thread_id=background_thread_id,
            agent_name=AGENT_NAME,
            usage_context={"platform": BACKGROUND_THREAD_PREFIX, "user_id": user_id, "channel_id": job_id},
        )

        await upsert_conversation_thread(
            thread_id=user_thread_id,
            agent_name=AGENT_NAME,
            title=task,
            platform=platform,
            user_id=user_id,
            channel_id=user_id,
        )

        await inject_agent_message_pair(
            thread_id=user_thread_id,
            human_content=f"[Scheduled Task]\n{task}",
            ai_content=response_text,
        )

        notification = (
            f"**Scheduled task completed:**\n{task}\n\n"
            f"**Result:**\n{response_text}"
        )
        await _send_notification(platform, user_id, notification, mode="markdown")

    except Exception as e:
        logger.error(f"Failed to execute scheduled task '{task}' for {user_thread_id}: {e}", exc_info=True)
        error_msg = (
            f"<b>Scheduled task failed:</b>\n{escape_html(task)}\n\n"
            f"Error: {escape_html(str(e))}"
        )
        await _send_notification(platform, user_id, error_msg)


def get_scheduler(container=None) -> AsyncIOScheduler:
    """Return container-scoped scheduler."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container(container)
    if active._scheduler is None:
        raise RuntimeError("Scheduler not initialized. Call initialize_scheduler() first.")
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

    sync_url = _normalize_url_to_sync(active.database_url)
    timezone_name, scheduler_tz = resolve_display_timezone()

    sync_engine = create_engine(sync_url, echo=False, pool_size=2, max_overflow=0)
    _migrate_legacy_job_references(sync_engine)
    scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(engine=sync_engine)},
        timezone=scheduler_tz,
    )
    scheduler.start()
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
