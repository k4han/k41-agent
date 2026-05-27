from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from agent.delivery.http.dashboard.routes.shared import _dashboard_spa


router = APIRouter()


# --- SPA routes -------------------------------------------------------


@router.get("/", include_in_schema=False)
@router.get("/chat", include_in_schema=False)
@router.get("/c/{thread_id:path}", include_in_schema=False)
@router.get("/sessions", include_in_schema=False)
@router.get("/tasks", include_in_schema=False)
@router.get("/scheduler", include_in_schema=False)
@router.get("/repositories", include_in_schema=False)
@router.get("/history", include_in_schema=False)
@router.get("/history/{thread_id:path}", include_in_schema=False)
@router.get("/settings/config", include_in_schema=False)
@router.get("/settings/providers", include_in_schema=False)
@router.get("/settings/connections", include_in_schema=False)
@router.get("/settings/channels", include_in_schema=False)
@router.get("/settings/agents", include_in_schema=False)
@router.get("/settings/prompt-variables", include_in_schema=False)
@router.get("/settings/security", include_in_schema=False)
@router.get("/settings/appearance", include_in_schema=False)
@router.get("/settings/usage", include_in_schema=False)
# Legacy routes - SPA handles redirect to /settings/*
@router.get("/channels", include_in_schema=False)
@router.get("/agents", include_in_schema=False)
@router.get("/change-password", include_in_schema=False)
async def dashboard_spa() -> Response:
    return _dashboard_spa()
