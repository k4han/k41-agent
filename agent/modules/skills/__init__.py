"""Public interface for the skills module.

Other modules should import from here, not from internal packages.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from collections.abc import Sequence
from typing import Any
from xml.sax.saxutils import escape as xml_escape

logger = logging.getLogger(__name__)

_repository = None

# TTL cache for repository-local skill discovery. The same workspace
# is hit many times per agent run (once for the catalog at llm_node,
# then once per ``skill`` tool invocation); caching avoids an N+1
# round-trip to the workspace backend.
_REPOSITORY_DISCOVERY_TTL_SECONDS = 5.0
_repository_discovery_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
_repository_discovery_lock = threading.Lock()


def _get_repository():
    global _repository
    if _repository is None:
        from agent.modules.skills.repository import FilesystemSkillRepository
        _repository = FilesystemSkillRepository()
    return _repository


def list_available_skills():
    """Return lightweight summaries of all discovered skills."""
    from agent.modules.skills.list import list_skills
    return list_skills(_get_repository())


def get_skill(name: str):
    """Load the full content of a skill by name."""
    from agent.modules.skills.load import load_skill
    return load_skill(_get_repository(), name)


def _allowed_names(allowed_names: Sequence[str] | None) -> set[str] | None:
    if allowed_names is None:
        return None
    return {str(name).strip() for name in allowed_names if str(name).strip()}


def _skill_catalog_xml(skills: Sequence[Any]) -> str:
    if not skills:
        return "<available_skills/>"

    lines = ["<available_skills>"]
    for skill in skills:
        skill_md_path = skill.path / "SKILL.md"
        lines.append("  <skill>")
        lines.append(f"    <name>{xml_escape(skill.name)}</name>")
        lines.append(f"    <description>{xml_escape(skill.description)}</description>")
        lines.append(f"    <location>{xml_escape(str(skill_md_path))}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def _skill_content_xml(skill: Any) -> str:
    lines = [f'<skill_content name="{xml_escape(skill.name)}">']
    lines.append(skill.body)
    lines.append("")
    lines.append(f"Skill directory: {skill.path}")
    lines.append("Relative paths in this skill are relative to the skill directory.")

    if skill.resources:
        lines.append("<skill_resources>")
        for res in skill.resources:
            lines.append(f"  <file>{xml_escape(res)}</file>")
        lines.append("</skill_resources>")

    lines.append("</skill_content>")
    return "\n".join(lines)


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
    allowed = _allowed_names(allowed_names)
    summaries = [
        summary
        for summary in list_available_skills()
        if allowed is None or summary.name in allowed
    ]
    return _skill_catalog_xml(summaries)


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

    return _skill_content_xml(skill)


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


def get_repository_skill_dir() -> str:
    """Return the configured repository-relative skill directory."""
    from agent.modules.skills.repository import normalize_repository_skill_dir
    from agent.shared.config.service import get_config_service

    return normalize_repository_skill_dir(
        get_config_service().get_str("skills.repository_dir", ".agent/skills")
    )


async def _discover_repository_skills(
    *,
    workspace: Any,
    repository_dir: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Discover repo-local skills, cached per (workspace, repo dir, thread)."""
    if workspace is None:
        return {}

    try:
        from agent.modules.skills.repository import normalize_repository_skill_dir
        skill_dir = normalize_repository_skill_dir(
            repository_dir or get_repository_skill_dir()
        )
    except ValueError as exc:
        logger.debug("Invalid repository skill dir: %s", exc)
        return {}

    workspace_key = (
        getattr(workspace, "locator", None) or str(workspace) or id(workspace)
    )
    cache_key = (str(workspace_key), skill_dir, str(thread_id or ""))
    now = time.monotonic()

    with _repository_discovery_lock:
        cached = _repository_discovery_cache.get(cache_key)
        if cached is not None and (now - cached[0]) < _REPOSITORY_DISCOVERY_TTL_SECONDS:
            return dict(cached[1])

    from agent.modules.skills.parser import parse_skill_md
    from agent.modules.skills.repository import normalize_skill_name
    from agent.modules.workspaces import get_workspace_browser, get_workspace_file_io

    try:
        browser = await get_workspace_browser(workspace, thread_id=thread_id)
        tree = await browser.tree(skill_dir)
        file_io = await get_workspace_file_io(workspace, thread_id=thread_id)
    except Exception as exc:
        logger.debug("Failed to inspect repository-local skills: %s", exc)
        return {}

    skills: dict[str, Any] = {}
    for entry in tree.get("entries", []):
        if not isinstance(entry, dict) or entry.get("kind") != "directory":
            continue
        try:
            dir_name = normalize_skill_name(str(entry.get("name") or ""))
        except ValueError:
            continue

        relative_skill_path = f"{skill_dir}/{dir_name}/SKILL.md"
        try:
            content = await file_io.read_text(relative_skill_path)
            skill = parse_skill_md(content, Path(skill_dir) / dir_name)
        except Exception as exc:
            logger.debug(
                "Failed to load repository-local skill '%s': %s",
                dir_name,
                exc,
            )
            continue
        if skill is not None:
            skills[skill.name] = skill

    with _repository_discovery_lock:
        _repository_discovery_cache[cache_key] = (now, dict(skills))
    return skills


