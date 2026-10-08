"""Public interface for the skills module.

Other modules should import from here, not from internal packages.
"""

from __future__ import annotations

import logging
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from agent.modules.skills.discovery import clear_discovery_cache
from agent.modules.skills.rendering import skill_catalog_xml, skill_content_xml
from agent.modules.skills.service import SkillsService, allowed_names as normalize_allowed_names
from agent.modules.skills.repository import (
    DEFAULT_SKILLS_ROOT,
    FilesystemSkillRepository,
    normalize_repository_skill_dir,
)

logger = logging.getLogger(__name__)


def _get_repository(container=None):
    """Return container-scoped skill repository."""
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).skill_repository


def list_available_skills():
    """Return lightweight summaries of all discovered skills."""
    from agent.modules.skills.list import list_skills
    return list_skills(_get_repository())


def get_skill(name: str):
    """Load the full content of a skill by name."""
    from agent.modules.skills.load import load_skill
    return load_skill(_get_repository(), name)


def get_skills_catalog_xml(allowed_names: Sequence[str] | None = None) -> str:
    """Build an XML catalog of available skills for LLM prompt injection.

    Format follows the agentskills.io recommendation::

        <available_skills>
          <skill>
            <name>...</name>
            <description>...</description>
            <location>...</location>
          </skill>
        </available_skills>

    Returns an empty ``<available_skills/>`` element if no skills exist.
    """
    allowed = normalize_allowed_names(allowed_names)
    summaries = [
        summary
        for summary in list_available_skills()
        if allowed is None or summary.name in allowed
    ]
    return skill_catalog_xml(summaries)


def get_skill_content_xml(name: str) -> str | None:
    """Build structured XML wrapping for a skill's full content.

    Returns ``None`` if the skill is not found.  Format::

        <skill_content name="...">
          [SKILL.md body]

          Skill directory: /path/to/skill
          ...
          <skill_resources>
            <file>scripts/foo.py</file>
          </skill_resources>
        </skill_content>
    """
    skill = get_skill(name)
    if skill is None:
        return None

    return skill_content_xml(skill)


def read_skill_content(name: str) -> str:
    """Read the raw managed SKILL.md content for a global skill."""
    return _get_repository().read_skill_content(name)


def create_skill(name: str, content: str):
    """Create a managed global skill."""
    return _get_repository().create_skill(name, content)


def update_skill(current_name: str, name: str, content: str):
    """Update or rename a managed global skill."""
    return _get_repository().update_skill(current_name, name, content)


def delete_skill(name: str) -> None:
    """Delete a managed global skill file."""
    _get_repository().delete_skill(name)


def _configured_repository_skill_dir() -> str:
    from agent.shared.config.service import get_config_service

    return get_config_service().get_str("skills.repository_dir", ".agent/skills")


def get_repository_skill_dir() -> str:
    """Return the configured repository-relative skill directory."""
    return normalize_repository_skill_dir(_configured_repository_skill_dir())


def reload_repository_skills() -> None:
    """Invalidate the repository-local skills discovery cache."""
    from agent.shared.infrastructure.revisions import SKILLS_REVISION, bump_revision

    clear_discovery_cache()
    bump_revision(SKILLS_REVISION)


def is_repository_skill_path(
    file_path: str,
    *,
    repository_dir: str | None = None,
) -> bool:
    """Return whether ``file_path`` points inside the repo-local skills directory.

    Accepts both workspace-relative and absolute paths: the configured skill
    directory is matched as a contiguous run of path segments anywhere in
    ``file_path``.
    """
    raw = str(file_path or "").strip()
    if not raw:
        return False

    try:
        skill_dir = normalize_repository_skill_dir(
            repository_dir or get_repository_skill_dir()
        )
    except ValueError as exc:
        logger.debug("Invalid repository skill dir: %s", exc)
        return False

    parts = [
        part
        for part in raw.replace("\\", "/").split("/")
        if part not in ("", ".")
    ]
    from agent.modules.skills.sources import repository_roots
    return any(
        parts[index : index + len(expected)] == expected
        for directory in repository_roots(skill_dir)
        for expected in [directory.split("/")]
        for index in range(len(parts) - len(expected) + 1)
    )


