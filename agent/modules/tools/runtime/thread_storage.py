"""Workspace-scoped storage helpers for physical ``.k41-agent`` directory and dual-tier sync."""

from __future__ import annotations

import base64
import hashlib
import inspect
import logging
import os
import re
import shutil
import shlex
from pathlib import Path

from agent.modules.tools.runtime.path_guard import resolve_safe_path
from agent.modules.workspaces import (
    WORKSPACE_STORAGE_EXCLUDE_COMMAND,
    UnsupportedWorkspaceCapabilityError,
    WorkspaceScope,
    WorkspaceFileIO,
)

logger = logging.getLogger(__name__)


THREAD_STORAGE_MOUNT = ".k41-agent"
THREAD_STORAGE_DIRS = ("scratchpad", "outputs", "uploads", "generated-images")
SYNC_STORAGE_DIRS = frozenset({"scratchpad", "uploads", "generated-images"})
THREAD_STORAGE_BASE_DIR = Path.home() / ".k41-agent" / "workspace-storage"
GENERATED_IMAGES_DIR = Path.home() / ".k41-agent" / "generated-images"
_SANDBOX_BACKENDS = frozenset({"daytona", "modal"})

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
    cache_key = str(THREAD_STORAGE_BASE_DIR / key)
    cached = _workspace_storage_root_cache.get(cache_key)
    if cached is not None:
        return cached
    root = THREAD_STORAGE_BASE_DIR / key
    _workspace_storage_root_cache[cache_key] = root
    return root


def ensure_thread_storage_root(thread_id: str) -> Path:
    root = thread_storage_root(thread_id)
    key = str(root)
    if key in _thread_storage_ready:
        return root
    root.mkdir(parents=True, exist_ok=True)
    _thread_storage_ready.add(key)
    return root


def ensure_workspace_storage_root(workspace_scope: WorkspaceScope | str) -> Path:
    root = workspace_storage_root(workspace_scope)
    key = str(root)
    if key in _workspace_storage_ready:
        return root
    root.mkdir(parents=True, exist_ok=True)
    _workspace_storage_ready.add(key)
    return root


def generated_images_dir_for_thread(thread_id: str, *, create: bool = True) -> Path:
    root = ensure_thread_storage_root(thread_id) if create else thread_storage_root(thread_id)
    target = root / "generated-images"
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


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
    target = root / "generated-images"
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


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
    from agent.modules.tools.coding.storage import ensure_workspace_exclude

    if pattern == f"{THREAD_STORAGE_MOUNT}/":
        ensure_workspace_exclude(Path(workspace_root))



def ensure_physical_workspace_storage(workspace_root: Path | str) -> Path:
    """Return the storage path and register Git exclusion without precreating directories."""
    root = Path(workspace_root)
    key = str(root.resolve())
    target = root / THREAD_STORAGE_MOUNT
    if key not in _physical_storage_ready:
        ensure_git_exclude(root, f"{THREAD_STORAGE_MOUNT}/")
        _physical_storage_ready.add(key)
    return target


def managed_storage_walk(root: Path):
    """Walk recognized persistent categories only, without following symlinks."""
    if not root.is_dir() or root.is_symlink():
        return
    for category in sorted(SYNC_STORAGE_DIRS):
        base = root / category
        if not base.is_dir() or base.is_symlink():
            continue
        for directory, dirs, files in os.walk(base):
            dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
            yield directory, dirs, [name for name in files if not (Path(directory) / name).is_symlink()]


def clear_persistent_scratchpads(thread_id: str) -> None:
    """Remove only this conversation's managed notes from host mirrors."""
    from agent.modules.tools.coding.storage import conversation_key
    if not THREAD_STORAGE_BASE_DIR.is_dir():
        return
    for root in THREAD_STORAGE_BASE_DIR.iterdir():
        target = root / "scratchpad" / conversation_key(thread_id)
        if (root.is_dir() and not root.is_symlink() and target.is_dir() and not target.is_symlink()
                and target.resolve().is_relative_to(root.resolve())):
            shutil.rmtree(target)


