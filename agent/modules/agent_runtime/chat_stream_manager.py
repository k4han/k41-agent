"""In-memory manager for executing agent chat streams in background tasks.

This allows chat stream runs to survive client disconnections (F5 or tab close),
while buffering generated stream events for client reconnection.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

from langgraph.errors import GraphRecursionError

from agent.shared.infrastructure.errors import (
    classify_agent_error,
    find_exception as _find_exception,
)

logger = logging.getLogger(__name__)

# Maximum number of events to buffer per session to prevent unbounded memory growth.
MAX_BUFFER_SIZE = 10_000

# Grace period (in seconds) after stream completes before removing the session.
# This gives reconnecting clients time to subscribe and consume buffered events.
SESSION_GRACE_PERIOD_SECONDS = 30


class ChatStreamSession:
    """Represents a background agent stream execution session."""

    def __init__(self, thread_id: str, params: dict[str, Any], run_fn: Any = None) -> None:
        self.thread_id = thread_id
        self.params = params
        self.run_fn = run_fn
        self.events_buffer: list[dict[str, Any]] = []
        self.queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self.task: asyncio.Task[None] | None = None
        self.lock = asyncio.Lock()
        self.done = False

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
                await self.push_event(event)
        except asyncio.CancelledError:
            logger.info("Background chat stream task cancelled for thread %s", self.thread_id)
            await self.push_event({"type": "error", "content": "Chat execution stopped."})
            raise
        except BaseException as exc:
            recursion_exc = _find_exception(exc, GraphRecursionError)
            if recursion_exc is not None:
                logger.warning("Recursion limit reached for thread %s", self.thread_id)
                await self.push_event({
                    "type": "error",
                    "code": "recursion_limit_reached",
                    "content": str(recursion_exc),
                })
            else:
                logger.exception(
                    "Error running background agent stream for thread %s", self.thread_id
                )
                agent_error = classify_agent_error(exc)
                await self.push_event({
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
            await manager.remove_session(self.thread_id)

    async def push_event(self, event: dict[str, Any]) -> None:
        """Push a generated stream event to the buffer and all subscribed queues."""
        async with self.lock:
            if len(self.events_buffer) < MAX_BUFFER_SIZE:
                self.events_buffer.append(event)
            for q in self.queues:
                q.put_nowait(event)

    async def subscribe(self) -> AsyncGenerator[dict[str, Any], None]:
        """Subscribe to stream events. Yields buffered events first, then new ones."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self.lock:
            # Replay all buffered events to the new subscriber
            for event in self.events_buffer:
                q.put_nowait(event)
            self.queues.add(q)
            is_done = self.done

        try:
            while True:
                if is_done and q.empty():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=0.5)
                    yield event
                except asyncio.TimeoutError:
                    pass
                async with self.lock:
                    is_done = self.done
        finally:
            async with self.lock:
                self.queues.discard(q)

    def cancel(self) -> None:
        """Cancel the background task execution."""
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
                existing.cancel()
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

    async def remove_session(self, thread_id: str) -> None:
        """Remove a session from the manager (thread-safe)."""
        async with self._lock:
            self._sessions.pop(thread_id, None)


_manager = ChatStreamManager()


def get_chat_stream_manager() -> ChatStreamManager:
    """Return the global chat stream manager instance."""
    return _manager


__all__ = [
    "ChatStreamSession",
    "ChatStreamManager",
    "get_chat_stream_manager",
]