def invalidate_repository_skills_for_path(
    file_path: str,
    *,
    repository_dir: str | None = None,
) -> bool:
    """Refresh repo-local skills when a write touches the skills directory.

    Lets an agent use a skill it just wrote without waiting for the discovery
    TTL or the system prompt cache TTL to lapse. Returns whether the path was
    relevant.
    """
    if not is_repository_skill_path(file_path, repository_dir=repository_dir):
        return False
    reload_repository_skills()
    return True


async def get_effective_skills_catalog_xml(
    *,
    allowed_names: Sequence[str] | None = None,
    workspace: Any = None,
    repository_dir: str | None = None,
    thread_id: str | None = None,
) -> str:
    """Build a skill catalog from allowed globals plus repo-local overrides."""
    return await SkillsService(_get_repository()).catalog_xml(
        names=allowed_names,
        workspace=workspace,
        repository_dir=repository_dir or _configured_repository_skill_dir(),
        thread_id=thread_id,
    )


async def get_effective_skill_content_xml(
    name: str,
    *,
    allowed_names: Sequence[str] | None = None,
    workspace: Any = None,
    repository_dir: str | None = None,
    thread_id: str | None = None,
) -> str | None:
    """Load a skill from repo-local skills first, then allowed global skills."""
    return await SkillsService(_get_repository()).content_xml(
        name,
        names=allowed_names,
        workspace=workspace,
        repository_dir=repository_dir or _configured_repository_skill_dir(),
        thread_id=thread_id,
    )


def install_skill(source: Path):
    """Install a skill from a local directory path."""
    from agent.modules.skills.install import install_skill_from_path
    repo = _get_repository()
    return install_skill_from_path(repo, source)


def reload_skills() -> None:
    """Re-scan the filesystem for skills."""
    # FilesystemSkillRepository.reload() already bumps SKILLS_REVISION, so the
    # discovery cache is cleared directly to avoid a redundant second bump.
    _get_repository().reload()
    clear_discovery_cache()
    logger.info("Skills reloaded.")


def get_skill_packages():
    """Resolve the container-owned package lifecycle service."""
    from agent.modules.skills.packages import get_skill_packages as resolve
    return resolve()


def validate_skill_document(content: str, *, name: str | None = None, strict: bool = False):
    from agent.modules.skills.packages import validate_document
    return validate_document(content, name=name, strict=strict)


def __getattr__(name: str):
    if name == "SkillPackages":
        from agent.modules.skills.packages import SkillPackages
        return SkillPackages
    if name == "WorkspacePackageIO":
        from agent.modules.skills.package_io import WorkspacePackageIO
        return WorkspacePackageIO
    if name in {"activation_key", "active_context", "model_skill_history", "skill_commands", "skill_events"}:
        from agent.modules.skills import context
        return getattr(context, name)
    if name in {"SkillSource", "SkillPackageSummary", "SkillResource", "SkillDiagnostic", "ActivatedSkill"}:
        from agent.modules.skills import models
        return getattr(models, name)
    if name == "Skill":
        from agent.modules.skills.models import Skill
        return Skill
    if name == "SkillSummary":
        from agent.modules.skills.models import SkillSummary
        return SkillSummary
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "SkillPackages", "WorkspacePackageIO", "activation_key", "active_context", "model_skill_history", "skill_commands", "skill_events",
    "SkillSource", "SkillPackageSummary", "SkillResource", "SkillDiagnostic", "ActivatedSkill",
    "get_skill_packages", "validate_skill_document",
    "Skill",
    "SkillSummary",
    "DEFAULT_SKILLS_ROOT",
    "FilesystemSkillRepository",
    "create_skill",
    "delete_skill",
    "get_effective_skill_content_xml",
    "get_effective_skills_catalog_xml",
    "get_repository_skill_dir",
    "get_skill",
    "get_skill_content_xml",
    "get_skills_catalog_xml",
    "install_skill",
    "invalidate_repository_skills_for_path",
    "is_repository_skill_path",
    "list_available_skills",
    "read_skill_content",
    "reload_repository_skills",
    "reload_skills",
    "normalize_repository_skill_dir",
    "update_skill",
]
