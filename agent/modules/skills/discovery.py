"""Bounded, generation-aware discovery of repository-local skills."""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent.modules.skills.models import Skill
from agent.modules.skills.parser import parse_skill_md
from agent.modules.skills.repository import normalize_repository_skill_dir, normalize_skill_name
from agent.modules.skills.resources import list_workspace_resources

logger = logging.getLogger(__name__)
DISCOVERY_TTL_SECONDS = 5.0
MAX_DISCOVERY_CACHE_ENTRIES = 128

_entries: OrderedDict[tuple[str, ...], tuple[float, dict[str, Skill]]] = OrderedDict()
_lock = threading.Lock()
_generation = 0


def clear_discovery_cache() -> None:
    """Invalidate completed and in-flight discovery results."""
    global _generation
    with _lock:
        _generation += 1
        _entries.clear()


async def discover_repository_skills(
    *,
    workspace: Any,
    repository_dir: str,
    thread_id: str | None = None,
) -> dict[str, Skill]:
    """Read metadata and instructions without scanning bundled resources."""
    if workspace is None:
        return {}

    from agent.modules.workspaces import (
        get_workspace_browser,
        get_workspace_file_io,
        resolve_workspace_ref,
    )

    try:
        skill_dir = normalize_repository_skill_dir(repository_dir)
        ref = resolve_workspace_ref(workspace)
    except ValueError as exc:
        logger.debug("Invalid repository skill workspace: %s", exc)
        return {}
    cache_key = (
        ref.backend,
        ref.locator,
        json.dumps(ref.metadata, sort_keys=True, default=str),
        skill_dir,
        str(thread_id or ""),
    )

    while True:
        now = time.monotonic()
        with _lock:
            generation = _generation
            cached = _entries.get(cache_key)
            if cached is not None:
                if now < cached[0]:
                    _entries.move_to_end(cache_key)
                    return copy.deepcopy(cached[1])
                del _entries[cache_key]

        try:
            browser = await get_workspace_browser(ref, thread_id=thread_id)
            tree = await browser.tree(skill_dir)
            file_io = await get_workspace_file_io(ref, thread_id=thread_id)
        except Exception as exc:
            logger.debug("Failed to inspect repository-local skills: %s", exc)
            return {}

        skills: dict[str, Skill] = {}
        for entry in tree.get("entries", []):
            if not isinstance(entry, dict) or entry.get("kind") != "directory":
                continue
            try:
                dir_name = normalize_skill_name(str(entry.get("name") or ""))
            except ValueError:
                continue
            try:
                content = await file_io.read_text(f"{skill_dir}/{dir_name}/SKILL.md")
                skill = parse_skill_md(content, Path(skill_dir) / dir_name)
            except Exception as exc:
                logger.debug("Failed to load repository-local skill '%s': %s", dir_name, exc)
                continue
            if skill is not None:
                if skill.name in skills:
                    logger.warning("Duplicate repository-local skill '%s' at %s", skill.name, skill.path)
                    continue
                skills[skill.name] = skill

        with _lock:
            if generation != _generation:
                # A write occurred during I/O; read again before returning.
                continue
            now = time.monotonic()
            expired = [key for key, (expires_at, _) in _entries.items() if now >= expires_at]
            for key in expired:
                del _entries[key]
            _entries[cache_key] = (now + DISCOVERY_TTL_SECONDS, copy.deepcopy(skills))
            _entries.move_to_end(cache_key)
            while len(_entries) > MAX_DISCOVERY_CACHE_ENTRIES:
                _entries.popitem(last=False)
        return skills


async def load_repository_skill_resources(
    skill: Skill, *, workspace: Any, thread_id: str | None = None,
) -> Skill:
    """Discover resources only when the skill's full content is requested."""
    from agent.modules.workspaces import get_workspace_browser

    try:
        browser = await get_workspace_browser(workspace, thread_id=thread_id)
        resources = await list_workspace_resources(browser, skill.path.as_posix())
    except Exception as exc:
        logger.warning("Failed to list resources for skill '%s': %s", skill.name, exc)
        resources = []
    return replace(skill, resources=resources)
