"""In-memory manager for executing agent chat streams in background tasks.

This allows chat stream runs to survive client disconnections (F5 or tab close),
while buffering generated stream events for client reconnection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from typing import Any, AsyncGenerator

from langgraph.errors import GraphRecursionError

from agent.shared.infrastructure.errors import (
    classify_agent_error,
    friendly_agent_error_message,
    find_exception as _find_exception,
)

logger = logging.getLogger(__name__)

# Larger replay logs spill to disk without discarding any events.
MAX_BUFFER_BYTES = 1_048_576

# Grace period (in seconds) after stream completes before removing the session.
# This gives reconnecting clients time to subscribe and consume buffered events.
SESSION_GRACE_PERIOD_SECONDS = 30


class ChatStreamSession:
    """Represents a background agent stream execution session."""

    def __init__(self, thread_id: str, params: dict[str, Any], run_fn: Any = None) -> None:
        self.thread_id = thread_id
        self.params = params
        self.run_fn = run_fn
        self._event_log = tempfile.SpooledTemporaryFile(max_size=MAX_BUFFER_BYTES, mode="w+b")
        self._closed = False
        # Subscribers retain only a cursor and a single coalesced wakeup.
        self.queues: set[asyncio.Queue[None]] = set()
        self.task: asyncio.Task[None] | None = None
        self.lock = asyncio.Lock()
        self.done = False
        self._superseded = False

    def start(self) -> None:
        """Start the agent stream execution in a background task."""
        self.task = asyncio.create_task(
            self._run_agent_background(),
            name=f"chat-stream-{self.thread_id}",
        )

    async def _run_agent_background(self) -> None:
        run_fn = self.run_fn
        if run_fn is None:
            from agent.modules.agent_runtime.runner import run_agent_stream
            run_fn = run_agent_stream

        logger.info("Starting background chat stream task for thread %s", self.thread_id)
        try:
            async for event in run_fn(**self.params):
                if event.get("type") == "error":
                    await self._push_error_event(event)
                else:
                    await self.push_event(event)
        except asyncio.CancelledError:
            logger.info("Background chat stream task cancelled for thread %s", self.thread_id)
            await self._push_error_event({"type": "error", "content": "Chat execution stopped."})
            raise
        except BaseException as exc:
            recursion_exc = _find_exception(exc, GraphRecursionError)
            if recursion_exc is not None:
                logger.warning("Recursion limit reached for thread %s", self.thread_id)
                await self._push_error_event({
                    "type": "error",
                    "code": "recursion_limit_reached",
                    "content": str(recursion_exc),
                })
            else:
                logger.exception(
                    "Error running background agent stream for thread %s", self.thread_id
                )
                agent_error = classify_agent_error(exc)
                await self._push_error_event({
                    "type": "error",
                    "code": agent_error.code,
                    "content": agent_error.message,
                })
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            self.done = True
            # Wait for a grace period so reconnecting clients can consume buffered events.
            # Check periodically whether any subscribers are still active.
            elapsed = 0.0
            interval = 1.0
            while elapsed < SESSION_GRACE_PERIOD_SECONDS:
                await asyncio.sleep(interval)
                elapsed += interval
                async with self.lock:
                    has_subscribers = len(self.queues) > 0
                # If all subscribers have disconnected and we've waited at least a bit, clean up.
                if not has_subscribers and elapsed >= 2.0:
                    break

            manager = get_chat_stream_manager()
            await manager.remove_session(self.thread_id, self)

    async def _push_error_event(self, event: dict[str, Any]) -> None:
        """Save an error before delivering it so history can restore identical text."""
        from agent.modules.conversations import record_thread_run_error

        event = {**event, "message": friendly_agent_error_message(
            event.get("message") or event.get("content"), code=event.get("code"),
        )}
        try:
            if not self._superseded:
                await record_thread_run_error(self.thread_id, event)
        except Exception:
            logger.warning("Failed to persist chat error for thread %s", self.thread_id, exc_info=True)
        await self.push_event(event)

    async def push_event(self, event: dict[str, Any]) -> None:
        """Append an event to the replay log and wake subscribers."""
        payload = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        async with self.lock:
            self._event_log.seek(0, 2)
            self._event_log.write(payload)
            for q in self.queues:
                if q.empty():
                    q.put_nowait(None)

    async def subscribe(self) -> AsyncGenerator[dict[str, Any], None]:
        """Subscribe to stream events. Yields buffered events first, then new ones."""
        q: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        async with self.lock:
            if self._closed:
                return
            self.queues.add(q)
        position = 0

        try:
            while True:
                async with self.lock:
                    self._event_log.seek(position)
                    line = self._event_log.readline()
                    position = self._event_log.tell()
                    is_done = self.done
                if line:
                    yield json.loads(line)
                    continue
                if is_done:
                    break
                try:
                    await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
        finally:
            async with self.lock:
                self.queues.discard(q)
                if self._closed and not self.queues:
                    self._event_log.close()

    def close(self) -> None:
        """Release the replay log after the last existing subscriber leaves."""
        self._closed = True
        if not self.queues:
            self._event_log.close()

    def cancel(self, *, superseded: bool = False) -> None:
        """Cancel the background task execution."""
        self._superseded = self._superseded or superseded
        if self.task and not self.task.done():
            self.task.cancel()


class ChatStreamManager:
    """Registry and manager for active ChatStreamSessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatStreamSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_session(
        self, thread_id: str, params: dict[str, Any], run_fn: Any = None
    ) -> ChatStreamSession:
        """Get or create an active background stream session for a thread.

        If a session already exists for the thread and is still running,
        it is cancelled first.
        """
        async with self._lock:
            existing = self._sessions.get(thread_id)
            if existing and not existing.done:
                logger.info("Cancelling existing stream session for thread %s to start new message", thread_id)
                existing.cancel(superseded=True)
                # Give it a brief moment to cancel/clean up
                await asyncio.sleep(0.05)

            session = ChatStreamSession(thread_id, params, run_fn)
            self._sessions[thread_id] = session
            session.start()
            return session

    async def get_session(self, thread_id: str) -> ChatStreamSession | None:
        """Retrieve an active stream session for a thread, if any exists."""
        async with self._lock:
            return self._sessions.get(thread_id)

    async def remove_session(self, thread_id: str, session: ChatStreamSession | None = None) -> None:
        """Remove a session from the manager (thread-safe).

        When ``session`` is provided, only remove the entry if it is still the
        same object. This prevents a just-cancelled session's grace-period
        cleanup from deleting a newly created session for the same thread
        (e.g. user cancelled then immediately sent a new message).
        """
        async with self._lock:
            if session is not None:
                existing = self._sessions.get(thread_id)
                if existing is not session:
                    session.close()
                    return
            removed = self._sessions.pop(thread_id, None)
            if removed is not None:
                removed.close()


def get_chat_stream_manager(container=None) -> ChatStreamManager:
    """Return container-scoped chat stream manager."""
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).chat_stream_manager


__all__ = [
    "ChatStreamSession",
    "ChatStreamManager",
    "get_chat_stream_manager",
]
