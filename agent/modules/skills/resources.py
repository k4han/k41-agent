"""Resource discovery for filesystem and workspace skill sources."""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import Any

RESOURCE_DIRECTORIES = ("scripts", "references", "assets")
MAX_RESOURCE_DIRECTORIES = 256
logger = logging.getLogger(__name__)


def list_local_resources(skill_dir: Path) -> list[str]:
    """List bundled files without following resources outside the skill."""
    root = skill_dir.resolve()
    resources: list[str] = []
    for name in RESOURCE_DIRECTORIES:
        directory = skill_dir / name
        if not directory.is_dir() or not directory.resolve().is_relative_to(root):
            continue
        for file in directory.rglob("*"):
            if file.is_file() and file.resolve().is_relative_to(root):
                resources.append(file.relative_to(skill_dir).as_posix())
    return sorted(resources)


async def list_workspace_resources(browser: Any, skill_dir: str) -> list[str]:
    """Walk resource directories through the workspace's browser backend."""
    tree = await browser.tree(skill_dir)
    pending = [
        str(entry.get("name"))
        for entry in tree.get("entries", [])
        if isinstance(entry, dict)
        and entry.get("kind") == "directory"
        and entry.get("name") in RESOURCE_DIRECTORIES
    ]
    resources: set[str] = set()
    visited: set[str] = set()
    while pending and len(visited) < MAX_RESOURCE_DIRECTORIES:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        try:
            tree = await browser.tree(f"{skill_dir}/{relative}")
        except FileNotFoundError:
            continue
        for entry in tree.get("entries", []):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            if not name or name in {".", ".."} or "/" in name or "\\" in name:
                continue
            path = str(PurePosixPath(relative) / name)
            if entry.get("kind") == "directory":
                pending.append(path)
            elif entry.get("kind") == "file":
                resources.add(path)
    if pending:
        logger.warning("Resource directory limit reached for skill at %s", skill_dir)
    return sorted(resources)
