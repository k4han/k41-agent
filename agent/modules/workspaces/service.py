from __future__ import annotations

import difflib
import logging
import mimetypes
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agent.modules.workspaces.repository import get_thread_workspace_repository
from agent.modules.workspaces.refs import (
    DEFAULT_LOCAL_WORKSPACE,
    WorkspaceRef,
    normalize_workspace_ref,
)

logger = logging.getLogger(__name__)

MAX_TREE_ENTRIES = 500
MAX_DIFF_CHARS = 200_000
MAX_FILE_BYTES = 300_000
MAX_UNTRACKED_FILE_CHARS = 120_000
MAX_DIRECTORY_BROWSE_ENTRIES = 500
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


def resolve_workspace_ref(workspace: WorkspaceRef | dict[str, Any] | str | None = None) -> WorkspaceRef:
    return normalize_workspace_ref(workspace, default_locator=DEFAULT_LOCAL_WORKSPACE)


def workspace_ref_from_local_path(
    working_dir: str | None = None,
    *,
    label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkspaceRef:
    return normalize_workspace_ref(
        {
            "backend": "local",
            "locator": working_dir,
            "label": label,
            "metadata": metadata or {},
        },
        default_locator=DEFAULT_LOCAL_WORKSPACE,
    )


def get_workspace_backend(workspace: WorkspaceRef | dict[str, Any] | str | None = None):
    ref = resolve_workspace_ref(workspace)
    if ref.backend == "local":
        from agent.modules.workspaces.local_backend import LocalWorkspaceBackend

        return LocalWorkspaceBackend(ref)
    raise ValueError(f"Unsupported workspace backend: {ref.backend}")


def resolve_workspace_root(working_dir: str | None = None) -> Path:
    source = str(working_dir or "").strip() or DEFAULT_LOCAL_WORKSPACE
    return Path(source).expanduser().resolve()


def ensure_workspace_directory(working_dir: str | None = None) -> Path:
    root = resolve_workspace_root(working_dir)
    if not root.exists():
        raise FileNotFoundError(f"Workspace does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Workspace is not a directory: {root}")
    return root


def _filesystem_roots() -> list[dict[str, str]]:
    if Path("C:/").exists():
        roots = [
            Path(f"{chr(code)}:/").resolve()
            for code in range(ord("A"), ord("Z") + 1)
            if Path(f"{chr(code)}:/").exists()
        ]
    else:
        roots = [Path("/")]
    return [{"name": str(root), "path": str(root)} for root in roots]


def list_workspace_directories(path: str | None = None) -> dict[str, Any]:
    source = str(path or "").strip()
    root = Path(source).expanduser().resolve() if source else Path.home().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {root}")

    entries: list[dict[str, str]] = []
    truncated = False
    try:
        children = sorted(
            root.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.lower()),
        )
    except PermissionError as exc:
        raise ValueError(f"Cannot access directory: {root}") from exc

    for child in children:
        if len(entries) >= MAX_DIRECTORY_BROWSE_ENTRIES:
            truncated = True
            break
        try:
            if not child.is_dir():
                continue
            resolved = child.resolve()
        except OSError:
            continue
        entries.append({"name": child.name, "path": str(resolved)})

    parent = "" if root.parent == root else str(root.parent)
    return {
        "path": str(root),
        "parent": parent,
        "entries": entries,
        "roots": _filesystem_roots(),
        "truncated": truncated,
    }


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
    workspace = resolve_workspace_ref(working_dir)
    await get_thread_workspace_repository().upsert(
        thread_id=thread_id,
        workspace=workspace,
    )
    return workspace.locator


async def remember_thread_workspace_ref(
    thread_id: str,
    workspace: WorkspaceRef | dict[str, Any] | str | None,
) -> WorkspaceRef:
    ref = resolve_workspace_ref(workspace)
    await get_thread_workspace_repository().upsert(
        thread_id=thread_id,
        workspace=ref,
    )
    return ref


async def get_thread_workspace_ref(thread_id: str) -> WorkspaceRef | None:
    record = await get_thread_workspace_repository().get(thread_id)
    if not record:
        return None
    workspace = record.get("workspace")
    if not workspace:
        return None
    return resolve_workspace_ref(workspace)


async def get_thread_workspace_refs(thread_ids: list[str]) -> dict[str, WorkspaceRef]:
    records = await get_thread_workspace_repository().list_by_thread_ids(thread_ids)
    result: dict[str, WorkspaceRef] = {}
    for thread_id, record in records.items():
        workspace = record.get("workspace")
        if workspace:
            result[thread_id] = resolve_workspace_ref(workspace)
    return result


async def get_thread_workspace_dir(thread_id: str) -> str | None:
    workspace = await get_thread_workspace_ref(thread_id)
    return workspace.locator if workspace else None


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
    for change in changes:
        additions, deletions = _change_line_stats(
            change,
            workspace_root=root,
            git_root=git_root,
        )
        change["additions"] = additions
        change["deletions"] = deletions
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


