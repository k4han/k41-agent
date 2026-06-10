from __future__ import annotations

import inspect
import json
import logging
import mimetypes
import posixpath
import shlex
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from agent.modules.workspaces.backends import CommandResult, WorkspaceUnavailableError
from agent.modules.workspaces.constants import (
    GIT_TIMEOUT_SECONDS,
    IGNORED_DIR_NAMES,
    MAX_FILE_BYTES,
    MAX_LIST_FILES_ENTRIES,
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
    git_relative_path,
    git_status_args,
    parse_git_status,
    truncate_diff,
)
from agent.modules.workspaces.posix_utils import normalize_posix_path
from agent.modules.workspaces.refs import WorkspaceRef
from agent.modules.workspaces.sandbox_backend import SandboxBackendBase
from agent.modules.workspaces.search_utils import (
    build_sandbox_glob_command,
    build_sandbox_grep_command,
    render_sandbox_glob_output,
    render_sandbox_grep_output,
)

logger = logging.getLogger(__name__)

MICROSANDBOX_BACKEND = "microsandbox"
DEFAULT_MICROSANDBOX_ROOT = "/workspace"
DEFAULT_MICROSANDBOX_IMAGE = "python:3.13-slim"
DEFAULT_MICROSANDBOX_CPUS = 1
DEFAULT_MICROSANDBOX_MEMORY = 512
DEFAULT_MICROSANDBOX_START_TIMEOUT_SECONDS = 30
DEFAULT_MICROSANDBOX_STOP_TIMEOUT_SECONDS = 10

def _config() -> dict[str, Any]:
    from agent.shared.config.service import get_config_service

    service = get_config_service()
    root = (
        service.get_str(
            "workspace.microsandbox.default_root",
            DEFAULT_MICROSANDBOX_ROOT,
        ).strip()
        or DEFAULT_MICROSANDBOX_ROOT
    )
    image = (
        service.get_str(
            "workspace.microsandbox.image",
            DEFAULT_MICROSANDBOX_IMAGE,
        ).strip()
        or DEFAULT_MICROSANDBOX_IMAGE
    )
    return {
        "enabled": service.get_bool("workspace.microsandbox.enabled", False),
        "default_root": resolve_microsandbox_path("/", root),
        "image": image,
        "cpus": max(
            1,
            service.get_int("workspace.microsandbox.cpus", DEFAULT_MICROSANDBOX_CPUS),
        ),
        "memory": max(
            128,
            service.get_int(
                "workspace.microsandbox.memory",
                DEFAULT_MICROSANDBOX_MEMORY,
            ),
        ),
        "max_duration_seconds": max(
            0,
            service.get_int("workspace.microsandbox.max_duration_seconds", 0),
        ),
        "idle_timeout_seconds": max(
            0,
            service.get_int("workspace.microsandbox.idle_timeout_seconds", 0),
        ),
        "start_timeout_seconds": max(
            1,
            service.get_int(
                "workspace.microsandbox.start_timeout_seconds",
                DEFAULT_MICROSANDBOX_START_TIMEOUT_SECONDS,
            ),
        ),
        "stop_timeout_seconds": max(
            1,
            service.get_int(
                "workspace.microsandbox.stop_timeout_seconds",
                DEFAULT_MICROSANDBOX_STOP_TIMEOUT_SECONDS,
            ),
        ),
        "replace_existing": service.get_bool(
            "workspace.microsandbox.replace_existing",
            False,
        ),
    }


def get_microsandbox_module():
    try:
        import microsandbox
    except ImportError as exc:
        raise RuntimeError(
            "The Microsandbox Python SDK is not installed. "
            "Install project dependencies with uv."
        ) from exc
    return microsandbox


def _require_enabled() -> dict[str, Any]:
    cfg = _config()
    if not cfg["enabled"]:
        raise ValueError("Microsandbox workspace backend is disabled.")
    return cfg


