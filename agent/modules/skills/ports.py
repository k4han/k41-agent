"""Port definitions (interfaces) for the skills module."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agent.modules.skills.models import Skill, SkillSummary


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


class SkillPackageIO(Protocol):
    async def operation(self, op: str, path: str = "", **kwargs) -> dict:
        ...

    async def archive(self, path: str) -> tuple[bytes, str]:
        ...

    async def read(self, path: str) -> tuple[bytes, dict]:
        ...

    async def write(self, path: str, data: bytes, *, expected_version: str | None = None, mode: int = 0o644) -> dict:
        ...

    async def install(self, packages: list[tuple[str, bytes]], *, overwrite=False, reuse=False, expected_versions=None, manifest=None) -> dict:
        ...


__all__ = ["SkillInstaller", "SkillRepository", "SkillPackageIO"]
