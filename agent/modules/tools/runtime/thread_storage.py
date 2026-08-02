"""Workspace-scoped storage helpers for the virtual ``.k41-agent`` mount."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from pathlib import Path

from agent.modules.tools.runtime.path_guard import resolve_safe_path
from agent.modules.workspaces import (
    IGNORED_DIR_NAMES,
    MAX_FILE_BYTES,
    MAX_GLOB_RESULTS,
    MAX_GREP_LINE_CHARS,
    MAX_GREP_RESULTS,
    MAX_LIST_FILES_ENTRIES,
    WorkspaceScope,
    WorkspaceFileIO,
    compile_glob_pattern,
    match_glob_path,
)


THREAD_STORAGE_MOUNT = ".k41-agent"
THREAD_STORAGE_DIRS = ("generated-images", "assets", "memory")
THREAD_STORAGE_BASE_DIR = Path.home() / ".k41-agent" / "workspace-storage"

# Module-level caches avoid repeated SHA256/regex work and redundant mkdir
# syscalls on every storage I/O call. The keys are the sanitized storage
# names, which are stable for a given workspace scope.
_workspace_storage_root_cache: dict[str, Path] = {}
_workspace_storage_ready: set[str] = set()
_thread_storage_ready: set[str] = set()

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_WINDOWS_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]+')


def root_thread_id(thread_id: str | None) -> str | None:
    normalized = str(thread_id or "").strip()
    if not normalized:
        return None
    return normalized.split(":sub:", 1)[0]


def sanitize_thread_id(thread_id: str) -> str:
    normalized = str(thread_id or "").strip()
    return sanitize_workspace_key(normalized or "thread")


def sanitize_workspace_key(workspace_key: str) -> str:
    normalized = str(workspace_key or "").strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    cleaned = _WINDOWS_UNSAFE_RE.sub("_", normalized)
    cleaned = re.sub(r"\s+", "_", cleaned).strip(" ._")
    if not cleaned:
        cleaned = "workspace"
    if cleaned.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"workspace-{cleaned}"
    cleaned = cleaned[:80].rstrip(" ._") or "workspace"
    return f"{cleaned}-{digest}"


def workspace_storage_key(workspace_scope: WorkspaceScope | str) -> str:
    if isinstance(workspace_scope, WorkspaceScope):
        key = workspace_scope.key
    else:
        key = str(workspace_scope or "")
    key = key.strip()
    if not key:
        raise ValueError("workspace scope is required for workspace storage.")
    return key


def thread_storage_root(thread_id: str) -> Path:
    root_id = root_thread_id(thread_id)
    if not root_id:
        raise ValueError("thread_id is required for thread storage.")
    return THREAD_STORAGE_BASE_DIR / sanitize_thread_id(root_id)


def workspace_storage_root(workspace_scope: WorkspaceScope | str) -> Path:
    key = sanitize_workspace_key(workspace_storage_key(workspace_scope))
    cached = _workspace_storage_root_cache.get(key)
    if cached is not None:
        return cached
    root = THREAD_STORAGE_BASE_DIR / key
    _workspace_storage_root_cache[key] = root
    return root


def ensure_thread_storage_root(thread_id: str) -> Path:
    root = thread_storage_root(thread_id)
    key = root.name
    if key in _thread_storage_ready:
        return root
    for name in THREAD_STORAGE_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    _thread_storage_ready.add(key)
    return root


def ensure_workspace_storage_root(workspace_scope: WorkspaceScope | str) -> Path:
    root = workspace_storage_root(workspace_scope)
    key = root.name
    if key in _workspace_storage_ready:
        return root
    for name in THREAD_STORAGE_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    _workspace_storage_ready.add(key)
    return root


def generated_images_dir_for_thread(thread_id: str, *, create: bool = True) -> Path:
    root = ensure_thread_storage_root(thread_id) if create else thread_storage_root(thread_id)
    return root / "generated-images"


def generated_images_dir_for_workspace(
    workspace_scope: WorkspaceScope | str,
    *,
    create: bool = True,
) -> Path:
    root = (
        ensure_workspace_storage_root(workspace_scope)
        if create
        else workspace_storage_root(workspace_scope)
    )
    return root / "generated-images"


def virtual_generated_image_path(filename: str) -> str:
    return f"{THREAD_STORAGE_MOUNT}/generated-images/{Path(filename).name}"


def virtual_storage_relative_path(path: str | None) -> str | None:
    raw = str(path or "").replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if raw == THREAD_STORAGE_MOUNT:
        return ""
    prefix = f"{THREAD_STORAGE_MOUNT}/"
    if raw.startswith(prefix):
        return raw[len(prefix) :].lstrip("/")
    return None


def resolve_thread_storage_path(thread_id: str, relative_path: str = ".") -> Path:
    root = ensure_thread_storage_root(thread_id)
    return Path(resolve_safe_path(str(root), relative_path or "."))


def resolve_workspace_storage_path(
    workspace_scope: WorkspaceScope | str,
    relative_path: str = ".",
) -> Path:
    root = ensure_workspace_storage_root(workspace_scope)
    return Path(resolve_safe_path(str(root), relative_path or "."))


def _virtual_path(relative_path: str) -> str:
    normalized = relative_path.replace(os.sep, "/").strip("/")
    if not normalized:
        return f"{THREAD_STORAGE_MOUNT}/"
    return f"{THREAD_STORAGE_MOUNT}/{normalized}"


def _format_storage_matches(matches: list[str], *, truncated: bool, limit: int) -> str:
    if not matches:
        return "(No matches)"
    output = "\n".join(matches)
    if truncated:
        output += f"\n...[truncated at {limit} results]"
    return output


class WorkspaceStorageFileIO:
    """Workspace file I/O wrapper that exposes workspace storage as ``.k41-agent``."""

    def __init__(self, base: WorkspaceFileIO, workspace_scope: WorkspaceScope | str) -> None:
        self._base = base
        self._workspace_scope = workspace_storage_key(workspace_scope)
        self.ref = base.ref

    async def list_dir(self, path: str = "") -> str:
        storage_rel = virtual_storage_relative_path(path)
        if storage_rel is not None:
            return self._list_storage_dir(storage_rel)

        output = await self._base.list_dir(path)
        if str(path or "") not in {"", "."}:
            return output
        entries = [] if output == "(Empty directory)" else output.splitlines()
        if f"{THREAD_STORAGE_MOUNT}/" not in entries:
            entries.append(f"{THREAD_STORAGE_MOUNT}/")
        return "\n".join(entries) if entries else f"{THREAD_STORAGE_MOUNT}/"

    async def read_text(self, file_path: str) -> str:
        storage_rel = virtual_storage_relative_path(file_path)
        if storage_rel is not None:
            path = resolve_workspace_storage_path(self._workspace_scope, storage_rel)
            with open(path, "r", encoding="utf-8") as file_handle:
                return file_handle.read()
        return await self._base.read_text(file_path)

    async def write_text(self, file_path: str, content: str, *, append: bool = False) -> str:
        storage_rel = virtual_storage_relative_path(file_path)
        if storage_rel is not None:
            ensure_workspace_storage_root(self._workspace_scope)
            path = resolve_workspace_storage_path(self._workspace_scope, storage_rel)
            mode = "a" if append else "w"
            with open(path, mode, encoding="utf-8") as file_handle:
                file_handle.write(content)
            return f"[OK] Wrote file: {_virtual_path(storage_rel)}"
        return await self._base.write_text(file_path, content, append=append)

    async def glob(
        self,
        pattern: str,
        *,
        path: str = "",
        include_dirs: bool = False,
    ) -> str:
        storage_path_rel = virtual_storage_relative_path(path)
        storage_pattern_rel = virtual_storage_relative_path(pattern)
        if storage_path_rel is not None or storage_pattern_rel is not None:
            target_rel = storage_path_rel or ""
            target_pattern = storage_pattern_rel if storage_pattern_rel is not None else pattern
            return self._glob_storage(
                target_pattern,
                path=target_rel,
                include_dirs=include_dirs,
            )
        return await self._base.glob(pattern, path=path, include_dirs=include_dirs)

    async def grep(
        self,
        pattern: str,
        *,
        path: str = "",
        include: str | None = None,
        case_insensitive: bool = False,
        max_results: int = 100,
    ) -> str:
        storage_rel = virtual_storage_relative_path(path)
        if storage_rel is not None:
            return self._grep_storage(
                pattern,
                path=storage_rel,
                include=include,
                case_insensitive=case_insensitive,
                max_results=max_results,
            )
        return await self._base.grep(
            pattern,
            path=path,
            include=include,
            case_insensitive=case_insensitive,
            max_results=max_results,
        )

    def _list_storage_dir(self, relative_path: str) -> str:
        target = resolve_workspace_storage_path(self._workspace_scope, relative_path)
        entries: list[str] = []
        truncated = False
        try:
            for entry in sorted(os.listdir(target)):
                if len(entries) >= MAX_LIST_FILES_ENTRIES:
                    truncated = True
                    break
                full_path = target / entry
                entries.append(f"{entry}/" if full_path.is_dir() else entry)
        except FileNotFoundError:
            return "(Directory not found)"
        if not entries:
            return "(Empty directory)"
        output = "\n".join(entries)
        if truncated:
            output += f"\n...[truncated at {MAX_LIST_FILES_ENTRIES} entries]"
        return output

    def _glob_storage(
        self,
        pattern: str,
        *,
        path: str = "",
        include_dirs: bool = False,
    ) -> str:
        root = ensure_workspace_storage_root(self._workspace_scope)
        base = resolve_workspace_storage_path(self._workspace_scope, path or ".")
        if not base.is_dir():
            return "(Directory not found)"

        pattern_regex = compile_glob_pattern(pattern)
        matches: list[str] = []
        truncated = False
        for current_root, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIR_NAMES)
            entries: list[tuple[str, bool]] = [(d, True) for d in dirs]
            entries.extend((f, False) for f in files)
            for name, is_dir in sorted(entries, key=lambda item: (not item[1], item[0].lower())):
                if is_dir and not include_dirs:
                    continue
                full_path = os.path.join(current_root, name)
                candidate_rel = os.path.relpath(full_path, root).replace(os.sep, "/")
                candidate_sub_rel = os.path.relpath(full_path, base).replace(os.sep, "/")
                if match_glob_path(pattern_regex, candidate_rel, candidate_sub_rel, is_dir):
                    suffix = "/" if is_dir else ""
                    matches.append(f"{_virtual_path(candidate_rel)}{suffix}")
                    if len(matches) >= MAX_GLOB_RESULTS:
                        truncated = True
                        break
            if truncated:
                break
        return _format_storage_matches(matches, truncated=truncated, limit=MAX_GLOB_RESULTS)

    def _grep_storage(
        self,
        pattern: str,
        *,
        path: str = "",
        include: str | None = None,
        case_insensitive: bool = False,
        max_results: int = 100,
    ) -> str:
        effective_max = MAX_GREP_RESULTS if max_results <= 0 else min(max_results, MAX_GREP_RESULTS)
        regex_flags = re.MULTILINE | (re.IGNORECASE if case_insensitive else 0)
        try:
            compiled = re.compile(pattern, regex_flags)
        except re.error:
            compiled = re.compile(re.escape(pattern), regex_flags)

        root = ensure_workspace_storage_root(self._workspace_scope)
        base = resolve_workspace_storage_path(self._workspace_scope, path or ".")
        if not base.is_dir():
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
                rel_path = os.path.relpath(full_path, root).replace(os.sep, "/")
                try:
                    file_size = os.path.getsize(full_path)
                except OSError:
                    continue
                if file_size > MAX_FILE_BYTES:
                    continue
                try:
                    file_handle = open(full_path, "r", encoding="utf-8", errors="replace")
                except OSError:
                    continue
                file_count += 1
                with file_handle:
                    for line_no, raw_line in enumerate(file_handle, start=1):
                        line = raw_line.rstrip("\r\n")
                        if compiled.search(line):
                            truncated_line = line
                            if len(truncated_line) > MAX_GREP_LINE_CHARS:
                                truncated_line = truncated_line[:MAX_GREP_LINE_CHARS] + "..."
                            results.append(f"{_virtual_path(rel_path)}:{line_no}: {truncated_line}")
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


ThreadStorageFileIO = WorkspaceStorageFileIO


ThreadStorageFileIO = WorkspaceStorageFileIO  # Backwards-compatible alias.

__all__ = [
    "THREAD_STORAGE_BASE_DIR",
    "THREAD_STORAGE_DIRS",
    "THREAD_STORAGE_MOUNT",
    "WorkspaceStorageFileIO",
    "ensure_thread_storage_root",
    "ensure_workspace_storage_root",
    "generated_images_dir_for_thread",
    "generated_images_dir_for_workspace",
    "resolve_thread_storage_path",
    "resolve_workspace_storage_path",
    "root_thread_id",
    "sanitize_thread_id",
    "sanitize_workspace_key",
    "thread_storage_root",
    "virtual_generated_image_path",
    "virtual_storage_relative_path",
    "workspace_storage_key",
    "workspace_storage_root",
]
