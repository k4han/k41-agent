from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from agent.modules.agents import get_catalog_service
from agent.modules.agent_runtime import get_background_task_manager
from agent.delivery.http.dashboard.routes.shared import (
    _agent_card_options,
    _paired_identities,
)
from agent.modules.github import (
    get_github_automation_service,
    get_github_settings,
)
from agent.modules.skills import get_repository_skill_dir, list_available_skills


router = APIRouter()
ACTIVE_TASK_STATUSES = {"pending", "running"}


def _repository_activity(
    repository: dict[str, Any],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    full_name = str(repository.get("full_name") or "")
    if not full_name:
        return {"active_count": 0, "recent_count": 0, "tasks": []}

    manager = get_background_task_manager()
    matches = [
        task
        for task in manager.list_all()
        if _task_repository_full_name(task) == full_name
    ]
    return {
        "active_count": sum(
            1 for task in matches if str(task.get("status") or "") in ACTIVE_TASK_STATUSES
        ),
        "recent_count": len(matches),
        "tasks": matches[:limit],
    }


def _repository_activity_map(repositories: list[dict[str, Any]]) -> dict[str, Any]:
    activity: dict[str, Any] = {}
    for repository in repositories:
        repository_id = repository.get("repository_id")
        if repository_id is None:
            continue
        activity[str(repository_id)] = _repository_activity(repository, limit=3)
    return activity


def _task_repository_full_name(task: dict[str, Any]) -> str:
    workspace = task.get("workspace") if isinstance(task, dict) else None
    if not isinstance(workspace, dict):
        return ""
    metadata = workspace.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("repository_full_name") or metadata.get("repository")
        if value:
            return str(value)
    label = str(workspace.get("label") or "").strip()
    if "/" in label and "\\" not in label:
        return label
    return ""


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
    repositories = await service.list_repository_bindings()
    return {
        "configured": settings.is_configured,
        "enabled": settings.enabled,
        "app_slug": settings.app_slug,
        "webhook_url": webhook_url,
        "install_url": install_url,
        "default_agent": settings.default_agent,
        "trigger_label": settings.trigger_label,
        "mention_triggers": list(settings.mention_triggers),
        "repositories": repositories,
        "repository_activity": _repository_activity_map(repositories),
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
    issue_label_enabled: bool = True
    issue_comment_enabled: bool = True
    pr_review_comment_enabled: bool = True
    repository_instructions: str = ""
    provider_name: str = ""
    model_name: str = ""
    context_trim_threshold: int | None = None
    tool_policy_mode: str = "inherit"
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_skills: list[str] = Field(default_factory=list)
    branch_prefix: str = "k41"
    workspace_backend: str = "local"


class SubmitGitHubRepositoryTaskBody(BaseModel):
    request: str
    notify_platform: str = ""
    notify_external_id: str = ""
    notify_channel_id: str = ""


@router.get("/dashboard-api/github/repositories/{repository_id}")
async def get_dashboard_github_repository(repository_id: int) -> dict[str, Any]:
    service = get_github_automation_service()
    try:
        repository = await service.get_repository_binding(repository_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    options = await _agent_card_options()
    return {
        "repository": repository,
        "activity": _repository_activity(repository, limit=10),
        "identities": await _paired_identities(),
        "agent_names": options["agent_names"],
        "tools": options["tools"],
        "tool_groups": options["tool_groups"],
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "path": str(skill.path),
            }
            for skill in list_available_skills()
        ],
        "repository_skill_dir": get_repository_skill_dir(),
        "provider_names": options["provider_names"],
        "default_provider": options["default_provider"],
        "default_model": options["default_model"],
        "model_catalogs": options["model_catalogs"],
        "model_catalog_error": options["model_catalog_error"],
    }


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
            issue_label_enabled=body.issue_label_enabled,
            issue_comment_enabled=body.issue_comment_enabled,
            pr_review_comment_enabled=body.pr_review_comment_enabled,
            repository_instructions=body.repository_instructions,
            provider_name=body.provider_name,
            model_name=body.model_name,
            context_trim_threshold=body.context_trim_threshold,
            tool_policy_mode=body.tool_policy_mode,
            allowed_tools=body.allowed_tools,
            allowed_skills=body.allowed_skills,
            branch_prefix=body.branch_prefix,
            workspace_backend=body.workspace_backend,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "updated", "repository": binding}


@router.post("/dashboard-api/github/repositories/{repository_id}/tasks")
async def submit_dashboard_github_repository_task(
    repository_id: int,
    body: SubmitGitHubRepositoryTaskBody,
) -> dict[str, Any]:
    service = get_github_automation_service()
    try:
        task_id = await service.submit_repository_task(
            repository_id,
            request=body.request,
            notify_platform=body.notify_platform,
            notify_external_id=body.notify_external_id,
            notify_channel_id=body.notify_channel_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "submitted", "task_id": task_id}
