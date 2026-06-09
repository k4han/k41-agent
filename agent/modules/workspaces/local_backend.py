from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import time
from typing import Any

from agent.modules.tools import resolve_safe_path
from agent.modules.workspaces.backends import CommandResult
from agent.modules.workspaces.constants import (
    IGNORED_DIR_NAMES,
    MAX_FILE_BYTES,
    MAX_GLOB_RESULTS,
    MAX_GREP_LINE_CHARS,
)
from agent.modules.workspaces.refs import WorkspaceRef
from agent.modules.workspaces.search_utils import clamp_grep_results
from agent.modules.workspaces.service import (
    delete_workspace_entry,
    get_workspace_changes,
    get_workspace_diff,
    get_workspace_file,
    list_workspace_tree,
    rename_workspace_entry,
    resolve_workspace_root,
)
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

MAX_LIST_FILES_ENTRIES = 5000
GIT_STATUS_CACHE_TTL = 2.0


def create_local_backend(ref: WorkspaceRef, *, thread_id: str | None = None) -> "LocalWorkspaceBackend":
    """Factory used by ``WorkspaceBackendDescriptor.backend_factory_loader``."""
    del thread_id
    return LocalWorkspaceBackend(ref)


class LocalWorkspaceBackend:
    """Workspace backend backed by the host filesystem."""

    def __init__(self, ref: WorkspaceRef) -> None:
        if ref.backend != "local":
            raise ValueError(f"Unsupported workspace backend: {ref.backend}")
        self.ref = ref
        self.root = resolve_workspace_root(ref.locator)
        self._git_status_cache_ts: float = 0.0
        self._git_status_cache: dict[str, dict[str, Any]] | None = None

    async def list_dir(self, path: str = "") -> str:
        target = resolve_safe_path(str(self.root), path or ".")
        entries: list[str] = []
        truncated = False
        try:
            for entry in sorted(os.listdir(target)):
                if len(entries) >= MAX_LIST_FILES_ENTRIES:
                    truncated = True
                    break
                full_path = os.path.join(target, entry)
                if os.path.isdir(full_path):
                    entries.append(f"{entry}/")
                else:
                    entries.append(entry)
        except FileNotFoundError:
            return "(Directory not found)"
        if not entries:
            return "(Empty directory)"
        output = "\n".join(entries)
        if truncated:
            output += f"\n...[truncated at {MAX_LIST_FILES_ENTRIES} entries]"
        return output

    async def read_text(self, file_path: str) -> str:
        full_path = resolve_safe_path(str(self.root), file_path)
        with open(full_path, "r", encoding="utf-8") as file_handle:
            return file_handle.read()

    async def write_text(self, file_path: str, content: str) -> str:
        full_path = resolve_safe_path(str(self.root), file_path)
        parent = os.path.dirname(full_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as file_handle:
            file_handle.write(content)
        return f"[OK] Wrote file: {full_path}"

    async def glob(
        self,
        pattern: str,
        *,
        path: str = "",
        include_dirs: bool = False,
    ) -> str:
        if not pattern:
            raise ValueError("Glob pattern must not be empty.")

        base = resolve_safe_path(str(self.root), path or ".")
        if not os.path.isdir(base):
            return "(Directory not found)"

        matches: list[str] = []
        truncated = False

        for current_root, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIR_NAMES)
            entries: list[tuple[str, bool]] = [(d, True) for d in dirs]
            entries.extend((f, False) for f in files)
            for name, is_dir in sorted(
                entries, key=lambda item: (not item[1], item[0].lower())
            ):
                if not include_dirs and is_dir:
                    continue
                candidate_rel = os.path.relpath(
                    os.path.join(current_root, name), str(self.root)
                )
                candidate_rel = candidate_rel.replace(os.sep, "/")
                if fnmatch.fnmatchcase(candidate_rel, pattern) or fnmatch.fnmatchcase(
                    name, pattern
                ):
                    matches.append(f"{candidate_rel}/" if is_dir else candidate_rel)
                    if len(matches) >= MAX_GLOB_RESULTS:
                        truncated = True
                        break
            if truncated:
                break

        if not matches:
            return "(No matches)"
        output = "\n".join(matches)
        if truncated:
            output += f"\n...[truncated at {MAX_GLOB_RESULTS} results]"
        return output

    async def grep(
        self,
        pattern: str,
        *,
        path: str = "",
        include: str | None = None,
        case_insensitive: bool = False,
        max_results: int = 100,
    ) -> str:
        if not pattern:
            raise ValueError("Grep pattern must not be empty.")
        effective_max = clamp_grep_results(max_results)
        regex_flags = re.MULTILINE | (re.IGNORECASE if case_insensitive else 0)
        try:
            compiled = re.compile(pattern, regex_flags)
        except re.error:
            compiled = re.compile(re.escape(pattern), regex_flags)

        base = resolve_safe_path(str(self.root), path or ".")
        if not os.path.isdir(base):
            return "(Directory not found)"

        results: list[str] = []
        truncated = False
        file_count = 0

        for current_root, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIR_NAMES)
            for filename in sorted(files):
                if include and not fnmatch.fnmatchcase(filename, include):
                    continue
                full_path = os.path.join(current_root, filename)
                rel_path = os.path.relpath(full_path, str(self.root)).replace(
                    os.sep, "/"
                )
                try:
                    file_size = os.path.getsize(full_path)
                except OSError:
                    continue
                if file_size > MAX_FILE_BYTES:
                    continue
                try:
                    file_handle = open(
                        full_path,
                        "r",
                        encoding="utf-8",
                        errors="replace",
                    )
                except OSError:
                    continue
                file_count += 1
                with file_handle:
                    for line_no, raw_line in enumerate(file_handle, start=1):
                        line = raw_line.rstrip("\r\n")
                        if compiled.search(line):
                            truncated_line = line
                            if len(truncated_line) > MAX_GREP_LINE_CHARS:
                                truncated_line = (
                                    truncated_line[:MAX_GREP_LINE_CHARS] + "..."
                                )
                            results.append(f"{rel_path}:{line_no}: {truncated_line}")
                            if len(results) >= effective_max:
                                truncated = True
                                break
                if truncated:
                    break
            if truncated:
                break

        if not results:
            return f"(No matches in {file_count} files)"
        header = f"[Matches in {file_count} file(s)]"
        output = header + "\n" + "\n".join(results)
        if truncated:
            output += f"\n...[truncated at {effective_max} results]"
        return output

    async def execute(
        self,
        command: str,
        *,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        from agent.modules.agent_runtime import (
            current_session_id_var,
            get_active_session_registry,
        )
        session_id = current_session_id_var.get()
        registry = get_active_session_registry()

        p = subprocess.Popen(
            command,
            shell=True,
            cwd=str(self.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )

        if session_id:
            registry.register_pid(session_id, p.pid)

        try:
            output, error = p.communicate(timeout=timeout)
            exit_code = p.returncode
        except subprocess.TimeoutExpired:
            p.kill()
            output, error = p.communicate()
            exit_code = -1
            error = (error or "") + f"\n[stderr]: Command timed out after {timeout} seconds."
        except Exception as exc:
            p.kill()
            output, error = p.communicate()
            exit_code = -1
            error = (error or "") + f"\n[stderr]: Command failed with exception: {exc}"
        finally:
            if session_id:
                registry.unregister_pid(session_id, p.pid)

        output_str = output or ""
        error_str = error or ""
        combined = output_str + (f"\n[stderr]: {error_str}" if error_str else "")
        truncated = False
        if max_output_chars is not None and len(combined) > max_output_chars:
            combined = combined[:max_output_chars] + "\n...[truncated]"
            truncated = True
        return CommandResult(
            output=combined,
            exit_code=exit_code,
            truncated=truncated,
        )

    async def tree(self, path: str | None = None) -> dict[str, Any]:
        return list_workspace_tree(working_dir=str(self.root), path=path)

    async def file(self, path: str) -> dict[str, Any]:
        return get_workspace_file(working_dir=str(self.root), path=path)

    async def changes(self) -> dict[str, Any]:
        result = get_workspace_changes(str(self.root))
        changes = result.get("changes", [])
        status_by_path: dict[str, dict[str, Any]] = {}
        for change in changes:
            path = change.get("path", "")
            if path:
                status_by_path[path] = change
        self._git_status_cache = status_by_path
        self._git_status_cache_ts = time.monotonic()
        return result

    async def diff(self, path: str) -> dict[str, Any]:
        cached_status = self._git_status_cache
        if cached_status is not None:
            elapsed = time.monotonic() - self._git_status_cache_ts
            if elapsed > GIT_STATUS_CACHE_TTL:
                cached_status = None
        return get_workspace_diff(
            working_dir=str(self.root),
            path=path,
            cached_status=cached_status,
        )

    async def rename(self, *, path: str, new_name: str) -> dict[str, Any]:
        return rename_workspace_entry(
            working_dir=str(self.root),
            path=path,
            new_name=new_name,
        )

    async def delete(self, *, path: str) -> dict[str, Any]:
        return delete_workspace_entry(working_dir=str(self.root), path=path)


__all__ = ["LocalWorkspaceBackend"]
