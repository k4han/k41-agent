"""Public interface for the skills module.

Other modules should import from here, not from internal packages.
"""

from __future__ import annotations

import logging
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from agent.modules.skills.application.install_skill import install_skill_from_path
from agent.modules.skills.application.list_skills import list_skills
from agent.modules.skills.application.load_skill import load_skill
from agent.modules.skills.domain.skill import Skill, SkillSummary
from agent.modules.skills.infrastructure.filesystem_repository import (
    FilesystemSkillRepository,
)

logger = logging.getLogger(__name__)

# --- Module-level singleton ---

_repository: FilesystemSkillRepository | None = None


def _get_repository() -> FilesystemSkillRepository:
    global _repository
    if _repository is None:
        _repository = FilesystemSkillRepository()
    return _repository


# --- Public functions ---


def list_available_skills() -> list[SkillSummary]:
    """Return lightweight summaries of all discovered skills."""
    return list_skills(_get_repository())


def get_skill(name: str) -> Skill | None:
    """Load the full content of a skill by name."""
    return load_skill(_get_repository(), name)


def get_skills_catalog_xml() -> str:
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
    summaries = list_available_skills()
    if not summaries:
        return "<available_skills/>"

    lines = ["<available_skills>"]
    for s in summaries:
        skill_md_path = s.path / "SKILL.md"
        lines.append("  <skill>")
        lines.append(f"    <name>{xml_escape(s.name)}</name>")
        lines.append(f"    <description>{xml_escape(s.description)}</description>")
        lines.append(f"    <location>{xml_escape(str(skill_md_path))}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


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


def install_skill(source: Path) -> Skill:
    """Install a skill from a local directory path."""
    repo = _get_repository()
    return install_skill_from_path(repo, source)


def reload_skills() -> None:
    """Re-scan the filesystem for skills."""
    _get_repository().reload()
    # Trigger a fresh scan immediately
    count = len(_get_repository().discover_all())
    logger.info("Skills reloaded: %d skill(s) available.", count)


__all__ = [
    "Skill",
    "SkillSummary",
    "get_skill",
    "get_skill_content_xml",
    "get_skills_catalog_xml",
    "install_skill",
    "list_available_skills",
    "reload_skills",
]
