"""Workspace-scoped storage helpers for physical ``.k41-agent`` directory and dual-tier sync."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

from agent.modules.tools.runtime.path_guard import resolve_safe_path
from agent.modules.workspaces import (
    WorkspaceScope,
    WorkspaceFileIO,
)


THREAD_STORAGE_MOUNT = ".k41-agent"
THREAD_STORAGE_DIRS = ("generated-images", "assets", "memory", "uploads", "scratchpad")
THREAD_STORAGE_BASE_DIR = Path.home() / ".k41-agent" / "workspace-storage"
GENERATED_IMAGES_DIR = Path.home() / ".k41-agent" / "generated-images"

# Module-level caches avoid repeated SHA256/regex work and redundant mkdir
# syscalls on every storage I/O call. The keys are the sanitized storage
# names, which are stable for a given workspace scope.
_workspace_storage_root_cache: dict[str, Path] = {}
_workspace_storage_ready: set[str] = set()
_thread_storage_ready: set[str] = set()
_physical_storage_ready: set[str] = set()

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


def ensure_git_exclude(workspace_root: Path | str, pattern: str = f"{THREAD_STORAGE_MOUNT}/") -> None:
    """Ensure a pattern is excluded in .git/info/exclude if .git exists."""
    try:
        git_dir = Path(workspace_root) / ".git"
        if git_dir.is_dir():
            info_dir = git_dir / "info"
            info_dir.mkdir(parents=True, exist_ok=True)
            exclude_file = info_dir / "exclude"
            existing = ""
            if exclude_file.exists():
                existing = exclude_file.read_text(encoding="utf-8", errors="replace")
            normalized_pattern = pattern.strip()
            if normalized_pattern not in existing.splitlines():
                with open(exclude_file, "a", encoding="utf-8") as file_handle:
                    if existing and not existing.endswith("\n"):
                        file_handle.write("\n")
                    file_handle.write(f"{normalized_pattern}\n")
    except OSError:
        pass


def ensure_physical_workspace_storage(workspace_root: Path | str) -> Path:
    """Ensure physical .k41-agent directory exists inside workspace and is git-excluded."""
    root = Path(workspace_root)
    key = str(root.resolve())
    target = root / THREAD_STORAGE_MOUNT
    if key not in _physical_storage_ready:
        for name in THREAD_STORAGE_DIRS:
            (target / name).mkdir(parents=True, exist_ok=True)
        ensure_git_exclude(root, f"{THREAD_STORAGE_MOUNT}/")
        _physical_storage_ready.add(key)
    return target


def hydrate_workspace_storage(
    workspace_root: Path | str,
    workspace_scope: WorkspaceScope | str,
) -> None:
    """Hydrate persistent host storage files into the physical workspace."""
    source_root = ensure_workspace_storage_root(workspace_scope)
    target_root = ensure_physical_workspace_storage(workspace_root)
    if not source_root.is_dir():
        return
    for current_root, _dirs, files in os.walk(source_root):
        rel_dir = os.path.relpath(current_root, source_root)
        dest_dir = target_root if rel_dir == "." else target_root / rel_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        for filename in files:
            src_file = Path(current_root) / filename
            dest_file = dest_dir / filename
            if not dest_file.exists() or src_file.stat().st_mtime > dest_file.stat().st_mtime:
                try:
                    shutil.copy2(src_file, dest_file)
                except OSError:
                    pass


def sync_back_workspace_storage(
    workspace_root: Path | str,
    workspace_scope: WorkspaceScope | str,
) -> None:
    """Sync files from physical workspace storage back to persistent host storage."""
    source_root = Path(workspace_root) / THREAD_STORAGE_MOUNT
    if not source_root.is_dir():
        return
    dest_root = ensure_workspace_storage_root(workspace_scope)
    for current_root, _dirs, files in os.walk(source_root):
        rel_dir = os.path.relpath(current_root, source_root)
        target_dir = dest_root if rel_dir == "." else dest_root / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for filename in files:
            src_file = Path(current_root) / filename
            target_file = target_dir / filename
            if not target_file.exists() or src_file.stat().st_mtime > target_file.stat().st_mtime:
                try:
                    shutil.copy2(src_file, target_file)
                except OSError:
                    pass


def save_generated_image(
    filename: str,
    image_bytes: bytes,
    workspace_scope: WorkspaceScope | str | None = None,
    workspace_root: Path | str | None = None,
) -> Path:
    """Save a generated image to persistent storage and physical workspace."""
    if workspace_scope:
        storage_dir = generated_images_dir_for_workspace(workspace_scope)
    else:
        storage_dir = GENERATED_IMAGES_DIR
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = storage_dir / filename
    storage_path.write_bytes(image_bytes)

    if workspace_root:
        try:
            ws_images_dir = ensure_physical_workspace_storage(workspace_root) / "generated-images"
            ws_images_dir.mkdir(parents=True, exist_ok=True)
            ws_path = ws_images_dir / filename
            ws_path.write_bytes(image_bytes)
        except OSError:
            pass

    return storage_path


def ingest_attachment_file(
    filename: str,
    content_bytes: bytes,
    workspace_scope: WorkspaceScope | str | None = None,
    workspace_root: Path | str | None = None,
) -> Path:
    """Save an uploaded attachment to persistent storage and physical workspace."""
    if workspace_scope:
        storage_dir = ensure_workspace_storage_root(workspace_scope) / "uploads"
    else:
        storage_dir = THREAD_STORAGE_BASE_DIR / "uploads"
    storage_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = Path(filename).name
    storage_path = storage_dir / safe_filename
    storage_path.write_bytes(content_bytes)

    if workspace_root:
        try:
            ws_uploads_dir = ensure_physical_workspace_storage(workspace_root) / "uploads"
            ws_uploads_dir.mkdir(parents=True, exist_ok=True)
            ws_path = ws_uploads_dir / safe_filename
            ws_path.write_bytes(content_bytes)
        except OSError:
            pass

    return storage_path


class WorkspaceStorageFileIO:
    """Workspace file I/O wrapper ensuring physical workspace storage."""

    def __init__(self, base: WorkspaceFileIO, workspace_scope: WorkspaceScope | str | None = None) -> None:
        self._base = base
        self._workspace_scope = workspace_storage_key(workspace_scope) if workspace_scope else None
        self.ref = base.ref
        if hasattr(base, "root") and base.root:
            ensure_physical_workspace_storage(base.root)

    async def list_dir(self, path: str = "") -> str:
        return await self._base.list_dir(path)

    async def read_text(self, file_path: str) -> str:
        return await self._base.read_text(file_path)

    async def write_text(self, file_path: str, content: str, *, append: bool = False) -> str:
        return await self._base.write_text(file_path, content, append=append)

    async def glob(
        self,
        pattern: str,
        *,
        path: str = "",
        include_dirs: bool = False,
    ) -> str:
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
        return await self._base.grep(
            pattern,
            path=path,
            include=include,
            case_insensitive=case_insensitive,
            max_results=max_results,
        )


ThreadStorageFileIO = WorkspaceStorageFileIO  # Backwards-compatible alias.

__all__ = [
    "GENERATED_IMAGES_DIR",
    "THREAD_STORAGE_BASE_DIR",
    "THREAD_STORAGE_DIRS",
    "THREAD_STORAGE_MOUNT",
    "ThreadStorageFileIO",
    "WorkspaceStorageFileIO",
    "ensure_git_exclude",
    "ensure_physical_workspace_storage",
    "ensure_thread_storage_root",
    "ensure_workspace_storage_root",
    "generated_images_dir_for_thread",
    "generated_images_dir_for_workspace",
    "hydrate_workspace_storage",
    "ingest_attachment_file",
    "resolve_thread_storage_path",
    "resolve_workspace_storage_path",
    "root_thread_id",
    "sanitize_thread_id",
    "sanitize_workspace_key",
    "save_generated_image",
    "sync_back_workspace_storage",
    "thread_storage_root",
    "virtual_generated_image_path",
    "virtual_storage_relative_path",
    "workspace_storage_key",
    "workspace_storage_root",
]