def _sandbox_name(label: str | None = None) -> str:
    raw = str(label or "").strip().lower()
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_"} else "-"
        for char in raw
    ).strip("-_")
    prefix = cleaned[:64] if cleaned else "k41-agent"
    return f"{prefix}-{uuid.uuid4().hex[:12]}"[:128]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_ms_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        timestamp = float(value) / 1000.0
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _normalize_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "unknown"
    if raw in {"running", "active", "started"}:
        return "started"
    if raw in {"starting"}:
        return "starting"
    if raw in {"stopping", "stopped"}:
        return "stopped"
    if raw in {"draining", "paused"}:
        return raw
    if raw in {"crashed", "error", "failed"}:
        return "error"
    if raw in {"destroyed", "deleted", "removed"}:
        return "destroyed"
    return raw


def microsandbox_metadata(
    *,
    root: str | None = None,
    status: str | None = None,
    image: str | None = None,
    touch: bool = False,
    started: bool = False,
    stopped: bool = False,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    now = _utcnow_iso()
    if root is not None:
        metadata["root"] = resolve_microsandbox_path("/", root)
    if status is not None:
        metadata["status"] = _normalize_status(status)
    if image is not None:
        metadata["image"] = image
    if touch:
        metadata["last_used_at"] = now
    if started:
        metadata["last_started_at"] = now
    if stopped:
        metadata["last_stopped_at"] = now
    return metadata


def _is_microsandbox_unavailable_error(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return False
    class_name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    if "notfound" in class_name or "not_found" in class_name:
        return True
    if "sandbox" in message and "not found" in message:
        return True
    if "no such sandbox" in message:
        return True
    if "failed to connect" in message and "portal" in message:
        return True
    if "not running" in message and "sandbox" in message:
        return True
    return False


def _raise_if_microsandbox_unavailable(ref: WorkspaceRef, exc: Exception) -> None:
    if not _is_microsandbox_unavailable_error(exc):
        return
    raise WorkspaceUnavailableError(
        (
            f"Microsandbox workspace {ref.locator} is no longer available. "
            "Create or attach a new Microsandbox workspace for this thread."
        ),
        backend=MICROSANDBOX_BACKEND,
        locator=ref.locator,
    ) from exc


def resolve_microsandbox_path(root: str, path: str | None = None) -> str:
    normalized_root = normalize_posix_path(root)
    if not posixpath.isabs(normalized_root):
        normalized_root = normalize_posix_path(f"/{normalized_root}")
    raw_path = str(path or "").strip()
    if not raw_path or raw_path == ".":
        return normalized_root
    if any(part == ".." for part in raw_path.replace("\\", "/").split("/")):
        raise ValueError(f"Path escapes workspace: {path}")

    normalized_input = normalize_posix_path(raw_path)
    if normalized_root == "/":
        if posixpath.isabs(normalized_input):
            return normalized_input
        return normalize_posix_path(posixpath.join(normalized_root, normalized_input))
    if normalized_input == normalized_root or normalized_input.startswith(
        f"{normalized_root}/"
    ):
        target = normalized_input
    elif posixpath.isabs(normalized_input):
        raise ValueError(f"Path escapes workspace: {path}")
    else:
        target = normalize_posix_path(
            posixpath.join(normalized_root, normalized_input)
        )

    if target != normalized_root and not target.startswith(f"{normalized_root}/"):
        raise ValueError(f"Path escapes workspace: {path}")
    return target


def microsandbox_relative_path(root: str, path: str) -> str:
    normalized_root = resolve_microsandbox_path("/", root)
    target = resolve_microsandbox_path(normalized_root, path)
    if target == normalized_root:
        return ""
    if normalized_root == "/":
        return target.lstrip("/")
    return target[len(normalized_root) + 1 :]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _detach_sandbox(sandbox: Any) -> None:
    detacher = getattr(sandbox, "detach", None)
    if not callable(detacher):
        return
    try:
        await _maybe_await(detacher())
    except Exception as exc:
        logger.debug("Failed to detach Microsandbox handle: %s", exc)


async def get_microsandbox_sandbox(ref: WorkspaceRef) -> Any:
    if ref.backend != MICROSANDBOX_BACKEND:
        raise ValueError(f"Unsupported workspace backend: {ref.backend}")
    cfg = _require_enabled()
    module = get_microsandbox_module()
    try:
        handle = await _maybe_await(module.Sandbox.get(ref.locator))
    except Exception as exc:
        _raise_if_microsandbox_unavailable(ref, exc)
        raise

    status = _normalize_status(getattr(handle, "status", ""))
    try:
        if status == "stopped":
            return await _maybe_await(handle.start(detached=True))
        return await _maybe_await(
            handle.connect(timeout=float(cfg["start_timeout_seconds"]))
        )
    except Exception as exc:
        _raise_if_microsandbox_unavailable(ref, exc)
        raise


async def create_microsandbox_workspace(*, label: str | None = None) -> WorkspaceRef:
    cfg = _require_enabled()
    module = get_microsandbox_module()
    sandbox_name = _sandbox_name(label)
    create_kwargs: dict[str, Any] = {
        "image": cfg["image"],
        "cpus": cfg["cpus"],
        "memory": cfg["memory"],
        "workdir": cfg["default_root"],
        "detached": True,
        "replace": cfg["replace_existing"],
    }
    if cfg["max_duration_seconds"] > 0:
        create_kwargs["max_duration"] = float(cfg["max_duration_seconds"])
    if cfg["idle_timeout_seconds"] > 0:
        create_kwargs["idle_timeout"] = float(cfg["idle_timeout_seconds"])

    sandbox = await _maybe_await(module.Sandbox.create(sandbox_name, **create_kwargs))
    ref = WorkspaceRef(
        backend=MICROSANDBOX_BACKEND,
        locator=sandbox_name,
        label=(label or "").strip() or f"microsandbox:{sandbox_name}",
        metadata=microsandbox_metadata(
            root=cfg["default_root"],
            status="started",
            image=cfg["image"],
            touch=True,
            started=True,
        ),
    )
    try:
        backend = MicrosandboxWorkspaceBackend(ref, sandbox=sandbox)
        await backend.ensure_root()
        await backend.ensure_git()
    finally:
        await _detach_sandbox(sandbox)
    return ref


async def attach_microsandbox_workspace(
    sandbox_id: str,
    *,
    label: str | None = None,
    root: str | None = None,
) -> WorkspaceRef:
    cfg = _require_enabled()
    normalized_sandbox_id = str(sandbox_id or "").strip()
    if not normalized_sandbox_id:
        raise ValueError("Microsandbox name is required.")
    selected_root = (root or "").strip() or cfg["default_root"]
    ref = WorkspaceRef(
        backend=MICROSANDBOX_BACKEND,
        locator=normalized_sandbox_id,
        label=(label or "").strip() or f"microsandbox:{normalized_sandbox_id}",
        metadata=microsandbox_metadata(
            root=selected_root,
            status="started",
            image=cfg["image"],
            touch=True,
        ),
    )
    backend = await MicrosandboxWorkspaceBackend.create(ref)
    try:
        await backend.ensure_root()
        await backend.ensure_git()
    finally:
        await _detach_sandbox(backend.sandbox)
    return ref


async def create_microsandbox_backend(
    ref: WorkspaceRef,
    *,
    thread_id: str | None = None,
) -> MicrosandboxWorkspaceBackend:
    del thread_id
    return await MicrosandboxWorkspaceBackend.create(ref)


async def stop_microsandbox_workspace(ref: WorkspaceRef) -> str:
    if ref.backend != MICROSANDBOX_BACKEND:
        raise ValueError(f"Unsupported workspace backend: {ref.backend}")
    cfg = _require_enabled()
    module = get_microsandbox_module()
    try:
        handle = await _maybe_await(module.Sandbox.get(ref.locator))
    except Exception as exc:
        _raise_if_microsandbox_unavailable(ref, exc)
        raise
    status = _normalize_status(getattr(handle, "status", ""))
    if status != "stopped":
        await _maybe_await(handle.stop(timeout=float(cfg["stop_timeout_seconds"])))
        status = "stopped"
    ref.metadata.update(
        microsandbox_metadata(
            root=str(ref.metadata.get("root") or DEFAULT_MICROSANDBOX_ROOT),
            status=status,
            stopped=True,
        )
    )
    return status


async def delete_microsandbox_workspace(ref: WorkspaceRef) -> str:
    if ref.backend != MICROSANDBOX_BACKEND:
        raise ValueError(f"Unsupported workspace backend: {ref.backend}")
    cfg = _require_enabled()
    module = get_microsandbox_module()
    try:
        handle = await _maybe_await(module.Sandbox.get(ref.locator))
    except Exception as exc:
        if _is_microsandbox_unavailable_error(exc):
            return "removed"
        raise
    status = _normalize_status(getattr(handle, "status", ""))
    if status != "stopped":
        try:
            await _maybe_await(handle.stop(timeout=float(cfg["stop_timeout_seconds"])))
        except Exception as exc:
            logger.debug("Microsandbox stop before remove failed: %s", exc)
            killer = getattr(handle, "kill", None)
            if callable(killer):
                await _maybe_await(killer(timeout=float(cfg["stop_timeout_seconds"])))
    remover = getattr(handle, "remove", None)
    if callable(remover):
        await _maybe_await(remover())
    else:
        await _maybe_await(module.Sandbox.remove(ref.locator))
    ref.metadata.update(
        microsandbox_metadata(
            root=str(ref.metadata.get("root") or DEFAULT_MICROSANDBOX_ROOT),
            status="destroyed",
        )
    )
    return "removed"


async def list_microsandbox_sandboxes() -> list[dict[str, Any]]:
    try:
        _require_enabled()
        module = get_microsandbox_module()
        handles = await _maybe_await(module.Sandbox.list())
    except Exception as exc:
        logger.debug("Microsandbox list unavailable: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for handle in handles:
        sandbox_name = str(getattr(handle, "name", "") or "").strip()
        if not sandbox_name:
            continue
        config: dict[str, Any] = {}
        config_json = str(getattr(handle, "config_json", "") or "")
        if config_json:
            try:
                parsed = json.loads(config_json)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                config = parsed
        status = _normalize_status(getattr(handle, "status", ""))
        root = str(config.get("workdir") or DEFAULT_MICROSANDBOX_ROOT).strip()
        created_at = _timestamp_ms_to_iso(getattr(handle, "created_at", None))
        updated_at = _timestamp_ms_to_iso(getattr(handle, "updated_at", None))
        metadata = {
            "root": resolve_microsandbox_path("/", root),
            "status": status,
            "image": config.get("image") or config.get("image_ref"),
            "config": config,
        }
        results.append(
            {
                "sandbox_id": sandbox_name,
                "backend": MICROSANDBOX_BACKEND,
                "label": f"microsandbox:{sandbox_name}",
                "root": metadata["root"],
                "status": status,
                "thread_id": None,
                "repository_full_name": None,
                "last_used_at": None,
                "last_started_at": created_at if status == "started" else None,
                "last_stopped_at": updated_at if status == "stopped" else None,
                "last_archived_at": None,
                "created_at": created_at,
                "updated_at": updated_at,
                "on_cloud": status not in {"destroyed"},
                "is_orphan": True,
                "metadata": metadata,
            }
        )
    return results


class MicrosandboxWorkspaceBackend(SandboxBackendBase):
    def __init__(self, ref: WorkspaceRef, *, sandbox: Any) -> None:
        super().__init__(ref)
        self.sandbox = sandbox
        self.fs = sandbox.fs
        self.root = resolve_microsandbox_path(
            "/",
            str(ref.metadata.get("root") or DEFAULT_MICROSANDBOX_ROOT),
        )
        self.ref.metadata.update(microsandbox_metadata(root=self.root, touch=True))

    @classmethod
    async def create(cls, ref: WorkspaceRef) -> MicrosandboxWorkspaceBackend:
        sandbox = await get_microsandbox_sandbox(ref)
        return cls(ref, sandbox=sandbox)

    def ensure_active(self) -> None:
        self.touch()

    async def ensure_root(self) -> None:
        await self._make_directory(self.root)

    async def ensure_git(self) -> None:
        check = await self._exec("command -v git", cwd="/", timeout=5)
        if check.exit_code == 0:
            return
        install = await self._exec(
            (
                "(apt-get update -qq && apt-get install -y -qq git) "
                "|| (apk add --no-cache git) "
                "|| (dnf install -y git)"
            ),
            cwd="/",
            timeout=180,
        )
        if install.exit_code not in (0, None):
            logger.warning(
                "Failed to install git in Microsandbox %s: %s",
                self.ref.locator,
                install.output.strip(),
            )

    def touch(self) -> None:
        self.ref.metadata.update(microsandbox_metadata(root=self.root, touch=True))

    async def _run_remote_aio(self, coro_fn: Callable[[], Any]) -> Any:
        try:
            return await _maybe_await(coro_fn())
        except Exception as exc:
            _raise_if_microsandbox_unavailable(self.ref, exc)
            raise

    async def list_dir(self, path: str = "") -> str:
        self.touch()
        target = resolve_microsandbox_path(self.root, path or ".")
        cached, value = self._get_metadata_cache("list_dir", target)
        if cached:
            return value
        command = f"ls -1 {shlex.quote(target)}"
        result = await self._exec(command, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            raise RuntimeError(
                result.output.strip() or "Failed to list Microsandbox directory."
            )
        entries = [line for line in result.output.splitlines() if line.strip()]
        truncated = len(entries) > MAX_LIST_FILES_ENTRIES
        entries = entries[:MAX_LIST_FILES_ENTRIES]
        if not entries:
            return "(Empty directory)"
        output = "\n".join(entries)
        if truncated:
            output += f"\n...[truncated at {MAX_LIST_FILES_ENTRIES} entries]"
        self._set_metadata_cache("list_dir", output, target)
        return output

    async def read_text(self, file_path: str) -> str:
        self.touch()
        return await self._run_remote_aio(
            lambda: self.fs.read_text(resolve_microsandbox_path(self.root, file_path))
        )

    async def write_text(self, file_path: str, content: str) -> str:
        self.touch()
        self._invalidate_workspace_caches()
        remote_path = resolve_microsandbox_path(self.root, file_path)
        await self._make_directory(posixpath.dirname(remote_path) or "/")
        await self._run_remote_aio(lambda: self.fs.write(remote_path, content.encode()))
        return f"[OK] Wrote file: {remote_path}"

    async def glob(
        self,
        pattern: str,
        *,
        path: str = "",
        include_dirs: bool = False,
    ) -> str:
        self.touch()
        if not pattern:
            raise ValueError("Glob pattern must not be empty.")
        target = resolve_microsandbox_path(self.root, path or ".")
        command = build_sandbox_glob_command(
            root=self.root,
            target=target,
            pattern=pattern,
            include_dirs=include_dirs,
        )
        result = await self._exec(command, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Glob failed.")
        return render_sandbox_glob_output(result.output)

    async def grep(
        self,
        pattern: str,
        *,
        path: str = "",
        include: str | None = None,
        case_insensitive: bool = False,
        max_results: int = 100,
    ) -> str:
        self.touch()
        if not pattern:
            raise ValueError("Grep pattern must not be empty.")
        target = resolve_microsandbox_path(self.root, path or ".")
        command = build_sandbox_grep_command(
            root=self.root,
            target=target,
            relative_path=microsandbox_relative_path(self.root, target),
            pattern=pattern,
            include=include,
            case_insensitive=case_insensitive,
            max_results=max_results,
        )
        result = await self._exec(command, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Grep failed.")
        return render_sandbox_grep_output(result.output, max_results=max_results)

    async def execute(
        self,
        command: str,
        *,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        self.touch()
        self._invalidate_workspace_caches()
        return await self._exec(
            command,
            cwd=self.root,
            timeout=timeout,
            max_output_chars=max_output_chars,
        )

    async def tree(self, path: str | None = None) -> dict[str, Any]:
        self.touch()
        target = resolve_microsandbox_path(self.root, path or ".")
        cached, value = self._get_metadata_cache("tree", target)
        if cached:
            return value
        info = await self._stat(target)
        if not file_info_is_dir(info):
            raise NotADirectoryError(f"Path is not a directory: {path or '.'}")
        entries: list[dict[str, Any]] = []
        truncated = False
        items = await self._run_remote_aio(lambda: self.fs.list(target))
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
        self.touch()
        remote_path = resolve_microsandbox_path(self.root, path)
        info = await self._stat(remote_path)
        if file_info_is_dir(info):
            raise ValueError(f"Path is not a file: {path}")
        raw_bytes = await self._read_bytes(remote_path)
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

    async def changes(self) -> dict[str, Any]:
        self.touch()
        git_root = await self._find_git_root()
        if git_root is None:
            return {
                "root": self.root,
                "is_git_repo": False,
                "changes": [],
                "message": "Workspace is not a Git repository.",
            }
        output = await self._run_git(git_status_args(), cwd=self.root)
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
        remote_path = resolve_microsandbox_path(self.root, path)
        relative = microsandbox_relative_path(self.root, remote_path)
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
                    await self._run_git(git_status_args(), cwd=self.root),
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
            staged = await self._run_git(
                ["diff", "--cached", "--no-ext-diff", "--", gpath],
                cwd=git_root,
            )
            unstaged = await self._run_git(
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

    async def rename(self, *, path: str, new_name: str) -> dict[str, Any]:
        self.touch()
        self._invalidate_workspace_caches()
        source = resolve_microsandbox_path(self.root, path)
        relative = microsandbox_relative_path(self.root, source)
        if not relative:
            raise ValueError("Cannot rename workspace root.")
        clean_name = str(new_name or "").strip()
        if not clean_name or clean_name in {".", ".."}:
            raise ValueError("New name is invalid.")
        if "/" in clean_name or "\\" in clean_name:
            raise ValueError("New name must not contain path separators.")
        destination = resolve_microsandbox_path(
            self.root,
            posixpath.join(posixpath.dirname(relative), clean_name),
        )
        if destination == source:
            return {"root": self.root, "path": source, "new_path": destination}
        check = await self._exec(f"test -e {shlex.quote(destination)}", cwd="/")
        if check.exit_code == 0:
            raise FileExistsError(f"Destination already exists: {clean_name}")
        result = await self._exec(
            f"mv {shlex.quote(source)} {shlex.quote(destination)}",
            cwd="/",
        )
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Rename failed.")
        return {"root": self.root, "path": source, "new_path": destination}

    async def delete(self, *, path: str) -> dict[str, Any]:
        self.touch()
        self._invalidate_workspace_caches()
        target = resolve_microsandbox_path(self.root, path)
        relative = microsandbox_relative_path(self.root, target)
        if not relative:
            raise ValueError("Cannot delete workspace root.")
        kind_result = await self._exec(
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
        result = await self._exec(f"rm -rf {shlex.quote(target)}", cwd="/")
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Delete failed.")
        return {"root": self.root, "path": target, "kind": kind}

    async def clone_repository(
        self,
        *,
        owner: str,
        repo: str,
        default_branch: str = "main",
        token: str | None = None,
        depth: int = 1,
    ) -> str:
        if not owner or not repo:
            raise ValueError("Repository owner and name are required.")
        self._invalidate_workspace_caches()
        self.touch()
        await self.ensure_root()
        await self.ensure_git()
        relative = repo
        destination = resolve_microsandbox_path(self.root, relative)
        branch = (default_branch or "main").strip() or "main"
        depth_flag = f"--depth={int(depth)}" if depth and int(depth) > 0 else ""
        if token:
            url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
        else:
            url = f"https://github.com/{owner}/{repo}.git"
        await self._make_directory(posixpath.dirname(destination) or "/")
        check = await self._exec(f"test -e {shlex.quote(destination)}", cwd="/")
        if check.exit_code == 0:
            existing = await self._exec(
                f"cd {shlex.quote(destination)} && git rev-parse --is-inside-work-tree",
                cwd="/",
                timeout=GIT_TIMEOUT_SECONDS,
            )
            if existing.exit_code in (0, None):
                return relative
            await self._exec(f"rm -rf {shlex.quote(destination)}", cwd="/")
        command = (
            f"git clone {depth_flag} --branch {shlex.quote(branch)} "
            f"--single-branch {shlex.quote(url)} {shlex.quote(destination)}"
        )
        result = await self._exec(command, cwd="/", timeout=120)
        if result.exit_code not in (0, None):
            detail = (result.output or "").strip()
            if "could not find" in detail.lower() or "not found" in detail.lower():
                fallback = (
                    f"git clone {depth_flag} --single-branch "
                    f"{shlex.quote(url)} {shlex.quote(destination)}"
                )
                result = await self._exec(fallback, cwd="/", timeout=120)
        if result.exit_code not in (0, None):
            raise RuntimeError(
                f"git clone failed for {owner}/{repo}: {result.output.strip()}"
            )
        return relative

    async def _exec(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        try:
            output = await _maybe_await(
                self.sandbox.shell(
                    command,
                    cwd=cwd,
                    timeout=float(timeout) if timeout is not None else None,
                )
            )
            stdout = _exec_output_text(output, "stdout")
            stderr = _exec_output_text(output, "stderr")
            combined = stdout + (f"\n[stderr]: {stderr}" if stderr else "")
            truncated = False
            if max_output_chars is not None and len(combined) > max_output_chars:
                combined = combined[:max_output_chars] + "\n...[truncated]"
                truncated = True
            exit_code = getattr(output, "exit_code", None)
            return CommandResult(
                output=combined,
                exit_code=int(exit_code) if exit_code is not None else None,
                truncated=truncated,
            )
        except Exception as exc:
            _raise_if_microsandbox_unavailable(self.ref, exc)
            raise

    async def _make_directory(self, path: str) -> None:
        result = await self._exec(f"mkdir -p {shlex.quote(path)}", cwd="/")
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Failed to create directory.")

    def _download_file(self, path: str) -> bytes | str:
        raise RuntimeError(f"Use async file reads for Microsandbox path: {path}")

    def _upload_file(self, content: bytes, path: str) -> None:
        raise RuntimeError(f"Use async file writes for Microsandbox path: {path}")

    def _stat_file(self, path: str) -> Any:
        raise RuntimeError(f"Use async stat for Microsandbox path: {path}")

    async def _stat(self, path: str) -> Any:
        return await self._run_remote_aio(lambda: self.fs.stat(path))

    async def _read_bytes(self, path: str) -> bytes:
        raw = await self._run_remote_aio(lambda: self.fs.read(path))
        if isinstance(raw, str):
            return raw.encode("utf-8")
        return bytes(raw or b"")

    async def _run_git(self, args: list[str], *, cwd: str) -> str:
        command = "git " + " ".join(shlex.quote(arg) for arg in args)
        result = await self._exec(command, cwd=cwd, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            detail = result.output.strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        return result.output

    async def _find_git_root(self) -> str | None:
        try:
            output = (await self._run_git(
                ["rev-parse", "--show-toplevel"],
                cwd=self.root,
            )).strip()
        except WorkspaceUnavailableError:
            raise
        except Exception:
            return None
        if not output:
            return None
        return normalize_posix_path(output.splitlines()[-1])

    async def _batch_change_line_stats(
        self,
        changes: list[dict[str, Any]],
        *,
        git_root: str,
    ) -> None:
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
                    output = await self._run_git(args, cwd=git_root)
                except WorkspaceUnavailableError:
                    raise
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
                continue
            stats = numstat_by_path.get(path, {})
            change["additions"] = stats.get("additions", 0)
            change["deletions"] = stats.get("deletions", 0)

    async def _change_line_stats(
        self,
        change: dict[str, Any],
        *,
        git_root: str,
    ) -> tuple[int, int]:
        from agent.modules.workspaces.git_utils import (
            compute_change_line_stats_from_numstat,
        )

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
                output = await self._run_git(args, cwd=git_root)
            except WorkspaceUnavailableError:
                raise
            except Exception:
                continue
            a, d = compute_change_line_stats_from_numstat(output)
            additions += a
            deletions += d
        return additions, deletions

    async def _text_line_count(self, path: str) -> int:
        try:
            raw_bytes = await self._read_bytes(path)
        except WorkspaceUnavailableError:
            raise
        except Exception:
            return 0
        if b"\0" in raw_bytes:
            return 0
        text = raw_bytes[: MAX_UNTRACKED_FILE_CHARS + 1].decode(
            "utf-8",
            errors="replace",
        )
        return len(text.splitlines()) or (1 if text else 0)

    async def _build_untracked_diff(self, path: str, relative_path: str) -> str:
        from agent.modules.workspaces.git_utils import build_untracked_diff_content

        try:
            content = await self.read_text(path)
        except WorkspaceUnavailableError:
            raise
        except Exception:
            return ""
        return build_untracked_diff_content(content, relative_path)


def _exec_output_text(output: Any, stream: str) -> str:
    text_attr = f"{stream}_text"
    bytes_attr = f"{stream}_bytes"
    try:
        value = getattr(output, text_attr)
    except Exception:
        value = None
    if value is not None:
        return str(value)
    try:
        raw = getattr(output, bytes_attr)
    except Exception:
        raw = b""
    if isinstance(raw, str):
        return raw
    return bytes(raw or b"").decode("utf-8", errors="replace")


async def clone_repository_in_microsandbox(
    ref: WorkspaceRef,
    *,
    owner: str,
    repo: str,
    default_branch: str = "main",
    token: str | None = None,
    depth: int = 1,
) -> str:
    if ref.backend != MICROSANDBOX_BACKEND:
        raise ValueError(f"Unsupported workspace backend: {ref.backend}")
    backend = await MicrosandboxWorkspaceBackend.create(ref)
    return await backend.clone_repository(
        owner=owner,
        repo=repo,
        default_branch=default_branch,
        token=token,
        depth=depth,
    )


__all__ = [
    "DEFAULT_MICROSANDBOX_IMAGE",
    "DEFAULT_MICROSANDBOX_ROOT",
    "MICROSANDBOX_BACKEND",
    "MicrosandboxWorkspaceBackend",
    "attach_microsandbox_workspace",
    "clone_repository_in_microsandbox",
    "create_microsandbox_backend",
    "create_microsandbox_workspace",
    "delete_microsandbox_workspace",
    "get_microsandbox_module",
    "get_microsandbox_sandbox",
    "list_microsandbox_sandboxes",
    "microsandbox_relative_path",
    "resolve_microsandbox_path",
    "stop_microsandbox_workspace",
]
