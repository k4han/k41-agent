"""Skill selection and prompt assembly across global and workspace sources."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent.modules.skills.discovery import (
    discover_repository_skills,
    load_repository_skill_resources,
)
from agent.modules.skills.ports import SkillRepository
from agent.modules.skills.rendering import skill_catalog_xml, skill_content_xml


def allowed_names(names: Sequence[str] | None) -> set[str] | None:
    """Normalize the selection of global skills; local skills remain available."""
    return None if names is None else {str(name).strip() for name in names if str(name).strip()}


class SkillsService:
    """Combine allowed global skills with repository-local overrides."""

    def __init__(self, repository: SkillRepository) -> None:
        self.repository = repository

    async def catalog_xml(
        self, *, names: Sequence[str] | None, workspace: Any,
        repository_dir: str, thread_id: str | None,
    ) -> str:
        allowed = allowed_names(names)
        skills = {
            skill.name: skill for skill in self.repository.list_summaries()
            if allowed is None or skill.name in allowed
        }
        local = await discover_repository_skills(
            workspace=workspace, repository_dir=repository_dir, thread_id=thread_id,
        )
        skills.update({name: skill.to_summary() for name, skill in local.items()})
        return skill_catalog_xml([skills[name] for name in sorted(skills)])

    async def content_xml(
        self, name: str, *, names: Sequence[str] | None, workspace: Any,
        repository_dir: str, thread_id: str | None,
    ) -> str | None:
        name = str(name or "").strip()
        if not name:
            return None
        local = await discover_repository_skills(
            workspace=workspace, repository_dir=repository_dir, thread_id=thread_id,
        )
        skill = local.get(name)
        if skill is not None:
            skill = await load_repository_skill_resources(
                skill, workspace=workspace, thread_id=thread_id,
            )
            return skill_content_xml(skill)
        allowed = allowed_names(names)
        if allowed is not None and name not in allowed:
            return None
        skill = self.repository.load_skill(name)
        return None if skill is None else skill_content_xml(skill)
