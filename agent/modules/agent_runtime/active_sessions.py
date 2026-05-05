"""In-memory registry for tracking currently running agent sessions."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

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
        self._lock = threading.Lock()

    def register(self, session: ActiveSession) -> str:
        """Register a new active session."""
        with self._lock:
            self._sessions[session.session_id] = session
        return session.session_id

    def unregister(self, session_id: str) -> None:
        """Remove a session when it finishes."""
        with self._lock:
            self._sessions.pop(session_id, None)

    def update_step(self, session_id: str, step: str) -> None:
        """Update the current processing step of a session."""
        with self._lock:
            if session := self._sessions.get(session_id):
                if session.current_step != step:
                    session.current_step = step

    def add_tool_call(self, session_id: str, tool_name: str) -> None:
        """Record a tool call and update the current step."""
        with self._lock:
            if session := self._sessions.get(session_id):
                session.tools_called.append(tool_name)
                del session.tools_called[:-MAX_RECORDED_TOOLS]
                session.current_step = f"{TOOL_STEP_PREFIX}{tool_name}"

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
]
