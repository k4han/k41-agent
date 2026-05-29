"""In-memory registry for tracking currently running agent sessions."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

current_session_id_var: ContextVar[str | None] = ContextVar("current_session_id", default=None)

MAX_RECORDED_TOOLS = 20

SESSION_STEP_INITIALIZING = "initializing"
SESSION_STEP_THINKING = "thinking"
SESSION_STEP_RESPONDING = "responding"
TOOL_STEP_PREFIX = "tool:"


@dataclass
class ActiveSession:
    """A currently running agent session."""

    thread_id: str
    platform: str
    user_id: str
    channel_id: str
    agent_name: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.time)
    current_step: str = SESSION_STEP_INITIALIZING
    tools_called: list[str] = field(default_factory=list)
    running_pids: set[int] = field(default_factory=set)

    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at

    def to_dict(self) -> dict[str, Any]:
        elapsed = self.elapsed_seconds()
        return {
            "thread_id": self.thread_id,
            "session_id": self.session_id,
            "platform": self.platform,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "agent_name": self.agent_name,
            "started_at": self.started_at,
            "elapsed_seconds": round(elapsed, 1),
            "elapsed_display": _format_elapsed(elapsed),
            "current_step": self.current_step,
            "tools_called": list(self.tools_called),
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


class ActiveSessionRegistry:
    """Thread-safe, in-memory registry of currently running sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, ActiveSession] = {}
        self._tasks: dict[str, Any] = {}
        self._listeners: set[Any] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> Any:
        """Subscribe to session events (returns asyncio.Queue)."""
        import asyncio
        q = asyncio.Queue()
        with self._lock:
            self._listeners.add(q)
        return q

    def unsubscribe(self, q: Any) -> None:
        """Unsubscribe from session events."""
        with self._lock:
            self._listeners.discard(q)

    def register_pid(self, session_id: str, pid: int) -> None:
        """Register an OS subprocess PID running under a session."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.running_pids.add(pid)

    def unregister_pid(self, session_id: str, pid: int) -> None:
        """Unregister an OS subprocess PID."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.running_pids.discard(pid)

    def _broadcast(self, event_type: str, data: Any) -> None:
        """Broadcast an event to all async listeners safely across threads."""
        import asyncio
        with self._lock:
            listeners = list(self._listeners)
        if not listeners:
            return

        event = {"type": event_type, "data": data}

        def push_to_queues():
            for q in listeners:
                try:
                    q.put_nowait(event)
                except Exception:
                    pass

        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(push_to_queues)
        except RuntimeError:
            # No running loop (e.g. outside asyncio environment)
            pass

    def register(self, session: ActiveSession, task: Any | None = None) -> str:
        """Register a new active session."""
        with self._lock:
            self._sessions[session.session_id] = session
            if task is not None:
                self._tasks[session.session_id] = task
            session_dict = session.to_dict()
        self._broadcast("session_started", session_dict)
        return session.session_id

    def unregister(self, session_id: str) -> None:
        """Remove a session when it finishes."""
        with self._lock:
            session = self._sessions.get(session_id)
            thread_id = session.thread_id if session else ""
            self._sessions.pop(session_id, None)
            self._tasks.pop(session_id, None)
        if thread_id:
            self._broadcast("session_stopped", {"session_id": session_id, "thread_id": thread_id})

    def cancel_session(self, session_id: str) -> bool:
        """Cancel the asyncio task and kill all running subprocesses associated with this session."""
        pids_to_kill = set()
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                pids_to_kill = set(session.running_pids)
            task = self._tasks.get(session_id)
            task_cancelled = False
            if task is not None:
                try:
                    task.cancel()
                    task_cancelled = True
                except Exception:
                    pass

        # Kill OS subprocesses outside the lock to prevent deadlock
        for pid in pids_to_kill:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception as exc:
                logger.debug("Failed to kill registered subprocess PID %d: %s", pid, exc)

        return task_cancelled

    def cancel_by_thread(self, thread_id: str) -> bool:
        """Cancel all sessions/tasks and kill their subprocesses associated with a thread_id."""
        cancelled = False
        sessions_to_cancel = []
        with self._lock:
            for session_id, session in list(self._sessions.items()):
                if session.thread_id == thread_id:
                    sessions_to_cancel.append(session_id)

        for session_id in sessions_to_cancel:
            if self.cancel_session(session_id):
                cancelled = True

        return cancelled

    def update_step(self, session_id: str, step: str) -> None:
        """Update the current processing step of a session."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                if session.current_step != step:
                    session.current_step = step
                    session_dict = session.to_dict()
                else:
                    session_dict = None
            else:
                session_dict = None
        if session_dict:
            self._broadcast("session_updated", session_dict)

    def add_tool_call(self, session_id: str, tool_name: str) -> None:
        """Record a tool call and update the current step."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.tools_called.append(tool_name)
                del session.tools_called[:-MAX_RECORDED_TOOLS]
                session.current_step = f"{TOOL_STEP_PREFIX}{tool_name}"
                session_dict = session.to_dict()
            else:
                session_dict = None
        if session_dict:
            self._broadcast("session_updated", session_dict)

    def list_active(self) -> list[dict[str, Any]]:
        """Return all active sessions as serializable dicts."""
        with self._lock:
            sessions = list(self._sessions.values())
        return [session.to_dict() for session in sessions]

    def get(self, session_id: str) -> dict[str, Any] | None:
        """Return a single session dict, or None."""
        with self._lock:
            session = self._sessions.get(session_id)
        return session.to_dict() if session else None

    def count(self) -> int:
        """Return the number of active sessions."""
        with self._lock:
            return len(self._sessions)


# Module-level singleton
_registry = ActiveSessionRegistry()


def get_active_session_registry() -> ActiveSessionRegistry:
    """Return the global active session registry."""
    return _registry


__all__ = [
    "ActiveSession",
    "ActiveSessionRegistry",
    "SESSION_STEP_INITIALIZING",
    "SESSION_STEP_RESPONDING",
    "SESSION_STEP_THINKING",
    "TOOL_STEP_PREFIX",
    "get_active_session_registry",
    "current_session_id_var",
]
