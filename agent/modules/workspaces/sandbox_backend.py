"""Abstract base class for cloud sandbox backends (Daytona, Modal)."""

from __future__ import annotations

import logging
import mimetypes
import posixpath
import shlex
import time
from abc import ABC, abstractmethod
from typing import Any

from agent.modules.workspaces.backends import CommandResult, WorkspaceRef
from agent.modules.workspaces.constants import (
    GIT_STATUS_CACHE_TTL,
    GIT_TIMEOUT_SECONDS,
    IGNORED_DIR_NAMES,
    MAX_FILE_BYTES,
    MAX_TREE_ENTRIES,
    MAX_UNTRACKED_FILE_CHARS,
)
from agent.modules.workspaces.file_info_utils import (
    file_info_is_dir,
    file_info_modified_at,
    file_info_name,
    file_info_path,
    file_info_size,
)
from agent.modules.workspaces.git_utils import (
    build_untracked_diff_content,
    compute_change_line_stats_from_numstat,
    git_relative_path,
    git_status_args,
    parse_git_status,
    truncate_diff,
)
from agent.modules.workspaces.metadata_cache import (
    get_workspace_metadata_cache,
    invalidate_workspace_metadata_cache,
    set_workspace_metadata_cache,
)
from agent.modules.workspaces.posix_utils import normalize_posix_path

logger = logging.getLogger(__name__)


