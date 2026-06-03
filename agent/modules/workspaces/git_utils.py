"""Shared git utilities for workspace backends."""

from __future__ import annotations

import difflib
import logging
from typing import Any

from agent.modules.workspaces.constants import (
    GIT_TIMEOUT_SECONDS,
    MAX_DIFF_CHARS,
    MAX_UNTRACKED_FILE_CHARS,
)
from agent.modules.workspaces.posix_utils import normalize_posix_path

import posixpath

logger = logging.getLogger(__name__)


def git_status_args() -> list[str]:
    """Return the standard ``git status`` arguments for porcelain output."""
    return [
        "-c",
        "status.relativePaths=false",
        "status",
        "--porcelain=v1",
        "-z",
        "--",
        ".",
    ]


def parse_git_status(
    output: str,
    *,
    workspace_root: str,
    git_root: str,
) -> list[dict[str, Any]]:
    """Parse ``git status --porcelain=v1 -z`` output into a list of change dicts.

    Each dict contains ``path``, ``status``, ``index_status``,
    ``working_tree_status`` and optionally ``old_path``.
    """
    changes: list[dict[str, Any]] = []
    items = output.split("\0")
    index = 0
    while index < len(items):
        item = items[index]
        index += 1
        if not item or len(item) < 4:
            continue
        code = item[:2]
        raw_path = item[3:]
        old_path = ""
        if ("R" in code or "C" in code) and index < len(items):
            old_path = items[index]
            index += 1
        if code == "!!":
            continue
        absolute_path = normalize_posix_path(posixpath.join(git_root, raw_path))
        if absolute_path != workspace_root and not absolute_path.startswith(
            f"{workspace_root}/"
        ):
            continue
        entry = {
            "path": absolute_path,
            "status": status_label(code),
            "index_status": code[0],
            "working_tree_status": code[1],
        }
        if old_path:
            old_absolute = normalize_posix_path(posixpath.join(git_root, old_path))
            if old_absolute == workspace_root or old_absolute.startswith(
                f"{workspace_root}/"
            ):
                entry["old_path"] = old_absolute
        changes.append(entry)
    return sorted(changes, key=lambda change: change["path"])


def status_label(code: str) -> str:
    """Convert a two-character git status code into a human-readable label."""
    if code == "??":
        return "untracked"
    if "R" in code:
        return "renamed"
    if "A" in code and "D" not in code:
        return "added"
    if "D" in code and "A" not in code:
        return "deleted"
    return "modified"


def git_relative_path(git_root: str, target: str) -> str:
    """Return *target* relative to *git_root* in posix format."""
    target_path = normalize_posix_path(target)
    root = normalize_posix_path(git_root)
    if target_path == root:
        return ""
    if root == "/":
        return target_path.lstrip("/")
    if not target_path.startswith(f"{root}/"):
        raise ValueError("Path escapes Git repository.")
    return target_path[len(root) + 1 :]


def truncate_diff(diff: str) -> tuple[str, bool]:
    """Truncate *diff* to ``MAX_DIFF_CHARS`` if necessary.

    Returns ``(truncated_diff, was_truncated)``.
    """
    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False
    return diff[:MAX_DIFF_CHARS] + "\n...[truncated]", True


def build_untracked_diff_content(
    content: str,
    relative_path: str,
    *,
    max_chars: int = MAX_UNTRACKED_FILE_CHARS,
) -> str:
    """Build a unified diff for an untracked file given its *content*."""
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
    lines = content.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        [],
        lines,
        fromfile="/dev/null",
        tofile=f"b/{relative_path}",
        lineterm="",
    )
    diff = "\n".join(diff_lines)
    if truncated:
        diff += "\n...[truncated]"
    return diff


def compute_change_line_stats_from_numstat(
    numstat_output: str,
) -> tuple[int, int]:
    """Parse ``git diff --numstat`` output and return ``(additions, deletions)``."""
    additions = 0
    deletions = 0
    for line in numstat_output.splitlines():
        fields = line.split("\t", 2)
        if len(fields) < 2:
            continue
        if fields[0].isdigit():
            additions += int(fields[0])
        if fields[1].isdigit():
            deletions += int(fields[1])
    return additions, deletions


__all__ = [
    "build_untracked_diff_content",
    "compute_change_line_stats_from_numstat",
    "git_relative_path",
    "git_status_args",
    "parse_git_status",
    "status_label",
    "truncate_diff",
]
