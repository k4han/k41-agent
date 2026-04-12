import logging
import uuid
from typing import Optional

import tzlocal
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from discord import Client
from sqlalchemy import create_engine

from agent.modules.users.domain.constants import Platform
from agent.modules.agent_runtime.application.session import SessionManager
from agent.modules.agent_runtime.application.runner import run_agent_full
from agent.modules.workflows.public import get_workflow_graph, make_run_config
from agent.shared.infrastructure.db.engine import (
    get_database_url,
    _normalize_url_to_sync,
)
from langchain_core.messages import AIMessage, HumanMessage

AGENT_NAME = "default"
GRAPH_NAME = "react_agent"
NODE_NAME = "llm"
BACKGROUND_THREAD_PREFIX = "bg"
TASK_DESCRIPTION_MAX_LEN = 16
SCHEDULER_SHUTDOWN_TIMEOUT = 10

logger = logging.getLogger(__name__)

_telegram_bot: Optional[Bot] = None
_discord_client: Optional[Client] = None

_sync_engine = None


def set_telegram_bot(bot: Bot) -> None:
    global _telegram_bot
    _telegram_bot = bot


def set_discord_client(client: Client) -> None:
    global _discord_client
    _discord_client = client


async def send_push_notification(platform: str, user_id: str, message: str):
    """Send a push notification via the appropriate channel client."""
    try:
        if platform == Platform.TELEGRAM and _telegram_bot:
            await _telegram_bot.send_message(chat_id=user_id, text=message, parse_mode="HTML")
        elif platform == Platform.DISCORD and _discord_client:
            discord_user = _discord_client.get_user(int(user_id))
            if discord_user is None:
                discord_user = await _discord_client.fetch_user(int(user_id))
            if discord_user:
                await discord_user.send(message)
        else:
            logger.warning(f"No active client for platform {platform} to push notification.")
    except Exception as e:
        logger.error(f"Failed to send push notification to {platform} user {user_id}: {e}")


_scheduler: Optional[AsyncIOScheduler] = None


async def execute_scheduled_task(platform: str, user_id: str, task: str):
    """Execute a scheduled task: run the agent, inject results into user thread, and notify."""
    job_id = str(uuid.uuid4())
    background_thread_id = f"{BACKGROUND_THREAD_PREFIX}_{platform}_{user_id}_{task[:TASK_DESCRIPTION_MAX_LEN]}_{job_id}"
    user_thread_id = SessionManager.make_thread_id(platform, user_id)

    logger.info(f"Executing scheduled task for {user_thread_id}: {task}")

    try:
        response_text = await run_agent_full(
            user_input=task,
            thread_id=background_thread_id,
            agent_name=AGENT_NAME,
        )

        graph = get_workflow_graph(GRAPH_NAME)
        user_config = make_run_config(thread_id=user_thread_id)

        await graph.aupdate_state(
            user_config,
            {"messages": [HumanMessage(content=f"[Scheduled Task]\n{task}"), AIMessage(content=response_text)]},
            as_node=NODE_NAME,
        )

        notification = f"<b>Scheduled task completed:</b>\n{task}\n\n<b>Result:</b>\n{response_text}"
        await send_push_notification(platform, user_id, notification)

    except Exception as e:
        logger.error(f"Failed to execute scheduled task '{task}' for {user_thread_id}: {e}", exc_info=True)
        error_msg = f"<b>Scheduled task failed:</b>\n{task}\n\nError: {str(e)}"
        await send_push_notification(platform, user_id, error_msg)


def get_scheduler() -> AsyncIOScheduler:
    """Return the global scheduler instance. Raises RuntimeError if not initialized."""
    global _scheduler
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized. Call initialize_scheduler() first.")
    return _scheduler


async def initialize_scheduler():
    """Initialize and start the APScheduler. Must be called from an async context."""
    global _scheduler, _sync_engine
    if _scheduler is not None:
        return

    sync_url = _normalize_url_to_sync(get_database_url())
    _sync_engine = create_engine(sync_url, echo=False, pool_size=2, max_overflow=0)

    local_tz = tzlocal.get_localzone()

    _scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(engine=_sync_engine)},
        timezone=local_tz,
    )
    _scheduler.start()
    logger.info(f"Background scheduler initialized and started. Timezone: {local_tz}")


async def stop_scheduler():
    """Stop the scheduler gracefully."""
    global _scheduler, _sync_engine
    if _scheduler:
        _scheduler.shutdown(wait=True, timeout=SCHEDULER_SHUTDOWN_TIMEOUT)
        _scheduler = None
    if _sync_engine:
        _sync_engine.dispose()
        _sync_engine = None
    logger.info("Background scheduler stopped.")