"""Use case: install a skill from a local directory."""

from __future__ import annotations

from pathlib import Path

from agent.modules.skills.domain.ports import SkillInstaller
from agent.modules.skills.domain.skill import Skill


def install_skill_from_path(installer: SkillInstaller, source: Path) -> Skill:
    """Validate and install a skill directory into the managed skills root.

    Parameters
    ----------
    installer:
        The backend that copies and persists the skill.
    source:
        Path to the skill directory containing a valid SKILL.md.

    Returns
    -------
    Skill
        The installed and re-parsed skill.

    Raises
    ------
    ValueError
        If *source* does not contain a valid SKILL.md.
    """
    return installer.install(source)


__all__ = ["install_skill_from_path"]
