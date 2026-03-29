"""Filesystem-based skill repository.

Scans ``~/.kaka-agent/skills/`` for subdirectories containing SKILL.md
files and caches the results.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from agent.modules.skills.domain.skill import Skill, SkillSummary
from agent.modules.skills.infrastructure.parser import parse_skill_md

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_ROOT = Path.home() / ".kaka-agent" / "skills"

# Directories to skip during scanning
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})

# Max scan depth to prevent runaway recursion
_MAX_DEPTH = 1  # Skills are expected at <root>/<skill-name>/SKILL.md


class FilesystemSkillRepository:
    """Discover and load skills from the local filesystem."""

    def __init__(self, skills_root: Path | None = None) -> None:
        self._root = skills_root or DEFAULT_SKILLS_ROOT
        self._cache: dict[str, Skill] | None = None

    @property
    def root(self) -> Path:
        return self._root

    def _ensure_root(self) -> None:
        """Create the skills directory if it doesn't exist."""
        self._root.mkdir(parents=True, exist_ok=True)

    def _scan(self) -> dict[str, Skill]:
        """Walk the skills root and parse all valid SKILL.md files."""
        if self._cache is not None:
            return self._cache

        self._ensure_root()
        skills: dict[str, Skill] = {}

        if not self._root.is_dir():
            self._cache = skills
            return self._cache

        for entry in sorted(self._root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue

            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                logger.debug("Skipping '%s' — no SKILL.md found.", entry.name)
                continue

            try:
                content = skill_md.read_text(encoding="utf-8")
                skill = parse_skill_md(content, entry)
                if skill is not None:
                    # Use dir name as the canonical key for dedup
                    skills[entry.name] = skill
                    logger.info("Discovered skill: '%s' at %s", skill.name, entry)
            except Exception:
                logger.exception("Failed to read skill at %s — skipping.", entry)

        self._cache = skills
        logger.info("Skills discovery complete: %d skill(s) found.", len(skills))
        return self._cache

    # --- SkillRepository protocol ---

    def discover_all(self) -> list[Skill]:
        return list(self._scan().values())

    def load_skill(self, name: str) -> Skill | None:
        return self._scan().get(name)

    def list_summaries(self) -> list[SkillSummary]:
        return [skill.to_summary() for skill in self._scan().values()]

    def reload(self) -> None:
        """Invalidate cache so the next access re-scans the filesystem."""
        self._cache = None
        logger.info("Skills cache invalidated — will re-scan on next access.")

    # --- SkillInstaller protocol ---

    def install(self, source: Path) -> Skill:
        """Copy a skill directory into the managed skills root.

        Raises ``ValueError`` if source doesn't contain a valid SKILL.md.
        """
        self._ensure_root()

        skill_md = source / "SKILL.md"
        if not skill_md.is_file():
            msg = f"Source directory '{source}' does not contain a SKILL.md file."
            raise ValueError(msg)

        content = skill_md.read_text(encoding="utf-8")
        skill = parse_skill_md(content, source)
        if skill is None:
            msg = f"SKILL.md in '{source}' is invalid — cannot install."
            raise ValueError(msg)

        dest = self._root / source.name
        if dest.exists():
            logger.info("Replacing existing skill at %s", dest)
            shutil.rmtree(dest)

        shutil.copytree(source, dest)
        logger.info("Installed skill '%s' to %s", skill.name, dest)

        # Invalidate cache so next access picks up the new skill
        self.reload()

        # Re-parse from installed location
        installed = parse_skill_md(
            (dest / "SKILL.md").read_text(encoding="utf-8"), dest
        )
        if installed is None:
            msg = f"Skill installed but re-parse failed at '{dest}'."
            raise RuntimeError(msg)

        return installed


__all__ = ["DEFAULT_SKILLS_ROOT", "FilesystemSkillRepository"]
