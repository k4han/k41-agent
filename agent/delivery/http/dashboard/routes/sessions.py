from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from agent.modules.agent_runtime import get_active_session_registry


router = APIRouter()


@router.get("/sessions/active")
async def list_active_sessions() -> dict[str, Any]:
    registry = get_active_session_registry()
    sessions = registry.list_active()
    return {"sessions": sessions, "count": len(sessions)}
