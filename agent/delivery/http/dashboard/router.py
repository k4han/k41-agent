from __future__ import annotations

from fastapi import APIRouter, Depends

from agent.modules.admin_auth import get_current_admin

from agent.delivery.http.dashboard.routes import (
    agents as agents_routes,
    catalog as catalog_routes,
    channels as channels_routes,
    conversations as conversations_routes,
    dashboard as dashboard_routes,
    generated_images as generated_images_routes,
    github as github_routes,
    mcp as mcp_routes,
    providers as providers_routes,
    sandboxes as sandboxes_routes,
    scheduler as scheduler_routes,
    settings as settings_routes,
    skills as skills_routes,
    spa as spa_routes,
    tasks as tasks_routes,
    usage as usage_routes,
    workspace as workspace_routes,
)

router = APIRouter(tags=["dashboard"], dependencies=[Depends(get_current_admin)])

for child_router in (
    spa_routes.router,
    catalog_routes.router,
    generated_images_routes.router,
    dashboard_routes.router,
    providers_routes.router,
    github_routes.router,
    workspace_routes.router,
    agents_routes.router,
    channels_routes.router,
    settings_routes.router,
    scheduler_routes.router,
    tasks_routes.router,
    usage_routes.router,
    conversations_routes.router,
    mcp_routes.router,
    sandboxes_routes.router,
    skills_routes.router,
):
    router.include_router(child_router)

__all__ = ["router"]
