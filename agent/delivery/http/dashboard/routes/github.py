from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from agent.modules.agents import get_catalog_service
from agent.modules.github import (
    get_github_automation_service,
    get_github_settings,
)


router = APIRouter()


@router.get("/dashboard-api/github")
async def get_dashboard_github(request: Request) -> dict[str, Any]:
    settings = get_github_settings()
    service = get_github_automation_service()
    cards = get_catalog_service().list_agent_cards()
    agent_names = sorted(
        card.name for card in cards if card.valid and not getattr(card, "hidden", False)
    )
    webhook_url = f"{str(request.base_url).rstrip('/')}/channels/github/webhook"
    install_url = (
        f"https://github.com/apps/{settings.app_slug}/installations/new"
        if settings.app_slug
        else ""
    )
    return {
        "configured": settings.is_configured,
        "enabled": settings.enabled,
        "app_slug": settings.app_slug,
        "webhook_url": webhook_url,
        "install_url": install_url,
        "default_agent": settings.default_agent,
        "trigger_label": settings.trigger_label,
        "mention_triggers": list(settings.mention_triggers),
        "repositories": await service.list_repository_bindings(),
        "agent_names": agent_names,
    }

@router.post("/dashboard-api/github/sync")
async def sync_dashboard_github() -> dict[str, Any]:
    service = get_github_automation_service()
    try:
        result = await service.sync_installations()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "synced", **result}


class GitHubRepositoryBindingBody(BaseModel):
    enabled: bool = False
    agent_name: str = ""
    trigger_label: str = ""
    mention_triggers: list[str] = Field(default_factory=list)
    notify_platform: str = ""
    notify_external_id: str = ""
    notify_channel_id: str = ""


@router.put("/dashboard-api/github/repositories/{repository_id}/binding")
async def update_dashboard_github_repository_binding(
    repository_id: int,
    body: GitHubRepositoryBindingBody,
) -> dict[str, Any]:
    service = get_github_automation_service()
    try:
        binding = await service.update_repository_binding(
            repository_id,
            enabled=body.enabled,
            agent_name=body.agent_name,
            trigger_label=body.trigger_label,
            mention_triggers=body.mention_triggers,
            notify_platform=body.notify_platform,
            notify_external_id=body.notify_external_id,
            notify_channel_id=body.notify_channel_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "updated", "repository": binding}
