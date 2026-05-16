"""In-memory registry for background agent tasks.

Background tasks are long-running agent executions that:
- Are submitted via dashboard or adapters (Telegram /task, etc.)
- Run asynchronously in the background
- Track their lifecycle: pending -> running -> completed/failed
- Store results for later retrieval
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from html import escape as escape_html
from typing import Any, Awaitable, Callable

from agent.modules.notifications import send_notification as _send_notification
from agent.modules.workflows import REACT_AGENT_GRAPH_TYPE

logger = logging.getLogger(__name__)

MAX_COMPLETED_TASKS = 100
MAX_STORED_TEXT_LENGTH = 20_000
BACKGROUND_THREAD_PREFIX = "task"
_GRAPH_NAME = REACT_AGENT_GRAPH_TYPE
_NODE_NAME = "llm"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class NotifyChannel:
    """Destination for task completion notification."""

    platform: str  # e.g. "telegram" or "discord"
    external_id: str  # chat_id or Discord user_id
    channel_id: str = ""  # channel/chat ID for thread matching


@dataclass
class BackgroundTask:
    """A background agent task with lifecycle tracking."""

    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    request: str = ""
    agent_name: str = "default"
    working_dir: str | None = None
    notify_channel: NotifyChannel | None = None
    completion_hook: Callable[["BackgroundTask"], Awaitable[None]] | None = field(
        default=None,
        repr=False,
    )
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    thread_id: str = ""
    _async_task: asyncio.Task | None = field(default=None, repr=False)

    def elapsed_seconds(self) -> float:
        """Return elapsed time since creation."""
        end = self.completed_at or time.time()
        start = self.started_at or self.created_at
        return end - start

    def to_dict(self) -> dict[str, Any]:
        elapsed = self.elapsed_seconds()
        notify_info = None
        if self.notify_channel:
            notify_info = {
                "platform": self.notify_channel.platform,
                "external_id": self.notify_channel.external_id,
                "channel_id": self.notify_channel.channel_id,
            }
        return {
            "task_id": self.task_id,
            "request": self.request,
            "agent_name": self.agent_name,
            "working_dir": self.working_dir,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": round(elapsed, 1),
            "elapsed_display": _format_elapsed(elapsed),
            "thread_id": self.thread_id,
            "notify_channel": notify_info,
        }


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds into a human-readable string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _truncate_stored_text(text: str) -> str:
    if len(text) <= MAX_STORED_TEXT_LENGTH:
        return text
    return text[:MAX_STORED_TEXT_LENGTH] + "\n...[truncated]"


class BackgroundTaskManager:
    """Thread-safe manager for background agent tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._lock = threading.Lock()

    async def submit(
        self,
        request: str,
        agent_name: str = "default",
        working_dir: str | None = None,
        notify_channel: NotifyChannel | None = None,
        completion_hook: Callable[[BackgroundTask], Awaitable[None]] | None = None,
    ) -> str:
        """Submit a new background task and start it.

        Returns the task_id.
        """
        from agent.modules.agent_runtime.runner import run_agent_full

        task = BackgroundTask(
            request=request,
            agent_name=agent_name,
            working_dir=working_dir,
            notify_channel=notify_channel,
            completion_hook=completion_hook,
        )
        task.thread_id = (
            f"{BACKGROUND_THREAD_PREFIX}_dashboard_{task.task_id}"
        )

        with self._lock:
            self._tasks[task.task_id] = task
            self._trim_completed()

        async_task = asyncio.create_task(
            self._execute(task, run_agent_full),
            name=f"bg-task-{task.task_id}",
        )
        task._async_task = async_task

        logger.info(
            "Background task %s submitted: agent=%s notify=%s request=%r",
            task.task_id,
            agent_name,
            notify_channel.platform if notify_channel else "none",
            request[:80],
        )
        return task.task_id

    async def _execute(self, task: BackgroundTask, run_fn: Any) -> None:
        """Execute the task in the background."""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        try:
            result = await run_fn(
                user_input=task.request,
                thread_id=task.thread_id,
                agent_name=task.agent_name,
                working_dir=task.working_dir,
            )
            task.result = _truncate_stored_text(result)
            if task.completion_hook is not None:
                await task.completion_hook(task)
            task.status = TaskStatus.COMPLETED
            logger.info("Background task %s completed successfully.", task.task_id)
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.error = "Task was cancelled."
            logger.info("Background task %s was cancelled.", task.task_id)
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error = _truncate_stored_text(str(exc))
            logger.error(
                "Background task %s failed: %s",
                task.task_id,
                exc,
                exc_info=True,
            )
        finally:
            task.completed_at = time.time()
            task._async_task = None
            with self._lock:
                self._trim_completed()
            await self._notify_completion(task)

    async def _notify_completion(self, task: BackgroundTask) -> None:
        """Inject results into the user's thread and send a push notification."""
        if task.notify_channel is None:
            return

        # Inject message pair into user's conversation thread so they can
        # continue chatting from the task context.
        if task.status == TaskStatus.COMPLETED and task.result:
            await self._inject_into_user_thread(task)

        request_preview = task.request[:200]
        if task.status == TaskStatus.COMPLETED:
            message = (
                f"<b>\u2705 Task completed</b>\n"
                f"<b>Request:</b> {escape_html(request_preview)}\n\n"
                f"<b>Result:</b>\n{escape_html(task.result[:1000])}"
            )
        elif task.status == TaskStatus.FAILED:
            message = (
                f"<b>\u274c Task failed</b>\n"
                f"<b>Request:</b> {escape_html(request_preview)}\n\n"
                f"<b>Error:</b> {escape_html(task.error[:500])}"
            )
        elif task.status == TaskStatus.CANCELLED:
            message = (
                f"<b>\u26a0\ufe0f Task cancelled</b>\n"
                f"<b>Request:</b> {escape_html(request_preview)}"
            )
        else:
            return

        await _send_notification(
            platform=task.notify_channel.platform,
            external_id=task.notify_channel.external_id,
            message=message,
        )

    async def _inject_into_user_thread(self, task: BackgroundTask) -> None:
        """Inject the task request/result as a message pair into the user's thread.

        This allows the user to continue chatting from the task context
        on their preferred channel (Telegram, Discord, etc.).
        """
        from agent.modules.agent_runtime.session import SessionManager
        from agent.modules.workflows import get_workflow_graph, make_run_config
        from langchain_core.messages import AIMessage, HumanMessage

        channel = task.notify_channel
        user_thread_id = SessionManager.make_thread_id(
            channel.platform, channel.external_id, channel.channel_id
        )

        try:
            graph = get_workflow_graph(_GRAPH_NAME)
            user_config = make_run_config(thread_id=user_thread_id)

            await graph.aupdate_state(
                user_config,
                {
                    "messages": [
                        HumanMessage(content=f"[Background Task]\n{task.request}"),
                        AIMessage(content=task.result),
                    ]
                },
                as_node=_NODE_NAME,
            )
            logger.info(
                "Injected background task %s results into thread %s.",
                task.task_id,
                user_thread_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to inject task %s results into thread %s: %s",
                task.task_id,
                user_thread_id,
                exc,
            )

    def get(self, task_id: str) -> dict[str, Any] | None:
        """Return a single task as a dict, or None."""
        with self._lock:
            task = self._tasks.get(task_id)
        return task.to_dict() if task else None

    def list_all(self) -> list[dict[str, Any]]:
        """Return all tasks, newest first."""
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tasks]

    def cancel(self, task_id: str) -> str:
        """Cancel a running task and return the outcome."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return "not_found"
            if task._async_task and not task._async_task.done():
                task._async_task.cancel()
                return "cancelled"
        return "not_running"

    def remove(self, task_id: str) -> bool:
        """Remove a completed/failed task from history."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                return False
            del self._tasks[task_id]
            return True

    def count_by_status(self) -> dict[str, int]:
        """Return counts grouped by status."""
        counts: dict[str, int] = {}
        with self._lock:
            for task in self._tasks.values():
                key = task.status.value
                counts[key] = counts.get(key, 0) + 1
        return counts

    def _trim_completed(self) -> None:
        """Trim old completed/failed tasks to keep memory bounded."""
        completed = [
            t
            for t in self._tasks.values()
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
        ]
        if len(completed) <= MAX_COMPLETED_TASKS:
            return
        completed.sort(key=lambda t: t.completed_at or 0)
        excess = len(completed) - MAX_COMPLETED_TASKS
        for task in completed[:excess]:
            self._tasks.pop(task.task_id, None)


# Module-level singleton
_manager = BackgroundTaskManager()


def get_background_task_manager() -> BackgroundTaskManager:
    """Return the global background task manager."""
    return _manager


__all__ = [
    "BackgroundTask",
    "BackgroundTaskManager",
    "NotifyChannel",
    "TaskStatus",
    "get_background_task_manager",
]