def reload_repository_skills() -> None:
    """Invalidate the repository-local skills discovery cache."""
    with _repository_discovery_lock:
        _repository_discovery_cache.clear()


async def get_effective_skills_catalog_xml(
    *,
    allowed_names: Sequence[str] | None = None,
    workspace: Any = None,
    repository_dir: str | None = None,
    thread_id: str | None = None,
) -> str:
    """Build a skill catalog from allowed globals plus repo-local overrides."""
    allowed = _allowed_names(allowed_names)
    skills_by_name: dict[str, Any] = {
        skill.name: skill
        for skill in list_available_skills()
        if allowed is None or skill.name in allowed
    }
    skills_by_name.update(
        await _discover_repository_skills(
            workspace=workspace,
            repository_dir=repository_dir,
            thread_id=thread_id,
        )
    )
    return _skill_catalog_xml(
        [skills_by_name[name] for name in sorted(skills_by_name)]
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
    normalized_name = str(name or "").strip()
    if not normalized_name:
        return None

    repository_skills = await _discover_repository_skills(
        workspace=workspace,
        repository_dir=repository_dir,
        thread_id=thread_id,
    )
    local_skill = repository_skills.get(normalized_name)
    if local_skill is not None:
        return _skill_content_xml(local_skill)

    allowed = _allowed_names(allowed_names)
    if allowed is not None and normalized_name not in allowed:
        return None
    return get_skill_content_xml(normalized_name)


def install_skill(source: Path):
    """Install a skill from a local directory path."""
    from agent.modules.skills.install import install_skill_from_path
    repo = _get_repository()
    return install_skill_from_path(repo, source)


def reload_skills() -> None:
    """Re-scan the filesystem for skills."""
    _get_repository().reload()
    reload_repository_skills()
    logger.info("Skills reloaded.")


def __getattr__(name: str):
    if name == "Skill":
        from agent.modules.skills.models import Skill
        return Skill
    if name == "SkillSummary":
        from agent.modules.skills.models import SkillSummary
        return SkillSummary
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Skill",
    "SkillSummary",
    "create_skill",
    "delete_skill",
    "get_effective_skill_content_xml",
    "get_effective_skills_catalog_xml",
    "get_repository_skill_dir",
    "get_skill",
    "get_skill_content_xml",
    "get_skills_catalog_xml",
    "install_skill",
    "list_available_skills",
    "read_skill_content",
    "reload_repository_skills",
    "reload_skills",
    "update_skill",
]
