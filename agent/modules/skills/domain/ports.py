"""Port definitions (interfaces) for the skills module."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agent.modules.skills.domain.skill import Skill, SkillSummary


class SkillRepository(Protocol):
    """Read-only source of skills from the filesystem or other backends."""

    def discover_all(self) -> list[Skill]:
        """Scan and return all valid skills."""
        ...

    def load_skill(self, name: str) -> Skill | None:
        """Load a single skill by name, or None if not found."""
        ...

    def list_summaries(self) -> list[SkillSummary]:
        """Return lightweight summaries for progressive disclosure."""
        ...

    def reload(self) -> None:
        """Invalidate caches and re-scan."""
        ...


class SkillInstaller(Protocol):
    """Persist a skill directory to the skills root."""

    def install(self, source: Path) -> Skill:
        """Copy *source* directory into the managed skills root.

        Raises ``ValueError`` if the source does not contain a valid SKILL.md.
        """
        ...


__all__ = ["SkillInstaller", "SkillRepository"]