class SandboxBackendBase(ABC):
    """Shared implementation for cloud sandbox backends.

    Subclasses **must** implement the abstract methods that touch the
    remote sandbox (``_exec``, ``_read_file_bytes``, ``_write_file_bytes``,
    ``_download_file``, ``upload_file``, ``_make_directory``,
    ``_stat_file``, ``clone_repository``).
    """

    # ------------------------------------------------------------------ #
    #  Construction & lifecycle
    # ------------------------------------------------------------------ #

    def __init__(self, ref: WorkspaceRef) -> None:
        if ref.backend not in {"daytona", "modal"}:
            raise ValueError(f"Unsupported sandbox backend: {ref.backend}")
        self.ref = ref
        self.root: str = ""
        self._git_status_cache_ts: float = 0.0
        self._git_status_cache: dict[str, dict[str, Any]] | None = None

    # ------------------------------------------------------------------ #
    #  Abstract primitives – subclasses must implement
    # ------------------------------------------------------------------ #

    @abstractmethod
    def ensure_active(self) -> None:
        """Ensure the underlying sandbox is running."""
        ...

    @abstractmethod
    def ensure_root(self) -> None:
        """Create the workspace root directory inside the sandbox."""
        ...

    @abstractmethod
    def _exec(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        """Execute a shell command inside the sandbox."""
        ...

    @abstractmethod
    def _download_file(self, path: str) -> bytes | str:
        """Download file content from the sandbox."""
        ...

    @abstractmethod
    def _upload_file(self, content: bytes, path: str) -> None:
        """Upload file content to the sandbox."""
        ...

    @abstractmethod
    def _make_directory(self, path: str) -> None:
        """Create a directory inside the sandbox."""
        ...

    @abstractmethod
    def _stat_file(self, path: str) -> Any:
        """Return a file-info object for *path*."""
        ...

    @abstractmethod
    async def clone_repository(
        self,
        *,
        owner: str,
        repo: str,
        default_branch: str = "main",
        token: str | None = None,
        depth: int = 1,
    ) -> str:
        """Clone a git repository into the sandbox, return relative path."""
        ...

    # ------------------------------------------------------------------ #
    #  File I/O helpers (shared)
    # ------------------------------------------------------------------ #

    async def read_text(self, file_path: str) -> str:
        """Read a text file from the sandbox."""
        from agent.modules.workspaces.posix_utils import resolve_remote_path

        remote_path = resolve_remote_path(self.root, file_path)
        raw = self._download_file(remote_path)
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw or "")

    async def write_text(self, file_path: str, content: str) -> str:
        """Write a text file to the sandbox."""
        from agent.modules.workspaces.posix_utils import resolve_remote_path

        self._invalidate_workspace_caches()
        remote_path = resolve_remote_path(self.root, file_path)
        parent = remote_path.rsplit("/", 1)[0] or "/"
        self._make_directory(parent)
        self._upload_file(content.encode("utf-8"), remote_path)
        return f"[OK] Wrote file: {remote_path}"

    # ------------------------------------------------------------------ #
    #  Browser / tree
    # ------------------------------------------------------------------ #

    async def tree(self, path: str | None = None) -> dict[str, Any]:
        """Return directory listing for *path*."""
        from agent.modules.workspaces.posix_utils import resolve_remote_path

        self.ensure_active()
        target = resolve_remote_path(self.root, path or ".")
        cached, value = self._get_metadata_cache("tree", target)
        if cached:
            return value
        entries: list[dict[str, Any]] = []
        truncated = False
        items = self._list_remote_files(target)
        for item in items:
            name = file_info_name(item)
            if not name:
                continue
            is_dir = file_info_is_dir(item)
            if is_dir and name in IGNORED_DIR_NAMES:
                continue
            if len(entries) >= MAX_TREE_ENTRIES:
                truncated = True
                break
            entry_path = file_info_path(item, target)
            entries.append(
                {
                    "name": name,
                    "path": entry_path,
                    "kind": "directory" if is_dir else "file",
                    "size": file_info_size(item) if not is_dir else None,
                    "modified_at": file_info_modified_at(item),
                }
            )
        entries.sort(
            key=lambda item: (item["kind"] != "directory", item["name"].lower())
        )
        result = {
            "root": self.root,
            "path": target,
            "entries": entries,
            "truncated": truncated,
        }
        self._set_metadata_cache("tree", result, target)
        return result

    async def file(self, path: str) -> dict[str, Any]:
        """Return file content and metadata."""
        from agent.modules.workspaces.posix_utils import resolve_remote_path

        self.ensure_active()
        remote_path = resolve_remote_path(self.root, path)
        info = self._stat_file(remote_path)
        if file_info_is_dir(info):
            raise ValueError(f"Path is not a file: {path}")
        raw = self._download_file(remote_path)
        if isinstance(raw, str):
            raw_bytes = raw.encode("utf-8")
        else:
            raw_bytes = bytes(raw or b"")
        truncated = len(raw_bytes) > MAX_FILE_BYTES
        if truncated:
            raw_bytes = raw_bytes[:MAX_FILE_BYTES]
        mime_type = mimetypes.guess_type(remote_path)[0] or "text/plain"
        if b"\0" in raw_bytes:
            return {
                "root": self.root,
                "path": remote_path,
                "mime_type": mime_type,
                "size": file_info_size(info),
                "content": "",
                "truncated": truncated,
                "binary": True,
                "message": "Binary files cannot be previewed.",
            }
        return {
            "root": self.root,
            "path": remote_path,
            "mime_type": mime_type,
            "size": file_info_size(info),
            "content": raw_bytes.decode("utf-8", errors="replace"),
            "truncated": truncated,
            "binary": False,
            "message": "File truncated." if truncated else "",
        }

    # ------------------------------------------------------------------ #
    #  Git operations (shared)
    # ------------------------------------------------------------------ #

    async def changes(self) -> dict[str, Any]:
        """Return git status for the workspace."""
        self.ensure_active()
        git_root = await self._find_git_root()
        if git_root is None:
            return {
                "root": self.root,
                "is_git_repo": False,
                "changes": [],
                "message": "Workspace is not a Git repository.",
            }
        output = await self._run_git_raw(git_status_args(), cwd=self.root)
        changes = parse_git_status(output, workspace_root=self.root, git_root=git_root)
        await self._batch_change_line_stats(changes, git_root=git_root)
        self._set_git_status_cache(changes)
        return {
            "root": self.root,
            "is_git_repo": True,
            "changes": changes,
            "message": "",
        }

    async def diff(self, path: str) -> dict[str, Any]:
        """Return git diff for a single file."""
        from agent.modules.workspaces.posix_utils import (
            relative_remote_path,
            resolve_remote_path,
        )

        self.ensure_active()
        remote_path = resolve_remote_path(self.root, path)
        relative = relative_remote_path(self.root, remote_path)
        if not relative:
            raise ValueError("File path is required.")

        git_root = await self._find_git_root()
        if git_root is None:
            return {
                "root": self.root,
                "path": remote_path,
                "is_git_repo": False,
                "status": "",
                "diff": "",
                "truncated": False,
                "message": "Workspace is not a Git repository.",
            }

        status_by_path = self._get_valid_git_status_cache()
        if status_by_path is None:
            status_by_path = {
                change["path"]: change
                for change in parse_git_status(
                    await self._run_git_raw(git_status_args(), cwd=self.root),
                    workspace_root=self.root,
                    git_root=git_root,
                )
            }
        change = status_by_path.get(remote_path)
        status = str(change.get("status") or "") if change else ""
        gpath = git_relative_path(git_root, remote_path)
        if status == "untracked":
            diff = await self._build_untracked_diff(remote_path, relative)
        else:
            staged = await self._run_git_raw(
                ["diff", "--cached", "--no-ext-diff", "--", gpath],
                cwd=git_root,
            )
            unstaged = await self._run_git_raw(
                ["diff", "--no-ext-diff", "--", gpath],
                cwd=git_root,
            )
            diff = "\n".join(
                part for part in (staged.strip(), unstaged.strip()) if part
            )

        diff, truncated = truncate_diff(diff)
        return {
            "root": self.root,
            "path": remote_path,
            "is_git_repo": True,
            "status": status,
            "diff": diff,
            "truncated": truncated,
            "message": "" if diff else "No diff is available for this file.",
        }

    # ------------------------------------------------------------------ #
    #  Entry mutation (shared)
    # ------------------------------------------------------------------ #

    async def rename(self, *, path: str, new_name: str) -> dict[str, Any]:
        """Rename a file or directory."""
        from agent.modules.workspaces.posix_utils import (
            relative_remote_path,
            resolve_remote_path,
        )

        self._invalidate_workspace_caches()
        self.ensure_active()
        source = resolve_remote_path(self.root, path)
        relative = relative_remote_path(self.root, source)
        if not relative:
            raise ValueError("Cannot rename workspace root.")
        clean_name = str(new_name or "").strip()
        if not clean_name or clean_name in {".", ".."}:
            raise ValueError("New name is invalid.")
        if "/" in clean_name or "\\" in clean_name:
            raise ValueError("New name must not contain path separators.")
        destination = resolve_remote_path(
            self.root,
            __import__("posixpath").posixpath.join(
                __import__("posixpath").posixpath.dirname(relative), clean_name
            ),
        )
        if destination == source:
            return {"root": self.root, "path": source, "new_path": destination}
        check = self._exec_sync(
            f"test -e {shlex.quote(destination)}", cwd="/"
        )
        if check.exit_code == 0:
            raise FileExistsError(f"Destination already exists: {clean_name}")
        result = self._exec_sync(
            f"mv {shlex.quote(source)} {shlex.quote(destination)}",
            cwd="/",
        )
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Rename failed.")
        return {"root": self.root, "path": source, "new_path": destination}

    async def delete(self, *, path: str) -> dict[str, Any]:
        """Delete a file or directory."""
        from agent.modules.workspaces.posix_utils import (
            relative_remote_path,
            resolve_remote_path,
        )

        self._invalidate_workspace_caches()
        self.ensure_active()
        target = resolve_remote_path(self.root, path)
        relative = relative_remote_path(self.root, target)
        if not relative:
            raise ValueError("Cannot delete workspace root.")
        kind_result = self._exec_sync(
            f"if [ -d {shlex.quote(target)} ]; then echo directory; "
            f"elif [ -f {shlex.quote(target)} ]; then echo file; "
            "else exit 44; fi",
            cwd="/",
        )
        if kind_result.exit_code == 44:
            raise FileNotFoundError(f"Path does not exist: {path}")
        if kind_result.exit_code not in (0, None):
            raise RuntimeError(kind_result.output.strip() or "Delete failed.")
        kind = kind_result.output.strip().splitlines()[-1]
        result = self._exec_sync(f"rm -rf {shlex.quote(target)}", cwd="/")
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Delete failed.")
        return {"root": self.root, "path": target, "kind": kind}

    # ------------------------------------------------------------------ #
    #  Private helpers
    # ------------------------------------------------------------------ #

    def _list_remote_files(self, path: str) -> list[Any]:
        """List files at *path* using the remote fs API.

        Subclasses may override this to use their specific API.
        """
        raise NotImplementedError(
            "Subclasses must implement _list_remote_files or override tree()."
        )

    def _exec_sync(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        """Synchronous command execution (for rename/delete).

        Defaults to ``_exec``; subclasses may override for sync sandboxes.
        """
        return self._exec(command, cwd=cwd, timeout=timeout,
                          max_output_chars=max_output_chars)

    def _cache_root(self) -> str:
        return str(self.root or self.ref.metadata.get("root") or "")

    def _get_metadata_cache(self, operation: str, *parts: Any) -> tuple[bool, Any]:
        return get_workspace_metadata_cache(
            self.ref,
            root=self._cache_root(),
            operation=operation,
            parts=tuple(parts),
        )

    def _set_metadata_cache(self, operation: str, value: Any, *parts: Any) -> None:
        set_workspace_metadata_cache(
            self.ref,
            value,
            root=self._cache_root(),
            operation=operation,
            parts=tuple(parts),
        )

    def _invalidate_workspace_caches(self) -> None:
        invalidate_workspace_metadata_cache(self.ref, root=self._cache_root())
        self._git_status_cache = None
        self._git_status_cache_ts = 0.0

    def _set_git_status_cache(self, changes: list[dict[str, Any]]) -> None:
        status_by_path: dict[str, dict[str, Any]] = {}
        for change in changes:
            path = change.get("path", "")
            if path:
                status_by_path[path] = change
        self._git_status_cache = status_by_path
        self._git_status_cache_ts = time.monotonic()

    def _get_valid_git_status_cache(self) -> dict[str, dict[str, Any]] | None:
        status_by_path = self._git_status_cache
        if status_by_path is None:
            return None
        if (time.monotonic() - self._git_status_cache_ts) > GIT_STATUS_CACHE_TTL:
            return None
        return status_by_path

    async def _run_git_raw(self, args: list[str], *, cwd: str) -> str:
        """Run a git command and return stdout."""
        command = "git " + " ".join(shlex.quote(arg) for arg in args)
        result = await self._exec_async(command, cwd=cwd, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            detail = result.output.strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        return result.output

    async def _exec_async(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        """Async wrapper around ``_exec``.

        Subclasses that are natively async should override this.
        """
        import asyncio

        return await asyncio.to_thread(
            self._exec, command, cwd=cwd, timeout=timeout,
            max_output_chars=max_output_chars,
        )

    async def _find_git_root(self) -> str | None:
        """Return the absolute path of the git repository root, or ``None``."""
        try:
            output = (await self._run_git_raw(
                ["rev-parse", "--show-toplevel"], cwd=self.root
            )).strip()
        except Exception:
            return None
        if not output:
            return None
        return normalize_posix_path(output.splitlines()[-1])

    async def _change_line_stats(
        self,
        change: dict[str, Any],
        *,
        git_root: str,
    ) -> tuple[int, int]:
        """Compute addition/deletion line counts for a single change."""
        path = str(change.get("path") or "")
        if not path:
            return 0, 0
        if change.get("status") == "untracked":
            return await self._text_line_count(path), 0
        gpath = git_relative_path(git_root, path)
        additions = 0
        deletions = 0
        for args in (
            ["diff", "--numstat", "--cached", "--no-ext-diff", "--", gpath],
            ["diff", "--numstat", "--no-ext-diff", "--", gpath],
        ):
            try:
                output = await self._run_git_raw(args, cwd=git_root)
            except Exception:
                continue
            a, d = compute_change_line_stats_from_numstat(output)
            additions += a
            deletions += d
        return additions, deletions

    async def _batch_change_line_stats(
        self,
        changes: list[dict[str, Any]],
        *,
        git_root: str,
    ) -> None:
        """Compute addition/deletion line counts for all changes in batch.

        Runs ``git diff --numstat`` only twice (staged + unstaged) regardless
        of the number of changed files.
        """
        if not changes:
            return

        numstat_by_path: dict[str, dict[str, int]] = {}
        has_tracked_changes = any(
            change.get("status") != "untracked" for change in changes
        )
        if has_tracked_changes:
            for args in (
                ["diff", "--numstat", "--cached", "--no-ext-diff"],
                ["diff", "--numstat", "--no-ext-diff"],
            ):
                try:
                    output = await self._run_git_raw(args, cwd=git_root)
                except Exception as exc:
                    logger.debug("Failed to compute batch line stats: %s", exc)
                    continue
                for line in output.splitlines():
                    fields = line.split("\t", 3)
                    if len(fields) < 3:
                        continue
                    add_str, del_str, file_path = fields[0], fields[1], fields[2]
                    abs_path = posixpath.normpath(posixpath.join(git_root, file_path))
                    entry = numstat_by_path.setdefault(
                        abs_path,
                        {"additions": 0, "deletions": 0},
                    )
                    if add_str.isdigit():
                        entry["additions"] += int(add_str)
                    if del_str.isdigit():
                        entry["deletions"] += int(del_str)

        for change in changes:
            path = str(change.get("path") or "")
            if change.get("status") == "untracked":
                change["additions"] = await self._text_line_count(path)
                change["deletions"] = 0
            else:
                stats = numstat_by_path.get(path, {})
                change["additions"] = stats.get("additions", 0)
                change["deletions"] = stats.get("deletions", 0)

    async def _text_line_count(self, path: str) -> int:
        """Count text lines in a remote file."""
        try:
            raw = self._download_file(path)
        except Exception:
            return 0
        raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw or b"")
        if b"\0" in raw_bytes:
            return 0
        text = raw_bytes[: MAX_UNTRACKED_FILE_CHARS + 1].decode(
            "utf-8", errors="replace"
        )
        return len(text.splitlines()) or (1 if text else 0)

    async def _build_untracked_diff(self, path: str, relative_path: str) -> str:
        """Build a unified diff for an untracked file."""
        try:
            content = await self.read_text(path)
        except Exception:
            return ""
        return build_untracked_diff_content(content, relative_path)


__all__ = ["SandboxBackendBase"]
