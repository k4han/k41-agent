import logging
import pickle
import uuid
from html import escape as escape_html
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from agent.modules.agent_runtime import SessionManager, run_agent_full
from agent.modules.notifications import send_notification as _send_notification
from agent.modules.workflows import get_workflow_graph, make_run_config
from agent.shared.infrastructure.db.engine import (
    get_database_url,
    _normalize_url_to_sync,
)
from agent.shared.timezone import resolve_display_timezone
from langchain_core.messages import AIMessage, HumanMessage

AGENT_NAME = "scheduler-executor"
GRAPH_NAME = "react_agent"
NODE_NAME = "llm"
BACKGROUND_THREAD_PREFIX = "bg"
TASK_DESCRIPTION_MAX_LEN = 16
SCHEDULER_SHUTDOWN_TIMEOUT = 10
APSCHEDULER_JOBS_TABLE = "apscheduler_jobs"
LEGACY_EXECUTE_TASK_REF = (
    "agent.modules.scheduler.infrastructure.apscheduler_service:execute_scheduled_task"
)
CURRENT_EXECUTE_TASK_REF = "agent.modules.scheduler.service:execute_scheduled_task"

logger = logging.getLogger(__name__)

_sync_engine = None


_scheduler: Optional[AsyncIOScheduler] = None


async def execute_scheduled_task(platform: str, user_id: str, task: str):
    """Execute a scheduled task: run the agent, inject results into user thread, and notify."""
    job_id = str(uuid.uuid4())
    background_thread_id = f"{BACKGROUND_THREAD_PREFIX}_{platform}_{user_id}_{task[:TASK_DESCRIPTION_MAX_LEN]}_{job_id}"
    user_thread_id = SessionManager.make_thread_id(platform, user_id, user_id)

    logger.info(f"Executing scheduled task for {user_thread_id}: {task}")

    try:
        response_text = await run_agent_full(
            user_input=task,
            thread_id=background_thread_id,
            agent_name=AGENT_NAME,
        )

        graph = get_workflow_graph(GRAPH_NAME)
        user_config = make_run_config(thread_id=user_thread_id)
        from agent.modules.conversations import upsert_conversation_thread

        await upsert_conversation_thread(
            thread_id=user_thread_id,
            agent_name=AGENT_NAME,
            title=task,
        )

        await graph.aupdate_state(
            user_config,
            {"messages": [HumanMessage(content=f"[Scheduled Task]\n{task}"), AIMessage(content=response_text)]},
            as_node=NODE_NAME,
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


def get_scheduler() -> AsyncIOScheduler:
    """Return the global scheduler instance. Raises RuntimeError if not initialized."""
    global _scheduler
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized. Call initialize_scheduler() first.")
    return _scheduler


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


async def initialize_scheduler():
    """Initialize and start the APScheduler. Must be called from an async context."""
    global _scheduler, _sync_engine
    if _scheduler is not None:
        return

    sync_url = _normalize_url_to_sync(get_database_url())
    _sync_engine = create_engine(sync_url, echo=False, pool_size=2, max_overflow=0)
    _migrate_legacy_job_references(_sync_engine)

    timezone_name, scheduler_tz = resolve_display_timezone()

    _scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(engine=_sync_engine)},
        timezone=scheduler_tz,
    )
    _scheduler.start()
    logger.info(
        "Background scheduler initialized and started. Timezone: %s",
        timezone_name,
    )


async def stop_scheduler():
    """Stop the scheduler gracefully."""
    global _scheduler, _sync_engine
    if _scheduler:
        _scheduler.shutdown(wait=True)
        _scheduler = None
    if _sync_engine:
        _sync_engine.dispose()
        _sync_engine = None
    logger.info("Background scheduler stopped.")
