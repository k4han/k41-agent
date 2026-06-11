from __future__ import annotations

import json
from typing import Any

from agent.modules.agent_runtime import get_active_session_registry

BACKGROUND_TASK_ACTIVE_STATUSES = {"pending", "running"}
SSE_HEARTBEAT_SECONDS = 15.0


def sse_event(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def active_session_for_thread(thread_id: str) -> dict[str, Any] | None:
    registry = get_active_session_registry()
    return next(
        (session for session in registry.list_active() if session["thread_id"] == thread_id),
        None,
    )


def is_active_background_task(task: dict[str, Any] | None) -> bool:
    return bool(task and task.get("status") in BACKGROUND_TASK_ACTIVE_STATUSES)