def hydrate_workspace_storage(
    workspace_root: Path | str,
    workspace_scope: WorkspaceScope | str,
) -> None:
    """Hydrate persistent host storage files into the physical workspace."""
    source_root = ensure_workspace_storage_root(workspace_scope)
    target_root = ensure_physical_workspace_storage(workspace_root)
    if not source_root.is_dir():
        return
    for current_root, _dirs, files in managed_storage_walk(source_root):
        rel_dir = os.path.relpath(current_root, source_root)
        dest_dir = target_root if rel_dir == "." else target_root / rel_dir
        for filename in files:
            src_file = Path(current_root) / filename
            dest_file = dest_dir / filename
            dest_dir.mkdir(parents=True, exist_ok=True)
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
    for current_root, _dirs, files in managed_storage_walk(source_root):
        rel_dir = os.path.relpath(current_root, source_root)
        target_dir = dest_root if rel_dir == "." else dest_root / rel_dir
        for filename in files:
            src_file = Path(current_root) / filename
            target_file = target_dir / filename
            target_dir.mkdir(parents=True, exist_ok=True)
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


async def ensure_sandbox_workspace_storage(
    workspace: object | None,
    *,
    thread_id: str | None = None,
) -> None:
    """Register internal storage Git exclusion inside a sandbox workspace.

    For ``daytona``/``modal`` backends the physical directory lives inside the
    remote sandbox and must be created via the workspace file I/O backend. This
    is a no-op for local workspaces where :func:`ensure_physical_workspace_storage`
    already handles the local filesystem.
    """
    if workspace is None:
        return
    try:
        from agent.modules.workspaces import get_workspace_file_io, resolve_workspace_ref

        ref = resolve_workspace_ref(workspace)
        if ref.backend not in _SANDBOX_BACKENDS:
            return
        file_io = await get_workspace_file_io(ref, thread_id=thread_id)
        # Do not create an internal tree, or a .git tree in a non-repository.
        try:
            executor = getattr(file_io, "execute", None)
            if callable(executor):
                await executor(
                    WORKSPACE_STORAGE_EXCLUDE_COMMAND,
                    timeout=10,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to ensure sandbox git exclude: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.debug("ensure_sandbox_workspace_storage failed: %s", exc)


async def ingest_attachment_file_to_sandbox(
    filename: str,
    content_bytes: bytes,
    workspace: object | None,
    *,
    thread_id: str | None = None,
) -> bool:
    """Write an attachment file into a sandbox's ``.k41-agent/uploads``.

    Returns ``True`` when the file was successfully written to the remote
    sandbox, ``False`` otherwise. The caller is still responsible for writing
    to the persistent host storage via :func:`ingest_attachment_file`.
    """
    if workspace is None or not content_bytes:
        return False
    try:
        from agent.modules.workspaces import (
            get_workspace_file_io,
            resolve_remote_path,
            resolve_workspace_ref,
        )

        ref = resolve_workspace_ref(workspace)
        if ref.backend not in _SANDBOX_BACKENDS:
            return False
        file_io = await get_workspace_file_io(ref, thread_id=thread_id)
        raw_name = str(filename).replace("\\", "/").lstrip("/")
        if raw_name.startswith(f"{THREAD_STORAGE_MOUNT}/"):
            raw_name = raw_name[len(THREAD_STORAGE_MOUNT) + 1:]
        if any(part in {"..", "."} for part in raw_name.split("/")):
            raise ValueError("Attachment path escapes workspace storage.")
        if "/" in raw_name:
            # Preserve sub-directory (e.g. generated-images/foo.png)
            rel = f"{THREAD_STORAGE_MOUNT}/{raw_name}"
            safe_name = Path(raw_name).name
            parent_rel = f"{THREAD_STORAGE_MOUNT}/{Path(raw_name).parent.as_posix()}"
        else:
            safe_name = Path(raw_name).name
            rel = f"{THREAD_STORAGE_MOUNT}/uploads/{safe_name}"
            parent_rel = f"{THREAD_STORAGE_MOUNT}/uploads"
        # Ensure parent exists.
        try:
            executor = getattr(file_io, "execute", None)
            if callable(executor):
                await executor(f"mkdir -p {shlex.quote(parent_rel)}", timeout=10)
        except Exception:
            pass

        root = getattr(file_io, "root", None) or str(ref.metadata.get("root") or "")
        if not root:
            root = "/workspace" if ref.backend == "modal" else "workspace"
        try:
            abs_path = resolve_remote_path(str(root), rel)
        except Exception:
            abs_path = f"{str(root).rstrip('/')}/{rel}"

        uploader = getattr(file_io, "_upload_file", None)
        if callable(uploader):
            try:
                result = uploader(content_bytes, abs_path)
                if inspect.isawaitable(result):
                    await result
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("Sandbox _upload_file failed for %s: %s", rel, exc)

        # Fallback: text write for utf-8 content.
        if b"\x00" not in content_bytes:
            try:
                text = content_bytes.decode("utf-8")
                await file_io.write_text(rel, text, append=False)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("Sandbox write_text fallback failed for %s: %s", rel, exc)

        # Final fallback: base64 via shell (handles binary). Write via a temp
        # file then decode to avoid shell quoting limits for large payloads.
        try:
            b64 = base64.b64encode(content_bytes).decode("ascii")
            tmp_rel = f"{parent_rel}/.tmp_{safe_name}.b64"
            # Write base64 chunks via the file API then decode inside sandbox.
            await file_io.write_text(tmp_rel, b64, append=False)
            executor = getattr(file_io, "execute", None)
            if callable(executor):
                await executor(
                    f"base64 -d {shlex.quote(tmp_rel)} > {shlex.quote(rel)} && rm -f {shlex.quote(tmp_rel)}",
                    timeout=30,
                )
            else:
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Sandbox base64 fallback failed for %s: %s", rel, exc)
            return False
    except Exception as exc:  # noqa: BLE001
        logger.debug("ingest_attachment_file_to_sandbox failed for %s: %s", filename, exc)
        return False


async def hydrate_workspace_storage_to_sandbox(
    workspace: object | None,
    workspace_scope: WorkspaceScope | str | None,
    *,
    thread_id: str | None = None,
) -> None:
    """Hydrate persistent host storage into a sandbox's physical ``.k41-agent``.

    Mirrors :func:`hydrate_workspace_storage` but uses the sandbox file I/O
    backend instead of local ``shutil`` copies.
    """
    if workspace is None or workspace_scope is None:
        return
    try:
        from agent.modules.workspaces import get_workspace_file_io, resolve_workspace_ref

        ref = resolve_workspace_ref(workspace)
        if ref.backend not in _SANDBOX_BACKENDS:
            return
        source_root = ensure_workspace_storage_root(workspace_scope)
        if not source_root.is_dir():
            return
        file_io = await get_workspace_file_io(ref, thread_id=thread_id)
        for current_root, _dirs, files in managed_storage_walk(source_root):
            rel_dir = os.path.relpath(current_root, source_root)
            for filename in files:
                src_file = Path(current_root) / filename
                rel_path = filename if rel_dir == "." else f"{rel_dir}/{filename}"
                target_rel = f"{THREAD_STORAGE_MOUNT}/{rel_path}"
                try:
                    content = src_file.read_bytes()
                    await ingest_attachment_file_to_sandbox(
                        target_rel, content, workspace, thread_id=thread_id
                    )
                except OSError:
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Hydrate to sandbox failed for %s: %s", target_rel, exc)
    except Exception as exc:  # noqa: BLE001
        logger.debug("hydrate_workspace_storage_to_sandbox failed: %s", exc)


class WorkspaceStorageFileIO:
    """Workspace file I/O wrapper ensuring physical workspace storage."""

    def __init__(self, base: WorkspaceFileIO, workspace_scope: WorkspaceScope | str | None = None) -> None:
        self._base = base
        self._workspace_scope = workspace_storage_key(workspace_scope) if workspace_scope else None
        self.ref = base.ref
        if getattr(base.ref, "backend", "local") == "local" and hasattr(base, "root") and base.root:
            ensure_physical_workspace_storage(base.root)

    async def list_dir(self, path: str = "") -> str:
        return await self._base.list_dir(path)

    async def read_text(self, file_path: str) -> str:
        return await self._base.read_text(file_path)

    async def read_bytes(self, file_path: str) -> bytes:
        reader = getattr(self._base, "read_bytes", None)
        if callable(reader):
            result = reader(file_path)
            if inspect.isawaitable(result):
                return await result
            return result
        raise UnsupportedWorkspaceCapabilityError(
            backend=getattr(getattr(self._base, "ref", None), "backend", "unknown"),
            capability="read_bytes",
        )

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
    "ensure_sandbox_workspace_storage",
    "ensure_thread_storage_root",
    "ensure_workspace_storage_root",
    "generated_images_dir_for_thread",
    "generated_images_dir_for_workspace",
    "hydrate_workspace_storage",
    "hydrate_workspace_storage_to_sandbox",
    "ingest_attachment_file",
    "ingest_attachment_file_to_sandbox",
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
