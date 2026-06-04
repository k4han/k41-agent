"""Filesystem-based skill repository.

Scans ``~/.kaka-agent/skills/`` for subdirectories containing SKILL.md
files and caches the results.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path, PureWindowsPath

from agent.modules.skills.models import Skill, SkillSummary
from agent.modules.skills.parser import parse_skill_md

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_ROOT = Path.home() / ".kaka-agent" / "skills"

# Directories to skip during scanning
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})

# Max scan depth to prevent runaway recursion
_MAX_DEPTH = 1  # Skills are expected at <root>/<skill-name>/SKILL.md
_SKILL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def normalize_skill_name(value: str) -> str:
    """Validate and return a canonical skill directory name."""
    name = str(value or "").strip()
    if not name:
        raise ValueError("Skill name is required.")
    if len(name) > 64:
        raise ValueError("Skill name must be 64 characters or fewer.")
    if "--" in name or not _SKILL_NAME_RE.match(name):
        raise ValueError(
            "Skill name must use lowercase letters, numbers, and single hyphens."
        )
    if any(separator in name for separator in ("/", "\\")):
        raise ValueError("Skill name must not contain path separators.")
    return name


def normalize_repository_skill_dir(value: str | None) -> str:
    """Validate the repository-relative directory used for repo-local skills."""
    raw = str(value or "").strip() or ".agent/skills"
    if "\\" in raw:
        raise ValueError("Repository skill directory must use '/' separators.")
    if raw.startswith("/") or Path(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
        raise ValueError("Repository skill directory must be relative.")
    normalized = raw.strip("/")
    if not normalized:
        raise ValueError("Repository skill directory cannot be empty.")
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError(
            "Repository skill directory must not contain '.', '..', or empty segments."
        )
    return normalized


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

    def _resolved_root(self) -> Path:
        self._ensure_root()
        return self._root.expanduser().resolve()

    def _skill_dir(self, name: str) -> Path:
        root = self._resolved_root()
        normalized_name = normalize_skill_name(name)
        target = (root / normalized_name).resolve()
        if target != root and not target.is_relative_to(root):
            raise ValueError("Skill path escapes the managed skills root.")
        return target

    def _validate_skill_content(self, name: str, content: str, skill_dir: Path) -> Skill:
        skill = parse_skill_md(content, skill_dir, strict=True)
        if skill.name != name:
            raise ValueError("SKILL.md frontmatter name must match the skill name.")
        return skill

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
                    # Key by the frontmatter `name` (the agentskills.io
                    # canonical identifier) so the LLM catalog and the
                    # `load_skill(name)` lookup agree even when the
                    # directory name differs from the frontmatter name.
                    skills[skill.name] = skill
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

    # --- Managed SKILL.md CRUD helpers ---

    def read_skill_content(self, name: str) -> str:
        skill_md = self._skill_dir(name) / "SKILL.md"
        if not skill_md.is_file():
            raise FileNotFoundError(f"Skill '{name}' was not found.")
        return skill_md.read_text(encoding="utf-8")

    def create_skill(self, name: str, content: str) -> Skill:
        normalized_name = normalize_skill_name(name)
        skill_dir = self._skill_dir(normalized_name)
        if skill_dir.exists():
            raise FileExistsError(f"Skill '{normalized_name}' already exists.")
        clean_content = str(content or "").strip() + "\n"
        skill = self._validate_skill_content(normalized_name, clean_content, skill_dir)
        skill_dir.mkdir(parents=True, exist_ok=False)
        (skill_dir / "SKILL.md").write_text(clean_content, encoding="utf-8")
        self.reload()
        return skill

    def update_skill(self, current_name: str, name: str, content: str) -> Skill:
        current_normalized = normalize_skill_name(current_name)
        next_normalized = normalize_skill_name(name)
        current_dir = self._skill_dir(current_normalized)
        if not (current_dir / "SKILL.md").is_file():
            raise FileNotFoundError(f"Skill '{current_normalized}' was not found.")

        next_dir = self._skill_dir(next_normalized)
        if next_normalized != current_normalized and next_dir.exists():
            raise FileExistsError(f"Skill '{next_normalized}' already exists.")

        clean_content = str(content or "").strip() + "\n"
        skill = self._validate_skill_content(next_normalized, clean_content, next_dir)

        if next_normalized != current_normalized:
            shutil.move(str(current_dir), str(next_dir))

        (next_dir / "SKILL.md").write_text(clean_content, encoding="utf-8")
        self.reload()
        return skill

    def delete_skill(self, name: str) -> None:
        skill_dir = self._skill_dir(name)
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            raise FileNotFoundError(f"Skill '{name}' was not found.")
        skill_md.unlink()
        try:
            skill_dir.rmdir()
        except OSError:
            pass
        self.reload()

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


__all__ = [
    "DEFAULT_SKILLS_ROOT",
    "FilesystemSkillRepository",
    "normalize_repository_skill_dir",
    "normalize_skill_name",
]
