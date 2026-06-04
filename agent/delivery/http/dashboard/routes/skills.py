from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agent.delivery.http.dashboard.routes.shared import _get_config_service
from agent.modules.skills import (
    create_skill,
    delete_skill,
    get_skill,
    list_available_skills,
    read_skill_content,
    reload_skills,
    update_skill,
)
from agent.modules.skills.repository import DEFAULT_SKILLS_ROOT

logger = logging.getLogger(__name__)
router = APIRouter()


class SkillBody(BaseModel):
    name: str
    content: str


def _handle_skill_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("Unexpected skill operation failure.")
    return HTTPException(status_code=500, detail=str(exc))


def _serialize_skill(name: str, *, include_content: bool = False) -> dict[str, Any]:
    skill = get_skill(name)
    if skill is None:
        raise FileNotFoundError(f"Skill '{name}' was not found.")
    payload: dict[str, Any] = {
        "name": skill.name,
        "description": skill.description,
        "path": str(skill.path),
        "skill_file": str(skill.path / "SKILL.md"),
        "license": skill.license,
        "compatibility": skill.compatibility,
        "metadata": dict(skill.metadata),
        "allowed_tools": list(skill.allowed_tools),
        "resources": list(skill.resources),
    }
    if include_content:
        payload["content"] = read_skill_content(name)
    return payload


def _skills_settings(request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    service = _get_config_service(request)
    settings, sources = service.get_settings_overview_and_sources()
    return (
        {key: value for key, value in settings.items() if key.startswith("skills.")},
        {key: value for key, value in sources.items() if key.startswith("skills.")},
    )


@router.get("/dashboard-api/skills")
async def get_dashboard_skills(request: Request) -> dict[str, Any]:
    settings, sources = _skills_settings(request)
    return {
        "skills_root": str(DEFAULT_SKILLS_ROOT),
        "settings": settings,
        "settings_sources": sources,
        "skills": [
            _serialize_skill(summary.name)
            for summary in list_available_skills()
        ],
    }


@router.get("/dashboard-api/skills/{name}")
async def get_dashboard_skill(name: str) -> dict[str, Any]:
    try:
        return {"skill": _serialize_skill(name, include_content=True)}
    except Exception as exc:
        raise _handle_skill_error(exc) from exc


@router.post("/dashboard-api/skills")
async def create_dashboard_skill(body: SkillBody) -> dict[str, Any]:
    try:
        skill = create_skill(body.name, body.content)
        return {"status": "created", "skill": _serialize_skill(skill.name)}
    except Exception as exc:
        raise _handle_skill_error(exc) from exc


@router.put("/dashboard-api/skills/{name}")
async def update_dashboard_skill(name: str, body: SkillBody) -> dict[str, Any]:
    try:
        skill = update_skill(name, body.name, body.content)
        return {"status": "updated", "skill": _serialize_skill(skill.name)}
    except Exception as exc:
        raise _handle_skill_error(exc) from exc


@router.delete("/dashboard-api/skills/{name}")
async def delete_dashboard_skill(name: str) -> dict[str, str]:
    try:
        delete_skill(name)
        return {"status": "deleted", "name": name}
    except Exception as exc:
        raise _handle_skill_error(exc) from exc


@router.post("/dashboard-api/skills/reload")
async def reload_dashboard_skills(request: Request) -> dict[str, Any]:
    reload_skills()
    return {"status": "reloaded", **await get_dashboard_skills(request)}

