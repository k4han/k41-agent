from __future__ import annotations

import difflib
import logging
import subprocess
from pathlib import Path
from typing import Any

from agent.modules.workflows import DEFAULT_WORKING_DIR
from agent.modules.workspaces.repository import get_thread_workspace_repository

logger = logging.getLogger(__name__)

MAX_TREE_ENTRIES = 500
MAX_DIFF_CHARS = 200_000
MAX_UNTRACKED_FILE_CHARS = 120_000
GIT_TIMEOUT_SECONDS = 10
IGNORED_DIR_NAMES = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
}


def resolve_workspace_root(working_dir: str | None = None) -> Path:
    source = str(working_dir or "").strip() or DEFAULT_WORKING_DIR
    return Path(source).expanduser().resolve()


def ensure_workspace_directory(working_dir: str | None = None) -> Path:
    root = resolve_workspace_root(working_dir)
    if not root.exists():
        raise FileNotFoundError(f"Workspace does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Workspace is not a directory: {root}")
    return root


def resolve_workspace_child(root: Path, path: str | None = None) -> Path:
    relative_path = str(path or "").strip()
    target = (root / relative_path).resolve()
    if target != root and not target.is_relative_to(root):
        raise ValueError("Path escapes workspace.")
    return target


def workspace_relative_path(root: Path, target: Path) -> str:
    if target == root:
        return ""
    return target.relative_to(root).as_posix()


async def remember_thread_workspace(thread_id: str, working_dir: str | None) -> str:
    root = resolve_workspace_root(working_dir)
    await get_thread_workspace_repository().upsert(
        thread_id=thread_id,
        working_dir=str(root),
    )
    return str(root)


async def get_thread_workspace_dir(thread_id: str) -> str | None:
    record = await get_thread_workspace_repository().get(thread_id)
    if not record:
        return None
    return str(record.get("working_dir") or "") or None


def list_workspace_tree(
    *,
    working_dir: str | None,
    path: str | None = None,
) -> dict[str, Any]:
    root = ensure_workspace_directory(working_dir)
    target = resolve_workspace_child(root, path)
    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {path or '.'}")
    if not target.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {path or '.'}")

    entries: list[dict[str, Any]] = []
    truncated = False
    for child in sorted(
        target.iterdir(),
        key=lambda item: (not item.is_dir(), item.name.lower()),
    ):
        if child.is_dir() and child.name in IGNORED_DIR_NAMES:
            continue
        if len(entries) >= MAX_TREE_ENTRIES:
            truncated = True
            break
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "path": workspace_relative_path(root, child),
                "kind": "directory" if child.is_dir() else "file",
                "size": stat.st_size if child.is_file() else None,
                "modified_at": stat.st_mtime,
            }
        )

    return {
        "root": str(root),
        "path": workspace_relative_path(root, target),
        "entries": entries,
        "truncated": truncated,
    }


def get_workspace_changes(working_dir: str | None) -> dict[str, Any]:
    root = ensure_workspace_directory(working_dir)
    git_root = _find_git_root(root)
    if git_root is None:
        return {
            "root": str(root),
            "is_git_repo": False,
            "changes": [],
            "message": "Workspace is not a Git repository.",
        }

    output = _run_git(_git_status_args(), cwd=root)
    changes = _parse_git_status(output, workspace_root=root, git_root=git_root)
    return {
        "root": str(root),
        "is_git_repo": True,
        "changes": changes,
        "message": "",
    }


def get_workspace_diff(*, working_dir: str | None, path: str) -> dict[str, Any]:
    root = ensure_workspace_directory(working_dir)
    target = resolve_workspace_child(root, path)
    relative_path = workspace_relative_path(root, target)
    if not relative_path:
        raise ValueError("File path is required.")

    git_root = _find_git_root(root)
    if git_root is None:
        return {
            "root": str(root),
            "path": relative_path,
            "is_git_repo": False,
            "status": "",
            "diff": "",
            "truncated": False,
            "message": "Workspace is not a Git repository.",
        }

    status_by_path = {
        change["path"]: change
        for change in _parse_git_status(
            _run_git(_git_status_args(), cwd=root),
            workspace_root=root,
            git_root=git_root,
        )
    }
    change = status_by_path.get(relative_path)
    status = str(change.get("status") or "") if change else ""
    git_path = _git_relative_path(git_root, target)

    if status == "untracked":
        diff = _build_untracked_diff(target, relative_path)
    else:
        unstaged = _run_git(["diff", "--no-ext-diff", "--", git_path], cwd=git_root)
        staged = _run_git(
            ["diff", "--cached", "--no-ext-diff", "--", git_path],
            cwd=git_root,
        )
        diff = "\n".join(part for part in (staged.strip(), unstaged.strip()) if part)

    diff, truncated = _truncate_diff(diff)
    return {
        "root": str(root),
        "path": relative_path,
        "is_git_repo": True,
        "status": status,
        "diff": diff,
        "truncated": truncated,
        "message": "" if diff else "No diff is available for this file.",
    }


def _run_git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _git_status_args() -> list[str]:
    return [
        "-c",
        "status.relativePaths=false",
        "status",
        "--porcelain=v1",
        "-z",
        "--",
        ".",
    ]


def _find_git_root(root: Path) -> Path | None:
    try:
        output = _run_git(["rev-parse", "--show-toplevel"], cwd=root).strip()
    except Exception:
        return None
    if not output:
        return None
    return Path(output).resolve()


def _parse_git_status(
    output: str,
    *,
    workspace_root: Path,
    git_root: Path,
) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    items = output.split("\0")
    index = 0
    while index < len(items):
        item = items[index]
        index += 1
        if not item:
            continue
        if len(item) < 4:
            continue
        code = item[:2]
        raw_path = item[3:]
        old_path = ""
        if ("R" in code or "C" in code) and index < len(items):
            old_path = items[index]
            index += 1
        if code == "!!":
            continue

        absolute_path = (git_root / raw_path).resolve()
        if absolute_path != workspace_root and not absolute_path.is_relative_to(
            workspace_root
        ):
            continue

        status = _status_label(code)
        entry = {
            "path": workspace_relative_path(workspace_root, absolute_path),
            "status": status,
            "index_status": code[0],
            "working_tree_status": code[1],
        }
        if old_path:
            old_absolute = (git_root / old_path).resolve()
            if old_absolute == workspace_root or old_absolute.is_relative_to(
                workspace_root
            ):
                entry["old_path"] = workspace_relative_path(workspace_root, old_absolute)
        changes.append(entry)

    return sorted(changes, key=lambda change: change["path"])


def _status_label(code: str) -> str:
    if code == "??":
        return "untracked"
    if "R" in code:
        return "renamed"
    if "A" in code and "D" not in code:
        return "added"
    if "D" in code and "A" not in code:
        return "deleted"
    return "modified"


def _git_relative_path(git_root: Path, target: Path) -> str:
    return target.resolve().relative_to(git_root).as_posix()


def _build_untracked_diff(target: Path, relative_path: str) -> str:
    if not target.exists() or not target.is_file():
        return ""
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Binary file b/{relative_path} is untracked."
    except OSError as exc:
        logger.debug("Failed to read untracked file %s: %s", target, exc)
        return ""

    truncated = len(content) > MAX_UNTRACKED_FILE_CHARS
    if truncated:
        content = content[:MAX_UNTRACKED_FILE_CHARS]
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


def _truncate_diff(diff: str) -> tuple[str, bool]:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False
    return diff[:MAX_DIFF_CHARS] + "\n...[truncated]", True
