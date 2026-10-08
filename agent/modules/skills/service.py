"""Skill selection and prompt assembly across global and workspace sources."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent.modules.skills.discovery import (
    discover_repository_skills,
    load_repository_skill_resources,
)
from agent.modules.skills.ports import SkillRepository
from agent.modules.skills.models import SkillSummary
from agent.modules.skills.parser import parse_skill_md
from agent.modules.skills.rendering import skill_catalog_xml, skill_content_xml
from agent.modules.skills.sources import is_enabled, repository_roots


def allowed_names(names: Sequence[str] | None) -> set[str] | None:
    """Normalize the selection of global skills; local skills remain available."""
    return None if names is None else {str(name).strip() for name in names if str(name).strip()}


class SkillsService:
    """Combine allowed global skills with repository-local overrides."""

    def __init__(self, repository: SkillRepository) -> None:
        from agent.modules.skills.packages import SkillPackages

        self.repository = repository
        self.packages = SkillPackages(repository)

    async def _local(self, workspace, repository_dir, thread_id):
        skills = {}
        for directory in reversed(repository_roots(repository_dir)):
            discovered = await discover_repository_skills(workspace=workspace, repository_dir=directory, thread_id=thread_id)
            for name, skill in discovered.items():
                from agent.modules.workspaces import resolve_workspace_ref
                ref = resolve_workspace_ref(workspace)
                root = ref.locator if ref.backend == "local" else str(ref.metadata.get("root") or "")
                if is_enabled("project", root, skill.path.as_posix(), ref.model_dump()):
                    skills[name] = skill
        return skills

    async def catalog_xml(
        self, *, names: Sequence[str] | None, workspace: Any,
        repository_dir: str, thread_id: str | None,
    ) -> str:
        allowed = allowed_names(names)
        skills = {}
        if allowed is None or allowed:
            for package in await self.packages.inventory(scope="global"):
                if package.enabled and not package.shadowed and (allowed is None or package.name in allowed):
                    skills[package.name] = SkillSummary(package.name, package.description,
                        Path(package.source.root) / package.source.directory)
        local = await self._local(workspace, repository_dir, thread_id)
        skills.update({name: skill.to_summary() for name, skill in local.items()})
        return skill_catalog_xml([skills[name] for name in sorted(skills)])

    async def content_xml(
        self, name: str, *, names: Sequence[str] | None, workspace: Any,
        repository_dir: str, thread_id: str | None,
    ) -> str | None:
        name = str(name or "").strip()
        if not name:
            return None
        local = await self._local(workspace, repository_dir, thread_id)
        skill = local.get(name)
        if skill is not None:
            skill = await load_repository_skill_resources(
                skill, workspace=workspace, thread_id=thread_id,
            )
            return skill_content_xml(skill)
        allowed = allowed_names(names)
        if allowed is not None and name not in allowed:
            return None
        for package in await self.packages.inventory(scope="global"):
            if package.name != name or not package.enabled or package.shadowed:
                continue
            transport = self.packages.transport(package, thread_id)
            raw, _ = await transport.read(self.packages.resource_path(package, "SKILL.md"))
            resources = (await transport.operation("resources", package.source.directory))["resources"]
            skill = parse_skill_md(raw.decode("utf-8-sig"), Path(package.source.root) / package.source.directory,
                                  resources=resources)
            return skill_content_xml(skill) if skill is not None else None
        return None
