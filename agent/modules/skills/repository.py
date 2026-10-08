"""Filesystem-based skill repository.

Scans ``~/.k41-agent/skills/`` for subdirectories containing SKILL.md
files and caches the results.
"""

from __future__ import annotations

import copy
import logging
import re
import shutil
import tempfile
import threading
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from typing import Any
from weakref import WeakValueDictionary

from agent.modules.skills.models import Skill, SkillSummary
from agent.modules.skills.parser import parse_skill_md
from agent.modules.skills.resources import list_local_resources
from agent.shared.infrastructure.revisions import SKILLS_REVISION, bump_revision, get_revision

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_ROOT = Path.home() / ".k41-agent" / "skills"

# Directories to skip during scanning
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})

_SKILL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_root_locks: WeakValueDictionary[str, Any] = WeakValueDictionary()
_root_locks_guard = threading.Lock()


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
        self._root = (skills_root or DEFAULT_SKILLS_ROOT).expanduser().resolve()
        self._cache: dict[str, Skill] | None = None
        self._fingerprint: tuple | None = None
        self._revision = -1
        with _root_locks_guard:
            key = str(self._root)
            lock = _root_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                _root_locks[key] = lock
            self._lock = lock

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
        if target == root or not target.is_relative_to(root):
            raise ValueError("Skill path escapes the managed skills root.")
        return target

    def _validate_skill_content(self, name: str, content: str, skill_dir: Path) -> Skill:
        # Imported skills may have a directory name different from their identifier.
        skill = parse_skill_md(content, skill_dir, strict=True, expected_name=name)
        assert skill is not None
        return skill

    def _source_fingerprint(self) -> tuple:
        """Observe changes made by other repositories, workers, or editors."""
        entries = []
        for directory in sorted(self._root.iterdir()):
            if directory.name.startswith(".") or directory.name in _SKIP_DIRS:
                continue
            if not directory.is_dir():
                continue
            if not directory.resolve().is_relative_to(self._root):
                continue
            skill_md = directory / "SKILL.md"
            try:
                if not skill_md.resolve().is_relative_to(self._root):
                    continue
                stat = skill_md.stat()
            except FileNotFoundError:
                continue
            entries.append((directory.name, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino))
        return tuple(entries)

    def _scan(self) -> dict[str, Skill]:
        """Walk the skills root and parse all valid SKILL.md files."""
        self._ensure_root()
        fingerprint = self._source_fingerprint()
        revision = get_revision(SKILLS_REVISION)
        if self._cache is not None and self._fingerprint == fingerprint and self._revision == revision:
            return self._cache
        skills: dict[str, Skill] = {}
        failed = False

        if not self._root.is_dir():
            self._cache = skills
            return self._cache

        for entry in sorted(self._root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            if not entry.resolve().is_relative_to(self._root):
                continue

            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                logger.debug("Skipping '%s' — no SKILL.md found.", entry.name)
                continue
            if not skill_md.resolve().is_relative_to(self._root):
                continue

            try:
                content = skill_md.read_text(encoding="utf-8")
                skill = parse_skill_md(content, entry)
                if skill is not None:
                    # Key by the frontmatter `name` (the agentskills.io
                    # canonical identifier) so the LLM catalog and the
                    # `load_skill(name)` lookup agree even when the
                    # directory name differs from the frontmatter name.
                    if skill.name in skills:
                        logger.warning("Duplicate skill '%s' at %s — skipping.", skill.name, entry)
                        continue
                    skills[skill.name] = skill
                    logger.info("Discovered skill: '%s' at %s", skill.name, entry)
            except Exception:
                failed = True
                logger.exception("Failed to read skill at %s — skipping.", entry)

        # Transient read failures must not hide a restored skill indefinitely.
        self._cache = None if failed else skills
        self._fingerprint = fingerprint
        self._revision = revision
        logger.info("Skills discovery complete: %d skill(s) found.", len(skills))
        return skills

    # --- SkillRepository protocol ---

    def discover_all(self) -> list[Skill]:
        with self._lock:
            return [self._with_resources(skill) for skill in self._scan().values()]

    def load_skill(self, name: str) -> Skill | None:
        with self._lock:
            skill = self._scan().get(name)
            return self._with_resources(skill) if skill is not None else None

    def _with_resources(self, skill: Skill) -> Skill:
        return replace(copy.deepcopy(skill), resources=list_local_resources(skill.path))

    def list_summaries(self) -> list[SkillSummary]:
        with self._lock:
            return [skill.to_summary() for skill in self._scan().values()]

    def reload(self) -> None:
        """Invalidate cache so the next access re-scans the filesystem."""
        with self._lock:
            self._cache = None
            self._fingerprint = None
            bump_revision(SKILLS_REVISION)
        logger.info("Skills cache invalidated — will re-scan on next access.")

    # --- Managed SKILL.md CRUD helpers ---

    def _managed_skill_dir(self, name: str) -> Path:
        normalized_name = str(name or "").strip()
        skill = self._scan().get(normalized_name)
        directory = skill.path.resolve() if skill is not None else self._skill_dir(normalized_name)
        if directory == self._root or not directory.is_relative_to(self._root):
            raise ValueError("Skill path escapes the managed skills root.")
        skill_md = directory / "SKILL.md"
        if not skill_md.resolve().is_relative_to(self._root):
            raise ValueError("Skill file escapes the managed skills root.")
        if not skill_md.is_file():
            raise FileNotFoundError(f"Skill '{name}' was not found.")
        if skill is None:
            # Allow repairing invalid files by directory name without taking over
            # a valid skill whose frontmatter declares a different identifier.
            existing = parse_skill_md(skill_md.read_text(encoding="utf-8"), directory)
            if existing is not None:
                raise FileNotFoundError(f"Skill '{name}' was not found.")
        return directory

    def _atomic_write(self, directory: Path, content: str) -> None:
        """Replace SKILL.md only after the complete new file has been written."""
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=directory,
                prefix=".SKILL-", suffix=".tmp", delete=False,
            ) as file:
                temporary = Path(file.name)
                file.write(content)
            temporary.replace(directory / "SKILL.md")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def read_skill_content(self, name: str) -> str:
        with self._lock:
            return (self._managed_skill_dir(name) / "SKILL.md").read_text(encoding="utf-8")

    def create_skill(self, name: str, content: str) -> Skill:
        with self._lock:
            normalized_name = normalize_skill_name(name)
            skill_dir = self._skill_dir(normalized_name)
            if skill_dir.exists() or normalized_name in self._scan():
                raise FileExistsError(f"Skill '{normalized_name}' already exists.")
            clean_content = str(content or "").strip() + "\n"
            skill = self._validate_skill_content(normalized_name, clean_content, skill_dir)
            skill_dir.mkdir(parents=True, exist_ok=False)
            try:
                self._atomic_write(skill_dir, clean_content)
            except Exception:
                skill_dir.rmdir()
                raise
            self.reload()
            return skill

    def update_skill(self, current_name: str, name: str, content: str) -> Skill:
        with self._lock:
            current_normalized = str(current_name or "").strip()
            next_normalized = normalize_skill_name(name)
            current_dir = self._managed_skill_dir(current_normalized)
            next_dir = current_dir if next_normalized == current_normalized else self._skill_dir(next_normalized)
            if next_normalized != current_normalized and (
                (next_dir != current_dir and next_dir.exists())
                or next_normalized in self._scan()
            ):
                raise FileExistsError(f"Skill '{next_normalized}' already exists.")

            clean_content = str(content or "").strip() + "\n"
            skill = self._validate_skill_content(next_normalized, clean_content, next_dir)
            renamed = next_dir != current_dir
            if renamed:
                current_dir.rename(next_dir)
            try:
                self._atomic_write(next_dir, clean_content)
            except Exception:
                if renamed:
                    next_dir.rename(current_dir)
                raise
            self.reload()
            return self._with_resources(skill)

    def delete_skill(self, name: str) -> None:
        with self._lock:
            skill_dir = self._managed_skill_dir(name)
            (skill_dir / "SKILL.md").unlink()
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
        with self._lock:
            source = source.expanduser().resolve()
            skill_md = source / "SKILL.md"
            if not skill_md.is_file():
                raise ValueError(f"Source directory '{source}' does not contain a SKILL.md file.")
            skill = parse_skill_md(skill_md.read_text(encoding="utf-8"), source)
            if skill is None:
                raise ValueError(f"SKILL.md in '{source}' is invalid — cannot install.")
            name = normalize_skill_name(skill.name)
            dest = self._skill_dir(name)
            existing = self._scan().get(name)
            if existing is not None:
                dest = self._managed_skill_dir(name)
            if source == dest or source.is_relative_to(dest) or dest.is_relative_to(source):
                raise ValueError("Source and installed skill directories must not overlap.")
            if existing is None and dest.exists():
                raise FileExistsError(
                    f"Cannot install skill '{name}': destination '{dest}' already exists."
                )

            temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=self._root)).resolve()
            if not temporary.is_relative_to(self._root):
                raise ValueError("Installation staging directory escapes the skills root.")
            staged = temporary / "payload"
            backup = temporary / "backup"
            keep_backup = False
            try:
                shutil.copytree(source, staged)
                installed = self._validate_skill_content(
                    name, (staged / "SKILL.md").read_text(encoding="utf-8"), dest,
                )
                installed = replace(installed, resources=list_local_resources(staged))
                if dest.exists():
                    dest.rename(backup)
                try:
                    staged.rename(dest)
                except Exception:
                    if backup.exists():
                        try:
                            backup.rename(dest)
                        except Exception:
                            keep_backup = True
                            logger.exception("Failed to restore skill; backup preserved at %s", backup)
                    raise
                self.reload()
                logger.info("Installed skill '%s' to %s", installed.name, dest)
                return installed
            finally:
                if not keep_backup:
                    # Staging and backup paths were resolved under the managed root.
                    try:
                        shutil.rmtree(temporary)
                    except OSError:
                        logger.warning("Failed to remove installation staging at %s", temporary, exc_info=True)


__all__ = [
    "DEFAULT_SKILLS_ROOT",
    "FilesystemSkillRepository",
    "normalize_repository_skill_dir",
    "normalize_skill_name",
]
