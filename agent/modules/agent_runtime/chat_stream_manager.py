"""In-memory manager for executing agent chat streams in background tasks.

This allows chat stream runs to survive client disconnections (F5 or tab close),
while buffering generated stream events for client reconnection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from bisect import bisect_right
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import aclosing, asynccontextmanager
from typing import Any, AsyncGenerator
from uuid import uuid4

from langgraph.errors import GraphRecursionError

from agent.modules.agent_runtime.protocol import application_event, protocol_event, run_agent_protocol_stream
from agent.shared.infrastructure.errors import (
    classify_agent_error,
    friendly_agent_error_message,
    find_exception as _find_exception,
)

logger = logging.getLogger(__name__)

# Bound recent event memory; archive older events to disk in batches.
MAX_BUFFER_BYTES = 1_048_576

# Grace period (in seconds) after stream completes before removing the session.
# This gives reconnecting clients time to subscribe and consume buffered events.
SESSION_GRACE_PERIOD_SECONDS = 30

# Allow cleanup to finish before retrying cancellation, then bound the retry too.
CANCELLATION_GRACE_PERIOD_SECONDS = 5


def _snapshot_json(value: Any) -> Any:
    """Copy JSON containers without serializing immutable scalar values."""
    if isinstance(value, dict):
        return {key: _snapshot_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_snapshot_json(item) for item in value]
    return value


def _snapshot_json_with_size(value: Any) -> tuple[Any, int]:
    """Snapshot an event and conservatively account for its retained memory."""
    if isinstance(value, dict):
        result = {}
        size = 0
        for key, item in value.items():
            child, child_size = _snapshot_json_with_size(item)
            result[key] = child
            size += sys.getsizeof(key) + child_size
        return result, sys.getsizeof(result) + size
    if isinstance(value, (list, tuple)):
        result = []
        size = 0
        for item in value:
            child, child_size = _snapshot_json_with_size(item)
            result.append(child)
            size += child_size
        return result, sys.getsizeof(result) + size
    return value, sys.getsizeof(value)


class _ReplayLog:
    """Keep live events in memory and archive only older batches."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._memory_bytes = 0
        self._first_seq = 1
        self._file = None
        self._file_size = 0
        # Each entry records the first sequence, byte offset, and batch length.
        self._batches: list[tuple[int, int, int]] = []
        self.closed = False

    def append(self, event: dict[str, Any]) -> None:
        snapshot, size = _snapshot_json_with_size(event)
        if self._events and self._memory_bytes + size > MAX_BUFFER_BYTES:
            self._spill()
        self._events.append(snapshot)
        self._memory_bytes += size
        if self._memory_bytes > MAX_BUFFER_BYTES:
            self._spill()

    def _spill(self) -> None:
        payload = json.dumps(self._events, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if self._file is None:
            self._file = tempfile.TemporaryFile(mode="w+b")
        self._file.seek(self._file_size)
        self._file.write(payload)
        self._batches.append((self._first_seq, self._file_size, len(payload)))
        self._file_size += len(payload)
        self._first_seq += len(self._events)
        self._events = []
        self._memory_bytes = 0

    def events_after(self, after_seq: int) -> Iterator[dict[str, Any]]:
        sequence = max(1, after_seq + 1)
        while True:
            if sequence >= self._first_seq:
                index = sequence - self._first_seq
                if index >= len(self._events):
                    return
                event = _snapshot_json(self._events[index])
                sequence += 1
                yield event
            else:
                batch_index = bisect_right(self._batches, sequence, key=lambda batch: batch[0]) - 1
                first_seq, offset, length = self._batches[batch_index]
                self._file.seek(offset)
                events = json.loads(self._file.read(length))
                for index in range(sequence - first_seq, len(events)):
                    event = events[index]
                    sequence = event["seq"] + 1
                    yield event
                del events

    def close(self) -> None:
        self.closed = True
        self._events.clear()
        self._memory_bytes = 0
        self._batches.clear()
        if self._file is not None:
            self._file.close()


class ChatStreamSession:
    """Represents a background agent stream execution session."""

    def __init__(self, thread_id: str, params: dict[str, Any], run_fn: Any = None) -> None:
        self.thread_id = thread_id
        self.run_fn = run_fn or run_agent_protocol_stream
        self.run_id = uuid4().hex
        self.last_seq = 0
        self.params = {**params, "_run_id": self.run_id}
        self._event_log = _ReplayLog()
        self._closed = False
        # Subscribers have independent cursors and a single coalesced wakeup.
        self.queues: set[asyncio.Queue[None]] = set()
        self.task: asyncio.Task[None] | None = None
        self.done = False
        self._superseded = False
        self._on_done: Callable[[ChatStreamSession], None] | None = None

    def start(self, *, on_done: Callable[[ChatStreamSession], None] | None = None) -> None:
        """Start the agent stream execution in a background task."""
        self._on_done = on_done
        self.task = asyncio.create_task(
            self._run_agent_background(),
            name=f"chat-stream-{self.thread_id}",
        )
        self.task.add_done_callback(self._task_finished)

    def _finish(self) -> None:
        """End subscriptions, including when the task never started."""
        self.done = True
        for queue in self.queues:
            if queue.empty():
                queue.put_nowait(None)

    def _task_finished(self, task: asyncio.Task[None]) -> None:
        self._finish()
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error(
                    "Chat stream task failed for thread %s", self.thread_id,
                    exc_info=(type(error), error, error.__traceback__),
                )
        if self._on_done is not None:
            self._on_done(self)

    async def _run_agent_background(self) -> None:
        logger.info("Starting chat stream run %s for thread %s", self.run_id, self.thread_id)
        try:
            async with aclosing(self.run_fn(**self.params)) as events:
                async for event in events:
                    update = application_event(event)
                    if update is not None and update.get("type") == "error":
                        await self._push_error_event(update)
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
            self._finish()

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
        await self.push_event(protocol_event("custom:k41", {**event, "projection": "ui"}))

    async def push_event(self, event: dict[str, Any]) -> None:
        """Append an event to the replay log and wake subscribers."""
        # Log access and subscriber registration never suspend on this event loop.
        self.last_seq += 1
        event = {**event, "seq": self.last_seq, "event_id": f"{self.run_id}:{self.last_seq}"}
        self._event_log.append(event)
        for q in self.queues:
            if q.empty():
                q.put_nowait(None)

    async def subscribe(self, *, after_seq: int = 0) -> AsyncGenerator[dict[str, Any], None]:
        """Subscribe to stream events. Yields buffered events first, then new ones."""
        q: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        if self._closed:
            return
        self.queues.add(q)

        try:
            while True:
                for event in self._event_log.events_after(after_seq):
                    after_seq = event["seq"]
                    yield event
                if self.done:
                    break
                try:
                    await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.queues.discard(q)
            if self._closed and not self.queues:
                self._event_log.close()

    def close(self) -> None:
        """Release the replay log after the last existing subscriber leaves."""
        self._closed = True
        if not self.queues:
            self._event_log.close()

    def cancel(self, *, superseded: bool = False, force: bool = False) -> None:
        """Request cancellation, optionally interrupting an earlier cancellation."""
        self._superseded = self._superseded or superseded
        if self.task and not self.task.done() and (force or not self.task.cancelling()):
            self.task.cancel()


class ChatStreamManager:
    """Registry and manager for active ChatStreamSessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatStreamSession] = {}
        self._thread_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._expiry_handles: dict[ChatStreamSession, asyncio.TimerHandle] = {}
        self._closing = False

    @property
    def closing(self) -> bool:
        """Whether shutdown has begun and new runs are rejected."""
        return self._closing

    @asynccontextmanager
    async def _lock_thread(self, thread_id: str) -> AsyncIterator[None]:
        lock_state = self._thread_locks.get(thread_id)
        if lock_state is None:
            lock, users = asyncio.Lock(), 0
        else:
            lock, users = lock_state
        self._thread_locks[thread_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self._thread_locks[thread_id]
            if users == 1:
                del self._thread_locks[thread_id]
            else:
                self._thread_locks[thread_id] = (lock, users - 1)

    def _ensure_open(self) -> None:
        if self._closing:
            raise RuntimeError("Chat stream manager is shutting down.")

    def _session_finished(self, session: ChatStreamSession) -> None:
        """Retain replay independently of execution and cancellation."""
        if self._closing or self._sessions.get(session.thread_id) is not session:
            self._remove_session(session.thread_id, session)
            return
        self._expiry_handles[session] = asyncio.get_running_loop().call_later(
            SESSION_GRACE_PERIOD_SECONDS, self._remove_session, session.thread_id, session,
        )

    def _remove_session(self, thread_id: str, session: ChatStreamSession) -> None:
        # All registry mutations run synchronously on the manager's event loop.
        if self._sessions.get(thread_id) is session:
            self._sessions.pop(thread_id)
        handle = self._expiry_handles.pop(session, None)
        if handle is not None:
            handle.cancel()
        session.close()

    async def _wait_for_sessions(self, sessions: list[ChatStreamSession]) -> None:
        """Wait for cleanup, retry cancellation, and fail if execution still runs."""
        tasks = {session.task for session in sessions if session.task is not None}
        if not tasks:
            return
        # asyncio.wait never propagates a waiting request's cancellation to runs.
        _, pending = await asyncio.wait(tasks, timeout=CANCELLATION_GRACE_PERIOD_SECONDS)
        if not pending:
            return
        for session in sessions:
            if session.task in pending:
                logger.warning("Retrying chat stream cancellation for thread %s", session.thread_id)
                session.cancel(force=True)
        _, pending = await asyncio.wait(pending, timeout=CANCELLATION_GRACE_PERIOD_SECONDS)
        if pending:
            thread_ids = ", ".join(session.thread_id for session in sessions if session.task in pending)
            raise TimeoutError(f"Chat stream cancellation timed out for threads: {thread_ids}")

    async def get_or_create_session(
        self, thread_id: str, params: dict[str, Any], run_fn: Any = None
    ) -> ChatStreamSession:
        """Get or create an active background stream session for a thread.

        Wait for the previous execution to stop before starting its replacement.
        Cancelling a waiting request must not interrupt the old run's cleanup.
        """
        self._ensure_open()
        async with self._lock_thread(thread_id):
            self._ensure_open()
            existing = self._sessions.get(thread_id)
            if existing and existing.task and not existing.task.done():
                logger.info("Cancelling existing stream session for thread %s to start new message", thread_id)
                existing.cancel(superseded=True)
                await self._wait_for_sessions([existing])

            self._ensure_open()
            if existing is not None:
                self._remove_session(thread_id, existing)
            session = ChatStreamSession(thread_id, params, run_fn)
            self._sessions[thread_id] = session
            session.start(on_done=self._session_finished)
            return session

    async def get_session(self, thread_id: str) -> ChatStreamSession | None:
        """Retrieve an active stream session for a thread, if any exists."""
        return self._sessions.get(thread_id)

    async def remove_session(self, thread_id: str, session: ChatStreamSession | None = None) -> None:
        """Remove a session on the manager's event loop.

        When ``session`` is provided, only remove the entry if it is still the
        same object. This prevents a just-cancelled session's grace-period
        cleanup from deleting a newly created session for the same thread
        (e.g. user cancelled then immediately sent a new message).
        """
        target = session if session is not None else self._sessions.get(thread_id)
        if target is not None:
            self._remove_session(thread_id, target)

    async def shutdown(self) -> None:
        """Reject new runs and finish cancellation before dependencies close."""
        self._closing = True
        sessions = list(self._sessions.values())
        for session in sessions:
            session.cancel()
        try:
            await self._wait_for_sessions(sessions)
        finally:
            for session in sessions:
                if session.task is None or session.task.done():
                    self._remove_session(session.thread_id, session)


def get_chat_stream_manager(container=None) -> ChatStreamManager:
    """Return container-scoped chat stream manager."""
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).chat_stream_manager


__all__ = [
    "ChatStreamSession",
    "ChatStreamManager",
    "get_chat_stream_manager",
]
