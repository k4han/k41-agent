"""Resource discovery for filesystem and workspace skill sources."""

from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath
from typing import Any

from agent.modules.workspaces import IGNORED_DIR_NAMES

RESOURCE_DIRECTORIES = ("scripts", "references", "assets")
MAX_RESOURCE_DIRECTORIES = 256
logger = logging.getLogger(__name__)


def list_local_resources(skill_dir: Path) -> list[str]:
    """List bundled files without following resources outside the skill."""
    root = skill_dir.resolve()
    resources: list[str] = []
    for current, directories, files in os.walk(skill_dir, followlinks=False):
        directories[:] = [name for name in directories if name not in IGNORED_DIR_NAMES
                          and (Path(current) / name).resolve().is_relative_to(root)]
        for name in files:
            file = Path(current) / name
            if file == skill_dir / "SKILL.md" or name.startswith(".skill-"):
                continue
            if file.is_file() and file.resolve().is_relative_to(root):
                resources.append(file.relative_to(skill_dir).as_posix())
    return sorted(resources)


async def list_workspace_resources(browser: Any, skill_dir: str) -> list[str]:
    """Walk resource directories through the workspace's browser backend."""
    pending = [""]
    resources: set[str] = set()
    visited: set[str] = set()
    while pending and len(visited) < MAX_RESOURCE_DIRECTORIES:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        try:
            path = f"{skill_dir}/{relative}".rstrip("/")
            entries = []
            offset = 0
            while True:
                pager = getattr(browser, "tree_page", None)
                tree = await pager(path, offset=offset) if callable(pager) else await browser.tree(path)
                entries.extend(tree.get("entries", []))
                next_offset = tree.get("next_offset")
                if next_offset is None:
                    if tree.get("truncated"):
                        raise ValueError("Resource listing is incomplete.")
                    break
                if next_offset <= offset:
                    raise ValueError("Resource pagination did not advance.")
                offset = next_offset
        except FileNotFoundError:
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            if not name or name in {".", ".."} or "/" in name or "\\" in name:
                continue
            if name in IGNORED_DIR_NAMES or name.startswith(".skill-") or (not relative and name == "SKILL.md"):
                continue
            path = str(PurePosixPath(relative) / name)
            if entry.get("kind") == "directory":
                pending.append(path)
            elif entry.get("kind") == "file":
                resources.add(path)
    if pending:
        logger.warning("Resource directory limit reached for skill at %s", skill_dir)
    return sorted(resources)