def get_workspace_file(*, working_dir: str | None, path: str) -> dict[str, Any]:
    root = ensure_workspace_directory(working_dir)
    target = resolve_workspace_child(root, path)
    relative_path = workspace_relative_path(root, target)
    if not relative_path:
        raise ValueError("File path is required.")
    if not target.exists():
        raise FileNotFoundError(f"File does not exist: {path}")
    if not target.is_file():
        raise ValueError(f"Path is not a file: {path}")

    stat = target.stat()
    with target.open("rb") as file_handle:
        raw_content = file_handle.read(MAX_FILE_BYTES + 1)

    truncated = len(raw_content) > MAX_FILE_BYTES
    if truncated:
        raw_content = raw_content[:MAX_FILE_BYTES]

    mime_type = mimetypes.guess_type(target.name)[0] or "text/plain"
    if b"\0" in raw_content:
        return {
            "root": str(root),
            "path": relative_path,
            "mime_type": mime_type,
            "size": stat.st_size,
            "content": "",
            "truncated": truncated,
            "binary": True,
            "message": "Binary files cannot be previewed.",
        }

    return {
        "root": str(root),
        "path": relative_path,
        "mime_type": mime_type,
        "size": stat.st_size,
        "content": raw_content.decode("utf-8", errors="replace"),
        "truncated": truncated,
        "binary": False,
        "message": "File truncated." if truncated else "",
    }


def rename_workspace_entry(
    *,
    working_dir: str | None,
    path: str,
    new_name: str,
) -> dict[str, Any]:
    root = ensure_workspace_directory(working_dir)
    target = resolve_workspace_child(root, path)
    relative_path = workspace_relative_path(root, target)
    if not relative_path:
        raise ValueError("Cannot rename workspace root.")
    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")

    clean_name = str(new_name or "").strip()
    if not clean_name:
        raise ValueError("New name is required.")
    if clean_name in {".", ".."}:
        raise ValueError("New name is invalid.")
    if any(separator in clean_name for separator in ("/", "\\")):
        raise ValueError("New name must not contain path separators.")

    destination = (target.parent / clean_name).resolve()
    if destination != root and not destination.is_relative_to(root):
        raise ValueError("Destination escapes workspace.")
    if destination == target:
        return {
            "root": str(root),
            "path": relative_path,
            "new_path": relative_path,
        }
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {clean_name}")

    target.rename(destination)
    return {
        "root": str(root),
        "path": relative_path,
        "new_path": workspace_relative_path(root, destination),
    }


def delete_workspace_entry(
    *,
    working_dir: str | None,
    path: str,
) -> dict[str, Any]:
    root = ensure_workspace_directory(working_dir)
    target = resolve_workspace_child(root, path)
    relative_path = workspace_relative_path(root, target)
    if not relative_path:
        raise ValueError("Cannot delete workspace root.")
    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")

    if target.is_dir():
        shutil.rmtree(target)
        kind = "directory"
    else:
        target.unlink()
        kind = "file"

    return {
        "root": str(root),
        "path": relative_path,
        "kind": kind,
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
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
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


def _change_line_stats(
    change: dict[str, Any],
    *,
    workspace_root: Path,
    git_root: Path,
) -> tuple[int, int]:
    path = str(change.get("path") or "")
    if not path:
        return 0, 0

    if change.get("status") == "untracked":
        target = resolve_workspace_child(workspace_root, path)
        return _text_line_count(target), 0

    target = (workspace_root / path).resolve()
    git_path = _git_relative_path(git_root, target)
    additions = 0
    deletions = 0
    for args in (
        ["diff", "--numstat", "--cached", "--no-ext-diff", "--", git_path],
        ["diff", "--numstat", "--no-ext-diff", "--", git_path],
    ):
        try:
            output = _run_git(args, cwd=git_root)
        except Exception as exc:
            logger.debug("Failed to compute line stats for %s: %s", path, exc)
            continue
        for line in output.splitlines():
            fields = line.split("\t", 2)
            if len(fields) < 2:
                continue
            if fields[0].isdigit():
                additions += int(fields[0])
            if fields[1].isdigit():
                deletions += int(fields[1])
    return additions, deletions


def _text_line_count(target: Path) -> int:
    if not target.exists() or not target.is_file():
        return 0
    try:
        with target.open("rb") as file_handle:
            raw_content = file_handle.read(MAX_UNTRACKED_FILE_CHARS + 1)
    except OSError as exc:
        logger.debug("Failed to read file %s for line stats: %s", target, exc)
        return 0
    if b"\0" in raw_content:
        return 0
    text = raw_content.decode("utf-8", errors="replace")
    return len(text.splitlines()) or (1 if text else 0)


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
