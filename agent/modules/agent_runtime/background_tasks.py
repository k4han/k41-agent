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
from agent.modules.workspaces import (
    WorkspaceRef,
    remember_thread_workspace_ref,
    resolve_workspace_ref,
)
from agent.modules.workflows import REACT_AGENT_GRAPH_TYPE

logger = logging.getLogger(__name__)

MAX_COMPLETED_TASKS = 100
MAX_STORED_TEXT_LENGTH = 20_000
TASK_EVENT_QUEUE_SIZE = 100
BACKGROUND_THREAD_PREFIX = "task"
_GRAPH_NAME = REACT_AGENT_GRAPH_TYPE
_NODE_NAME = "llm"


def _parse_timestamp(value: float | str | None) -> float:
    """Convert a timestamp value to float seconds since epoch."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


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
    workspace: WorkspaceRef | None = None
    context_trim_threshold: int | None = None
    allowed_tool_names: list[str] | None = None
    allowed_skill_names: list[str] | None = None
    provider: str | None = None
    model: str | None = None
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
        end = self.completed_at if self.completed_at is not None else time.time()
        start = self.started_at if self.started_at is not None else self.created_at
        if start == 0.0:
            return 0.0
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
            "workspace": self.workspace.model_dump() if self.workspace else None,
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
            "allowed_tool_names": list(self.allowed_tool_names or []),
            "allowed_skill_names": list(self.allowed_skill_names or []),
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


def _task_usage_context(task: BackgroundTask) -> dict[str, str]:
    if task.thread_id:
        try:
            from agent.modules.agent_runtime.session import SessionManager

            platform, user_id, channel_id = SessionManager.parse_thread_id(task.thread_id)
            return {
                "platform": platform,
                "user_id": user_id,
                "channel_id": channel_id,
            }
        except ValueError:
            pass
    return {
        "platform": BACKGROUND_THREAD_PREFIX,
        "user_id": "dashboard",
        "channel_id": task.task_id,
    }


class BackgroundTaskManager:
    """Thread-safe manager for background agent tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    async def restore_from_persistence(self) -> None:
        """Restore task history from the background task table."""
        from agent.modules.agent_runtime.repository import get_background_task_repository

        try:
            repository = get_background_task_repository()
            records = await repository.list(limit=MAX_COMPLETED_TASKS, offset=0)
            new_tasks: dict[str, BackgroundTask] = {}
            for record in records:
                task = self._task_from_record(record)
                if task.task_id in self._tasks:
                    continue

                if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    task.status = TaskStatus.FAILED
                    task.error = "Interrupted by server restart"
                    task.completed_at = time.time()
                    await self._persist_task(task, repository=repository)

                new_tasks[task.task_id] = task

            with self._lock:
                self._tasks.update(new_tasks)
                self._trim_completed()
            logger.info("Restored %d background tasks from persistence.", len(new_tasks))
        except Exception as exc:
            logger.warning("Failed to restore background tasks: %s", exc)

    def _task_from_record(self, record: dict[str, Any]) -> BackgroundTask:
        status_value = str(record.get("status") or TaskStatus.FAILED.value)
        try:
            status = TaskStatus(status_value)
        except ValueError:
            status = TaskStatus.FAILED

        notify_channel = None
        notify_platform = str(record.get("notify_platform") or "")
        notify_external_id = str(record.get("notify_external_id") or "")
        if notify_platform and notify_external_id:
            notify_channel = NotifyChannel(
                platform=notify_platform,
                external_id=notify_external_id,
                channel_id=str(record.get("notify_channel_id") or notify_external_id),
            )

        return BackgroundTask(
            task_id=str(record.get("task_id") or ""),
            request=str(record.get("request") or ""),
            agent_name=str(record.get("agent_name") or "default"),
            workspace=(
                resolve_workspace_ref(record.get("workspace"))
                if record.get("workspace")
                else None
            ),
            notify_channel=notify_channel,
            status=status,
            result=str(record.get("result") or ""),
            error=str(record.get("error") or ""),
            thread_id=str(record.get("thread_id") or ""),
            allowed_tool_names=(
                list(record.get("allowed_tool_names"))
                if record.get("allowed_tool_names") is not None
                else None
            ),
            allowed_skill_names=(
                list(record.get("allowed_skill_names"))
                if record.get("allowed_skill_names") is not None
                else None
            ),
            created_at=_parse_timestamp(record.get("created_at")),
            started_at=_parse_timestamp(record.get("started_at")) or None,
            completed_at=_parse_timestamp(record.get("completed_at")) or None,
        )

    async def _persist_task(self, task: BackgroundTask, repository: Any | None = None) -> None:
        from agent.modules.agent_runtime.repository import get_background_task_repository

        notify_channel = task.notify_channel
        repo = repository or get_background_task_repository()
        await repo.upsert(
            task_id=task.task_id,
            thread_id=task.thread_id,
            request=task.request,
            agent_name=task.agent_name,
            working_dir=task.workspace.locator if task.workspace else None,
            workspace=task.workspace,
            notify_platform=notify_channel.platform if notify_channel else "",
            notify_external_id=notify_channel.external_id if notify_channel else "",
            notify_channel_id=notify_channel.channel_id if notify_channel else "",
            status=task.status.value,
            result=task.result,
            error=task.error,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            allowed_tool_names=task.allowed_tool_names,
            allowed_skill_names=task.allowed_skill_names,
        )

    async def submit(
        self,
        request: str,
        agent_name: str = "default",
        workspace: WorkspaceRef | dict[str, Any] | str | None = None,
        working_dir: str | None = None,
        notify_channel: NotifyChannel | None = None,
        completion_hook: Callable[[BackgroundTask], Awaitable[None]] | None = None,
        context_trim_threshold: int | None = None,
        allowed_tool_names: list[str] | None = None,
        allowed_skill_names: list[str] | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> str:
        """Submit a new background task and start it.

        Returns the task_id.
        """
        from agent.modules.agent_runtime.runner import run_agent_stream

        task = BackgroundTask(
            request=request,
            agent_name=agent_name,
            workspace=(
                resolve_workspace_ref(workspace if workspace is not None else working_dir)
                if workspace is not None or working_dir
                else None
            ),
            context_trim_threshold=context_trim_threshold if context_trim_threshold and context_trim_threshold > 0 else None,
            allowed_tool_names=list(allowed_tool_names) if allowed_tool_names else None,
            allowed_skill_names=(
                list(allowed_skill_names) if allowed_skill_names is not None else None
            ),
            provider=provider.strip() if provider else None,
            model=model.strip() if model else None,
            notify_channel=notify_channel,
            completion_hook=completion_hook,
        )
        task.thread_id = (
            f"{BACKGROUND_THREAD_PREFIX}_dashboard_{task.task_id}"
        )

        from agent.modules.conversations import (
            THREAD_KIND_BACKGROUND,
            schedule_conversation_title_generation,
            upsert_conversation_thread,
        )

        await self._persist_task(task)
        if task.workspace is not None:
            try:
                await remember_thread_workspace_ref(task.thread_id, task.workspace)
            except Exception as exc:
                logger.warning(
                    "Failed to remember workspace for background task %s: %s",
                    task.task_id,
                    exc,
                )
        await upsert_conversation_thread(
            thread_id=task.thread_id,
            agent_name=task.agent_name,
            title=task.request,
            kind=THREAD_KIND_BACKGROUND,
        )
        schedule_conversation_title_generation(
            thread_id=task.thread_id,
            title=task.request,
        )

        with self._lock:
            self._tasks[task.task_id] = task
            self._trim_completed()
        self._publish_task_event(task)

        async_task = asyncio.create_task(
            self._execute(task, run_agent_stream),
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
        from agent.modules.conversations import THREAD_KIND_BACKGROUND, upsert_conversation_thread

        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        try:
            await self._persist_task(task)
        except Exception as exc:
            logger.warning("Failed to persist background task %s start: %s", task.task_id, exc)
        self._publish_task_event(task)

        try:
            result = await self._run_streamed_task(task, run_fn)
            task.result = _truncate_stored_text(result)
            if task.completion_hook is not None:
                await task.completion_hook(task)
            task.status = TaskStatus.COMPLETED
            logger.info("Background task %s completed successfully.", task.task_id)
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.error = "Task was cancelled."
            self._publish_agent_event(task, {"type": "error", "content": task.error})
            logger.info("Background task %s was cancelled.", task.task_id)
        except BaseException as exc:
            from agent.shared.infrastructure.errors import classify_agent_error

            agent_error = classify_agent_error(exc)
            task.status = TaskStatus.FAILED
            task.error = _truncate_stored_text(agent_error.message)
            self._publish_agent_event(
                task,
                {
                    "type": "error",
                    "code": agent_error.code,
                    "content": agent_error.message,
                },
            )
            logger.error(
                "Background task %s failed: %s",
                task.task_id,
                exc,
                exc_info=True,
            )
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            task.completed_at = time.time()

            try:
                await self._persist_task(task)
                await upsert_conversation_thread(
                    thread_id=task.thread_id,
                    agent_name=task.agent_name,
                    title=task.request,
                    kind=THREAD_KIND_BACKGROUND,
                )
            except Exception as exc:
                logger.warning("Failed to persist background task %s completion: %s", task.task_id, exc)
            with self._lock:
                self._trim_completed()
                task._async_task = None
            self._publish_task_event(task)
            self._publish_done_event(task)
            await self._notify_completion(task)

    async def _run_streamed_task(self, task: BackgroundTask, run_fn: Any) -> str:
        message_chunks: list[str] = []
        final_content = ""

        async for event in run_fn(
            user_input=task.request,
            thread_id=task.thread_id,
            agent_name=task.agent_name,
            workspace=task.workspace,
            context_trim_threshold=task.context_trim_threshold,
            allowed_tool_names=task.allowed_tool_names,
            allowed_skill_names=task.allowed_skill_names,
            provider=task.provider,
            model=task.model,
            usage_context=_task_usage_context(task),
        ):
            if isinstance(event, dict):
                event_type = str(event.get("type") or "")
                content = event.get("content")
                if event_type == "message" and isinstance(content, str):
                    message_chunks.append(content)
                elif event_type == "final" and isinstance(content, str):
                    final_content = content
                self._publish_agent_event(task, event)

        return final_content or "".join(message_chunks)

    async def _notify_completion(self, task: BackgroundTask) -> None:
        """Inject results into the user's thread and send a push notification."""
        if task.notify_channel is None:
            return

        # Inject message pair into user's conversation thread so they can
        # continue chatting from the task context.
        if task.status == TaskStatus.COMPLETED and task.result:
            await self._inject_into_user_thread(task)

        request_preview = task.request[:200]
        notification_mode = "html"
        if task.status == TaskStatus.COMPLETED:
            message = (
                f"**\u2705 Task completed**\n"
                f"**Request:** {request_preview}\n\n"
                f"**Result:**\n{task.result[:1000]}"
            )
            notification_mode = "markdown"
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
            mode=notification_mode,
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

    def get_by_thread_id(self, thread_id: str) -> dict[str, Any] | None:
        """Return a task by its conversation thread ID, or None."""
        with self._lock:
            task = next(
                (task for task in self._tasks.values() if task.thread_id == thread_id),
                None,
            )
        return task.to_dict() if task else None

    def subscribe(self, thread_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to live events for a background task thread."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=TASK_EVENT_QUEUE_SIZE)
        with self._lock:
            self._subscribers.setdefault(thread_id, set()).add(queue)
        return queue

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a live event subscription."""
        with self._lock:
            subscribers = self._subscribers.get(thread_id)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(thread_id, None)

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

    async def remove(self, task_id: str) -> bool:
        """Remove a completed/failed task from history."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                return False
            thread_id = task.thread_id

        from agent.modules.agent_runtime.repository import get_background_task_repository
        from agent.modules.conversations import mark_conversation_thread_deleted
        from agent.modules.tools import close_thread_shell_sessions
        from agent.modules.workspaces import delete_thread_workspace
        from agent.modules.workflows import delete_workflow_thread_tree

        await get_background_task_repository().mark_deleted(task_id)
        if thread_id:
            close_thread_shell_sessions(thread_id)
            await delete_thread_workspace(thread_id)
            await mark_conversation_thread_deleted(thread_id)
            await delete_workflow_thread_tree(thread_id)

        with self._lock:
            current = self._tasks.get(task_id)
            if current is task and current.status not in (TaskStatus.RUNNING, TaskStatus.PENDING):
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

    def _publish_agent_event(self, task: BackgroundTask, event: dict[str, Any]) -> None:
        self._publish_event(task.thread_id, "agent", event)

    def _publish_task_event(self, task: BackgroundTask) -> None:
        self._publish_event(task.thread_id, "task", {"task": task.to_dict()})

    def _publish_done_event(self, task: BackgroundTask) -> None:
        self._publish_event(task.thread_id, "done", {"task": task.to_dict()})

    def _publish_event(self, thread_id: str, event_name: str, data: dict[str, Any]) -> None:
        if not thread_id:
            return
        with self._lock:
            subscribers = list(self._subscribers.get(thread_id, ()))

        payload = {"event": event_name, "data": data}
        for queue in subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass


# Module-level singleton
_manager = BackgroundTaskManager()


def get_background_task_manager() -> BackgroundTaskManager:
    """Return the global background task manager."""
    return _manager


__all__ = [
    "BackgroundTask",
    "BackgroundTaskManager",
    "NotifyChannel",
    "TASK_EVENT_QUEUE_SIZE",
    "TaskStatus",
    "get_background_task_manager",
]
