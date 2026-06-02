from __future__ import annotations

import asyncio
import difflib
import logging
import mimetypes
import posixpath
import shlex
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.modules.workspaces.backends import CommandResult
from agent.modules.workspaces.local_backend import MAX_LIST_FILES_ENTRIES
from agent.modules.workspaces.refs import WorkspaceRef
from agent.modules.workspaces.service import (
    IGNORED_DIR_NAMES,
    MAX_DIFF_CHARS,
    MAX_FILE_BYTES,
    MAX_TREE_ENTRIES,
    MAX_UNTRACKED_FILE_CHARS,
)


logger = logging.getLogger(__name__)

DAYTONA_BACKEND = "daytona"
DEFAULT_DAYTONA_ROOT = "workspace"
GIT_TIMEOUT_SECONDS = 10
DAYTONA_STATUS_STARTED = "started"
DAYTONA_STATUS_STOPPED = "stopped"
DAYTONA_STATUS_ARCHIVED = "archived"
DAYTONA_STATUS_DESTROYED = "destroyed"
DAYTONA_STATUS_UNKNOWN = "unknown"


class _DaytonaLifecycleSweeper:
    """Owns the lifecycle sweeper task so it survives module reloads cleanly.

    Encapsulating the task in an instance removes the module-level mutable
    global that previously caused stale tasks to leak across ``sys.modules``
    reloads in tests.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        async with self._lock:
            if self.is_running():
                return
            self._task = asyncio.create_task(
                _daytona_lifecycle_sweeper_loop(),
                name="daytona-lifecycle-sweeper",
            )

    async def stop(self) -> None:
        async with self._lock:
            task = self._task
            if task is None:
                return
            self._task = None
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


_sweeper = _DaytonaLifecycleSweeper()


def _config() -> tuple[bool, str, str]:
    from agent.shared.config.service import get_config_service

    service = get_config_service()
    enabled = service.get_bool("workspace.daytona.enabled", False)
    api_key = service.get_str("workspace.daytona.api_key", "").strip()
    root = (
        service.get_str(
            "workspace.daytona.default_root",
            DEFAULT_DAYTONA_ROOT,
        ).strip()
        or DEFAULT_DAYTONA_ROOT
    )
    return enabled, api_key, root


def _lifecycle_config() -> dict[str, int]:
    from agent.shared.config.service import get_config_service

    service = get_config_service()
    return {
        "auto_stop_minutes": max(
            0,
            service.get_int("workspace.daytona.auto_stop_minutes", 30),
        ),
        "auto_archive_days": max(
            0,
            service.get_int("workspace.daytona.auto_archive_days", 7),
        ),
        "sweeper_interval_seconds": max(
            30,
            service.get_int("workspace.daytona.sweeper_interval_seconds", 60),
        ),
        "start_timeout_seconds": max(
            1,
            service.get_int("workspace.daytona.start_timeout_seconds", 120),
        ),
        "stop_timeout_seconds": max(
            1,
            service.get_int("workspace.daytona.stop_timeout_seconds", 60),
        ),
    }


def get_daytona_client():
    enabled, api_key, _ = _config()
    if not enabled:
        raise ValueError("Daytona workspace backend is disabled.")
    if not api_key:
        raise ValueError("Daytona API key is not configured.")
    try:
        from daytona import Daytona, DaytonaConfig
    except ImportError as exc:
        try:
            from daytona_sdk import Daytona, DaytonaConfig
        except ImportError:
            raise RuntimeError(
                "The Daytona Python SDK is not installed. Install project dependencies with uv."
            ) from exc
    return Daytona(DaytonaConfig(api_key=api_key))


def create_daytona_workspace(*, label: str | None = None) -> WorkspaceRef:
    _, _, root = _config()
    client = get_daytona_client()
    sandbox = client.create()
    sandbox_id = str(getattr(sandbox, "id", "") or "").strip()
    if not sandbox_id:
        raise RuntimeError("Daytona did not return a sandbox ID.")
    metadata = daytona_lifecycle_metadata(
        root=root,
        status=_sandbox_state(sandbox),
        touch=True,
        started=True,
    )
    ref = WorkspaceRef(
        backend=DAYTONA_BACKEND,
        locator=sandbox_id,
        label=(label or "").strip() or f"daytona:{sandbox_id}",
        metadata=metadata,
    )
    backend = DaytonaWorkspaceBackend(ref, sandbox=sandbox)
    backend.ensure_root()
    ref.metadata.update(
        daytona_lifecycle_metadata(
            root=root,
            status=backend.status,
            touch=True,
            started=True,
        )
    )
    return ref


def attach_daytona_workspace(
    sandbox_id: str,
    *,
    label: str | None = None,
    root: str | None = None,
) -> WorkspaceRef:
    _, _, default_root = _config()
    normalized_sandbox_id = str(sandbox_id or "").strip()
    if not normalized_sandbox_id:
        raise ValueError("Daytona sandbox ID is required.")
    selected_root = (root or "").strip() or default_root or DEFAULT_DAYTONA_ROOT
    ref = WorkspaceRef(
        backend=DAYTONA_BACKEND,
        locator=normalized_sandbox_id,
        label=(label or "").strip() or f"daytona:{normalized_sandbox_id}",
        metadata=daytona_lifecycle_metadata(root=selected_root, touch=True),
    )
    backend = DaytonaWorkspaceBackend(ref)
    backend.ensure_root()
    ref.metadata.update(
        daytona_lifecycle_metadata(
            root=selected_root,
            status=backend.status,
            touch=True,
        )
    )
    return ref


def daytona_lifecycle_metadata(
    *,
    root: str | None = None,
    status: str | None = None,
    touch: bool = False,
    started: bool = False,
    stopped: bool = False,
    archived: bool = False,
) -> dict[str, Any]:
    now = _utcnow_iso()
    metadata: dict[str, Any] = {}
    if root is not None:
        metadata["root"] = (
            root or DEFAULT_DAYTONA_ROOT
        ).strip() or DEFAULT_DAYTONA_ROOT
    if status is not None:
        metadata["status"] = _normalize_status(status)
    if touch:
        metadata["last_used_at"] = now
    if started:
        metadata["last_started_at"] = now
    if stopped:
        metadata["last_stopped_at"] = now
    if archived:
        metadata["last_archived_at"] = now
    return metadata


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return DAYTONA_STATUS_UNKNOWN
    if raw.startswith("sandboxstate."):
        raw = raw.rsplit(".", 1)[-1]
    if raw in {"running", "active"}:
        return DAYTONA_STATUS_STARTED
    if raw in {"stopping", "stopped"}:
        return DAYTONA_STATUS_STOPPED
    if raw in {"archived", "archive"}:
        return DAYTONA_STATUS_ARCHIVED
    if raw in {"destroyed", "deleted", "removed"}:
        return DAYTONA_STATUS_DESTROYED
    return raw


def _sandbox_state(sandbox: Any) -> str:
    state = getattr(sandbox, "state", None)
    if state is None:
        return DAYTONA_STATUS_UNKNOWN
    value = getattr(state, "value", None)
    if value is None:
        value = getattr(state, "name", None)
    return _normalize_status(value if value is not None else state)


def _refresh_sandbox_state(sandbox: Any) -> str:
    refresh = getattr(sandbox, "refresh_data", None)
    if callable(refresh):
        with suppress(Exception):
            refresh()
    return _sandbox_state(sandbox)


def _is_daytona_not_found_error(exc: Exception) -> bool:
    class_name = exc.__class__.__name__.lower()
    if "notfound" in class_name or "not_found" in class_name:
        return True
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if str(status or "").strip() == "404":
        return True
    message = str(exc).lower()
    return "404" in message or ("sandbox" in message and "not found" in message)


def _thread_root_id(thread_id: str | None) -> str | None:
    normalized = str(thread_id or "").strip()
    if not normalized:
        return None
    return normalized.split(":sub:", 1)[0]


def update_daytona_thread_lifecycle_sync(
    thread_id: str | None,
    *,
    root: str | None = None,
    status: str | None = None,
    touch: bool = False,
    started: bool = False,
    stopped: bool = False,
    archived: bool = False,
) -> None:
    normalized_thread_id = _thread_root_id(thread_id)
    if not normalized_thread_id:
        return

    from agent.modules.workspaces.repository import (
        update_thread_workspace_metadata_sync,
    )

    metadata = daytona_lifecycle_metadata(
        root=root,
        status=status,
        touch=touch,
        started=started,
        stopped=stopped,
        archived=archived,
    )
    if not metadata:
        return
    try:
        update_thread_workspace_metadata_sync(
            thread_id=normalized_thread_id,
            metadata=metadata,
            expected_backend=DAYTONA_BACKEND,
        )
    except Exception as exc:
        logger.debug(
            "Failed to update Daytona lifecycle metadata for thread %s: %s",
            normalized_thread_id,
            exc,
        )


def get_daytona_sandbox(ref: WorkspaceRef):
    if ref.backend != DAYTONA_BACKEND:
        raise ValueError(f"Unsupported workspace backend: {ref.backend}")
    return get_daytona_client().get(ref.locator)


def ensure_daytona_workspace_active(
    ref: WorkspaceRef,
    *,
    sandbox: Any | None = None,
    thread_id: str | None = None,
) -> Any:
    sandbox_obj = sandbox or get_daytona_sandbox(ref)
    status = _refresh_sandbox_state(sandbox_obj)
    started = False

    if status == DAYTONA_STATUS_DESTROYED:
        raise RuntimeError(f"Daytona sandbox is destroyed: {ref.locator}")

    if status in {"error", "build_failed"}:
        recoverable = bool(getattr(sandbox_obj, "recoverable", False))
        recover = getattr(sandbox_obj, "recover", None)
        if recoverable and callable(recover):
            recover(timeout=_lifecycle_config()["start_timeout_seconds"])
            started = True
        else:
            raise RuntimeError(f"Daytona sandbox is not recoverable: {status}")
    elif status not in {DAYTONA_STATUS_STARTED, "starting", DAYTONA_STATUS_UNKNOWN}:
        starter = getattr(sandbox_obj, "start", None)
        if not callable(starter):
            raise RuntimeError(f"Daytona sandbox is not active: {status}")
        starter(timeout=_lifecycle_config()["start_timeout_seconds"])
        started = True
    elif status == "starting":
        waiter = getattr(sandbox_obj, "wait_for_sandbox_start", None)
        if callable(waiter):
            waiter(timeout=_lifecycle_config()["start_timeout_seconds"])
            started = True

    final_status = _refresh_sandbox_state(sandbox_obj)
    if final_status == DAYTONA_STATUS_UNKNOWN:
        final_status = DAYTONA_STATUS_STARTED
    ref.metadata.update(
        daytona_lifecycle_metadata(
            root=str(ref.metadata.get("root") or DEFAULT_DAYTONA_ROOT),
            status=final_status,
            touch=True,
            started=started,
        )
    )
    update_daytona_thread_lifecycle_sync(
        thread_id,
        root=str(ref.metadata.get("root") or DEFAULT_DAYTONA_ROOT),
        status=final_status,
        touch=True,
        started=started,
    )
    return sandbox_obj


def stop_daytona_workspace(
    ref: WorkspaceRef,
    *,
    thread_id: str | None = None,
    force: bool = False,
) -> str:
    sandbox = get_daytona_sandbox(ref)
    status = _refresh_sandbox_state(sandbox)
    if status not in {
        DAYTONA_STATUS_STOPPED,
        DAYTONA_STATUS_ARCHIVED,
        DAYTONA_STATUS_DESTROYED,
        DAYTONA_STATUS_UNKNOWN,
    }:
        stopper = getattr(sandbox, "stop", None)
        if not callable(stopper):
            raise RuntimeError("Daytona sandbox does not support stop().")
        stopper(timeout=_lifecycle_config()["stop_timeout_seconds"], force=force)
        status = _refresh_sandbox_state(sandbox)
    if status == DAYTONA_STATUS_UNKNOWN:
        status = DAYTONA_STATUS_STOPPED
    ref.metadata.update(
        daytona_lifecycle_metadata(
            root=str(ref.metadata.get("root") or DEFAULT_DAYTONA_ROOT),
            status=status,
            stopped=status == DAYTONA_STATUS_STOPPED,
        )
    )
    update_daytona_thread_lifecycle_sync(
        thread_id,
        root=str(ref.metadata.get("root") or DEFAULT_DAYTONA_ROOT),
        status=status,
        stopped=status == DAYTONA_STATUS_STOPPED,
    )
    return status


def archive_daytona_workspace(
    ref: WorkspaceRef,
    *,
    thread_id: str | None = None,
) -> str:
    sandbox = get_daytona_sandbox(ref)
    status = _refresh_sandbox_state(sandbox)
    if status not in {DAYTONA_STATUS_STOPPED, DAYTONA_STATUS_ARCHIVED}:
        stop_daytona_workspace(ref, thread_id=thread_id)
        sandbox = get_daytona_sandbox(ref)
        status = _refresh_sandbox_state(sandbox)
    if status != DAYTONA_STATUS_ARCHIVED:
        archiver = getattr(sandbox, "archive", None)
        if not callable(archiver):
            raise RuntimeError("Daytona sandbox does not support archive().")
        archiver()
        status = _refresh_sandbox_state(sandbox)
    if status == DAYTONA_STATUS_UNKNOWN:
        status = DAYTONA_STATUS_ARCHIVED
    ref.metadata.update(
        daytona_lifecycle_metadata(
            root=str(ref.metadata.get("root") or DEFAULT_DAYTONA_ROOT),
            status=status,
            archived=status == DAYTONA_STATUS_ARCHIVED,
        )
    )
    update_daytona_thread_lifecycle_sync(
        thread_id,
        root=str(ref.metadata.get("root") or DEFAULT_DAYTONA_ROOT),
        status=status,
        archived=status == DAYTONA_STATUS_ARCHIVED,
    )
    return status


def delete_daytona_workspace(
    ref: WorkspaceRef,
    *,
    thread_id: str | None = None,
) -> str:
    if ref.backend != DAYTONA_BACKEND:
        raise ValueError(f"Unsupported workspace backend: {ref.backend}")

    root = str(ref.metadata.get("root") or DEFAULT_DAYTONA_ROOT)
    try:
        sandbox = get_daytona_sandbox(ref)
    except Exception as exc:
        if not _is_daytona_not_found_error(exc):
            raise
        status = DAYTONA_STATUS_DESTROYED
    else:
        status = _refresh_sandbox_state(sandbox)
        if status != DAYTONA_STATUS_DESTROYED:
            deleter = getattr(sandbox, "delete", None)
            if not callable(deleter):
                raise RuntimeError("Daytona sandbox does not support delete().")
            deleter(timeout=_lifecycle_config()["stop_timeout_seconds"])
            status = DAYTONA_STATUS_DESTROYED

    ref.metadata.update(daytona_lifecycle_metadata(root=root, status=status))
    update_daytona_thread_lifecycle_sync(thread_id, root=root, status=status)
    return status


async def sweep_idle_daytona_workspaces(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    enabled, api_key, _ = _config()
    if not enabled or not api_key:
        return {
            "checked": 0,
            "stopped": 0,
            "archived": 0,
            "skipped": 0,
            "errors": [],
        }

    lifecycle = _lifecycle_config()
    auto_stop = lifecycle["auto_stop_minutes"]
    auto_archive = lifecycle["auto_archive_days"]
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stop_after = timedelta(minutes=auto_stop) if auto_stop > 0 else None
    archive_after = timedelta(days=auto_archive) if auto_archive > 0 else None

    from agent.modules.tools import close_thread_shell_sessions
    from agent.modules.workspaces.repository import get_thread_workspace_repository
    from agent.modules.workspaces.service import resolve_workspace_ref

    records = await get_thread_workspace_repository().list_by_backend(DAYTONA_BACKEND)
    result: dict[str, Any] = {
        "checked": len(records),
        "stopped": 0,
        "archived": 0,
        "skipped": 0,
        "errors": [],
    }
    for thread_id, record in records.items():
        workspace_payload = record.get("workspace")
        if not workspace_payload:
            result["skipped"] += 1
            continue
        try:
            workspace = resolve_workspace_ref(workspace_payload)
            last_used_at = _parse_utc_datetime(workspace.metadata.get("last_used_at"))
            if last_used_at is None:
                result["skipped"] += 1
                continue
            idle_for = current_time - last_used_at
            status = _normalize_status(workspace.metadata.get("status"))
            if status == DAYTONA_STATUS_DESTROYED:
                result["skipped"] += 1
                continue

            if archive_after is not None and idle_for >= archive_after:
                close_thread_shell_sessions(thread_id)
                archive_daytona_workspace(workspace, thread_id=thread_id)
                result["archived"] += 1
                continue

            if (
                stop_after is not None
                and idle_for >= stop_after
                and status
                not in {
                    DAYTONA_STATUS_STOPPED,
                    DAYTONA_STATUS_ARCHIVED,
                    DAYTONA_STATUS_DESTROYED,
                }
            ):
                close_thread_shell_sessions(thread_id)
                stop_daytona_workspace(workspace, thread_id=thread_id)
                result["stopped"] += 1
            else:
                result["skipped"] += 1
        except Exception as exc:
            if _is_daytona_not_found_error(exc):
                logger.info(
                    "Daytona sandbox not found for thread %s; marking as destroyed.",
                    thread_id,
                )
                workspace.metadata.update(
                    daytona_lifecycle_metadata(
                        root=str(workspace.metadata.get("root") or DEFAULT_DAYTONA_ROOT),
                        status=DAYTONA_STATUS_DESTROYED,
                    )
                )
                update_daytona_thread_lifecycle_sync(
                    thread_id,
                    root=str(workspace.metadata.get("root") or DEFAULT_DAYTONA_ROOT),
                    status=DAYTONA_STATUS_DESTROYED,
                )
                result["skipped"] += 1
            else:
                logger.warning(
                    "Failed to sweep Daytona workspace for thread %s: %s",
                    thread_id,
                    exc,
                )
                result["errors"].append({"thread_id": thread_id, "error": str(exc)})
    return result


async def _daytona_lifecycle_sweeper_loop() -> None:
    while True:
        lifecycle = _lifecycle_config()
        try:
            await sweep_idle_daytona_workspaces()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Daytona lifecycle sweeper failed: %s", exc)
        await asyncio.sleep(lifecycle["sweeper_interval_seconds"])


def start_daytona_lifecycle_sweeper() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("No running event loop; skipping sweeper start.")
        return
    loop.create_task(_sweeper.start())


async def stop_daytona_lifecycle_sweeper() -> None:
    await _sweeper.stop()


def resolve_daytona_path(root: str, path: str | None = None) -> str:
    normalized_root = _normalize_posix_path(root)
    raw_path = str(path or "").strip()
    if not raw_path or raw_path == ".":
        return normalized_root
    if any(part == ".." for part in raw_path.replace("\\", "/").split("/")):
        raise ValueError(f"Path escapes workspace: {path}")

    normalized_input = _normalize_posix_path(raw_path)
    if normalized_root == "/":
        if posixpath.isabs(normalized_input):
            return normalized_input
        return _normalize_posix_path(posixpath.join(normalized_root, normalized_input))
    if normalized_input == normalized_root or normalized_input.startswith(
        f"{normalized_root}/"
    ):
        target = normalized_input
    elif posixpath.isabs(normalized_input):
        raise ValueError(f"Path escapes workspace: {path}")
    else:
        target = _normalize_posix_path(
            posixpath.join(normalized_root, normalized_input)
        )

    if target != normalized_root and not target.startswith(f"{normalized_root}/"):
        raise ValueError(f"Path escapes workspace: {path}")
    return target


def daytona_relative_path(root: str, path: str) -> str:
    target = resolve_daytona_path(root, path)
    if target == root:
        return ""
    return target[len(root) + 1 :]


class DaytonaWorkspaceBackend:
    """Workspace backend backed by a Daytona sandbox."""

    def __init__(
        self,
        ref: WorkspaceRef,
        *,
        sandbox: Any | None = None,
        thread_id: str | None = None,
    ) -> None:
        if ref.backend != DAYTONA_BACKEND:
            raise ValueError(f"Unsupported workspace backend: {ref.backend}")
        self.ref = ref
        self.thread_id = _thread_root_id(thread_id)
        self.client = None if sandbox is not None else get_daytona_client()
        self.sandbox = sandbox if sandbox is not None else self.client.get(ref.locator)
        self.status = DAYTONA_STATUS_UNKNOWN
        self.fs = None
        self.process = None
        self.ensure_active()
        self.root_hint = str(ref.metadata.get("root") or DEFAULT_DAYTONA_ROOT).strip()
        self.root = self._resolve_root(self.root_hint)

    def ensure_active(self) -> None:
        self.sandbox = ensure_daytona_workspace_active(
            self.ref,
            sandbox=self.sandbox,
            thread_id=self.thread_id,
        )
        self.status = _normalize_status(self.ref.metadata.get("status"))
        self.fs = getattr(self.sandbox, "fs", None)
        self.process = getattr(self.sandbox, "process", None)
        if self.fs is None or self.process is None:
            raise RuntimeError("Daytona sandbox does not expose fs/process APIs.")

    def touch(self) -> None:
        self.ref.metadata.update(
            daytona_lifecycle_metadata(
                root=str(self.ref.metadata.get("root") or DEFAULT_DAYTONA_ROOT),
                status=self.status,
                touch=True,
            )
        )
        update_daytona_thread_lifecycle_sync(
            self.thread_id,
            root=str(self.ref.metadata.get("root") or DEFAULT_DAYTONA_ROOT),
            status=self.status,
            touch=True,
        )

    def ensure_root(self) -> None:
        self.ensure_active()
        self._exec(f"mkdir -p {shlex.quote(self.root)}", cwd="/")

    def clone_repository(
        self,
        *,
        owner: str,
        repo: str,
        default_branch: str = "main",
        token: str | None = None,
        depth: int = 1,
    ) -> str:
        """Clone a git repository into the workspace root and return the relative path.

        The clone URL embeds an installation token when provided so the sandbox
        can fetch private repositories. Returns the path relative to ``self.root``
        (for example ``"owner/repo"``).
        """
        if not owner or not repo:
            raise ValueError("Repository owner and name are required.")
        self.ensure_active()
        self.ensure_root()
        relative = f"{owner}/{repo}"
        destination = resolve_daytona_path(self.root, relative)
        branch = (default_branch or "main").strip() or "main"
        depth_flag = f"--depth={int(depth)}" if depth and int(depth) > 0 else ""
        if token:
            url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
        else:
            url = f"https://github.com/{owner}/{repo}.git"
        parent = posixpath.dirname(destination)
        self._exec(f"mkdir -p {shlex.quote(parent)}", cwd="/")
        if self._exec(f"test -e {shlex.quote(destination)}", cwd="/").exit_code == 0:
            existing = self._exec(
                f"cd {shlex.quote(destination)} && git rev-parse --is-inside-work-tree",
                cwd="/",
                timeout=GIT_TIMEOUT_SECONDS,
            )
            if existing.exit_code in (0, None):
                return relative
            self._exec(f"rm -rf {shlex.quote(destination)}", cwd="/")
        command = (
            f"git clone {depth_flag} --branch {shlex.quote(branch)} "
            f"--single-branch {shlex.quote(url)} {shlex.quote(destination)}"
        )
        result = self._exec(command, cwd="/", timeout=120)
        if result.exit_code not in (0, None):
            detail = (result.output or "").strip()
            if "could not find" in detail.lower() or "not found" in detail.lower():
                fallback = (
                    f"git clone {depth_flag} --single-branch "
                    f"{shlex.quote(url)} {shlex.quote(destination)}"
                )
                result = self._exec(fallback, cwd="/", timeout=120)
        if result.exit_code not in (0, None):
            raise RuntimeError(
                f"git clone failed for {owner}/{repo}: {result.output.strip()}"
            )
        return relative

    async def list_files(self, sub_dir: str = "") -> str:
        self.ensure_active()
        target = resolve_daytona_path(self.root, sub_dir or ".")
        command = (
            f"find {shlex.quote(target)} "
            "-type d \\( -name .git -o -name .venv -o -name node_modules "
            "-o -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \\) "
            "-prune -o -type f -print"
        )
        result = self._exec(command, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Failed to list Daytona files.")
        files = [line for line in result.output.splitlines() if line.strip()]
        truncated = len(files) > MAX_LIST_FILES_ENTRIES
        files = files[:MAX_LIST_FILES_ENTRIES]
        if not files:
            return "(Empty directory)"
        output = "\n".join(files)
        if truncated:
            output += f"\n...[truncated at {MAX_LIST_FILES_ENTRIES} entries]"
        return output

    async def read_text(self, file_path: str) -> str:
        self.ensure_active()
        remote_path = resolve_daytona_path(self.root, file_path)
        raw = self._download_file(remote_path)
        if isinstance(raw, str):
            return raw
        return bytes(raw or b"").decode("utf-8", errors="replace")

    async def write_text(self, file_path: str, content: str) -> str:
        self.ensure_active()
        remote_path = resolve_daytona_path(self.root, file_path)
        parent = posixpath.dirname(remote_path)
        if parent:
            self._exec(f"mkdir -p {shlex.quote(parent)}", cwd="/")
        self._upload_file(content.encode("utf-8"), remote_path)
        return f"[OK] Wrote file: {remote_path}"

    async def execute(
        self,
        command: str,
        *,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        self.ensure_active()
        return self._exec(
            command,
            cwd=self.root,
            timeout=timeout,
            max_output_chars=max_output_chars,
        )

    async def tree(self, path: str | None = None) -> dict[str, Any]:
        self.ensure_active()
        target = resolve_daytona_path(self.root, path or ".")
        entries: list[dict[str, Any]] = []
        truncated = False
        for item in self.fs.list_files(target):
            name = _file_info_name(item)
            if not name:
                continue
            is_dir = _file_info_is_dir(item)
            if is_dir and name in IGNORED_DIR_NAMES:
                continue
            if len(entries) >= MAX_TREE_ENTRIES:
                truncated = True
                break
            entry_path = _file_info_path(item, target)
            entries.append(
                {
                    "name": name,
                    "path": entry_path,
                    "kind": "directory" if is_dir else "file",
                    "size": _file_info_size(item) if not is_dir else None,
                    "modified_at": _file_info_modified_at(item),
                }
            )
        entries.sort(
            key=lambda item: (item["kind"] != "directory", item["name"].lower())
        )
        return {
            "root": self.root,
            "path": target,
            "entries": entries,
            "truncated": truncated,
        }

    async def file(self, path: str) -> dict[str, Any]:
        self.ensure_active()
        remote_path = resolve_daytona_path(self.root, path)
        info = self.fs.get_file_info(remote_path)
        if _file_info_is_dir(info):
            raise ValueError(f"Path is not a file: {path}")
        size = _file_info_size(info)
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
                "size": size,
                "content": "",
                "truncated": truncated,
                "binary": True,
                "message": "Binary files cannot be previewed.",
            }
        return {
            "root": self.root,
            "path": remote_path,
            "mime_type": mime_type,
            "size": size,
            "content": raw_bytes.decode("utf-8", errors="replace"),
            "truncated": truncated,
            "binary": False,
            "message": "File truncated." if truncated else "",
        }

    async def changes(self) -> dict[str, Any]:
        self.ensure_active()
        git_root = self._find_git_root()
        if git_root is None:
            return {
                "root": self.root,
                "is_git_repo": False,
                "changes": [],
                "message": "Workspace is not a Git repository.",
            }
        output = self._run_git(_git_status_args(), cwd=self.root)
        changes = _parse_git_status(output, workspace_root=self.root, git_root=git_root)
        for change in changes:
            additions, deletions = self._change_line_stats(change, git_root=git_root)
            change["additions"] = additions
            change["deletions"] = deletions
        return {
            "root": self.root,
            "is_git_repo": True,
            "changes": changes,
            "message": "",
        }

    async def diff(self, path: str) -> dict[str, Any]:
        self.ensure_active()
        remote_path = resolve_daytona_path(self.root, path)
        relative_path = daytona_relative_path(self.root, remote_path)
        if not relative_path:
            raise ValueError("File path is required.")

        git_root = self._find_git_root()
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

        status_by_path = {
            change["path"]: change
            for change in _parse_git_status(
                self._run_git(_git_status_args(), cwd=self.root),
                workspace_root=self.root,
                git_root=git_root,
            )
        }
        change = status_by_path.get(remote_path)
        status = str(change.get("status") or "") if change else ""
        git_path = _git_relative_path(git_root, remote_path)
        if status == "untracked":
            diff = await self._build_untracked_diff(remote_path, relative_path)
        else:
            staged = self._run_git(
                ["diff", "--cached", "--no-ext-diff", "--", git_path],
                cwd=git_root,
            )
            unstaged = self._run_git(
                ["diff", "--no-ext-diff", "--", git_path],
                cwd=git_root,
            )
            diff = "\n".join(
                part for part in (staged.strip(), unstaged.strip()) if part
            )

        diff, truncated = _truncate_diff(diff)
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
        self.ensure_active()
        source = resolve_daytona_path(self.root, path)
        relative_path = daytona_relative_path(self.root, source)
        if not relative_path:
            raise ValueError("Cannot rename workspace root.")
        clean_name = str(new_name or "").strip()
        if not clean_name or clean_name in {".", ".."}:
            raise ValueError("New name is invalid.")
        if "/" in clean_name or "\\" in clean_name:
            raise ValueError("New name must not contain path separators.")
        destination = resolve_daytona_path(
            self.root,
            posixpath.join(posixpath.dirname(relative_path), clean_name),
        )
        if destination == source:
            return {"root": self.root, "path": source, "new_path": destination}
        check = self._exec(f"test -e {shlex.quote(destination)}", cwd="/")
        if check.exit_code == 0:
            raise FileExistsError(f"Destination already exists: {clean_name}")
        result = self._exec(
            f"mv {shlex.quote(source)} {shlex.quote(destination)}",
            cwd="/",
        )
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Rename failed.")
        return {"root": self.root, "path": source, "new_path": destination}

    async def delete(self, *, path: str) -> dict[str, Any]:
        self.ensure_active()
        target = resolve_daytona_path(self.root, path)
        relative_path = daytona_relative_path(self.root, target)
        if not relative_path:
            raise ValueError("Cannot delete workspace root.")
        kind_result = self._exec(
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
        result = self._exec(f"rm -rf {shlex.quote(target)}", cwd="/")
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Delete failed.")
        return {"root": self.root, "path": target, "kind": kind}

    def _resolve_root(self, root_hint: str) -> str:
        hint = root_hint or DEFAULT_DAYTONA_ROOT
        command = f"mkdir -p {shlex.quote(hint)} && cd {shlex.quote(hint)} && pwd"
        result = self._exec(command, cwd=None, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            raise RuntimeError(
                result.output.strip() or "Failed to resolve Daytona root."
            )
        root = result.output.strip().splitlines()[-1]
        return _normalize_posix_path(root)

    def _exec(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        try:
            response = self.process.exec(command, cwd=cwd, timeout=timeout)
        except TypeError:
            fallback_cwd = cwd or getattr(self, "root", None) or "."
            response = self.process.exec(command, fallback_cwd, timeout)
        output_value = getattr(response, "result", None)
        if output_value is None:
            output_value = getattr(response, "output", None)
        if output_value is None:
            artifacts = getattr(response, "artifacts", None)
            output_value = getattr(artifacts, "stdout", None)
        output = str(output_value or "")
        stderr = str(getattr(response, "stderr", "") or "")
        exit_code = getattr(response, "exit_code", None)
        combined = output + (f"\n[stderr]: {stderr}" if stderr else "")
        truncated = False
        if max_output_chars is not None and len(combined) > max_output_chars:
            combined = combined[:max_output_chars] + "\n...[truncated]"
            truncated = True
        return CommandResult(
            output=combined,
            exit_code=int(exit_code) if exit_code is not None else None,
            truncated=truncated,
        )

    def _download_file(self, path: str) -> bytes | str:
        return self.fs.download_file(path)

    def _upload_file(self, content: bytes, path: str) -> None:
        self.fs.upload_file(content, path)

    def _run_git(self, args: list[str], *, cwd: str) -> str:
        command = "git " + " ".join(shlex.quote(arg) for arg in args)
        result = self._exec(command, cwd=cwd, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            detail = result.output.strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        return result.output

    def _find_git_root(self) -> str | None:
        try:
            output = self._run_git(
                ["rev-parse", "--show-toplevel"], cwd=self.root
            ).strip()
        except Exception:
            return None
        if not output:
            return None
        return _normalize_posix_path(output.splitlines()[-1])

    def _change_line_stats(
        self,
        change: dict[str, Any],
        *,
        git_root: str,
    ) -> tuple[int, int]:
        path = str(change.get("path") or "")
        if not path:
            return 0, 0
        if change.get("status") == "untracked":
            return self._text_line_count(path), 0
        git_path = _git_relative_path(git_root, path)
        additions = 0
        deletions = 0
        for args in (
            ["diff", "--numstat", "--cached", "--no-ext-diff", "--", git_path],
            ["diff", "--numstat", "--no-ext-diff", "--", git_path],
        ):
            try:
                output = self._run_git(args, cwd=git_root)
            except Exception:
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

    def _text_line_count(self, path: str) -> int:
        try:
            raw = self._download_file(path)
        except Exception:
            return 0
        raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw or b"")
        if b"\0" in raw_bytes:
            return 0
        text = raw_bytes[: MAX_UNTRACKED_FILE_CHARS + 1].decode(
            "utf-8",
            errors="replace",
        )
        return len(text.splitlines()) or (1 if text else 0)

    async def _build_untracked_diff(self, path: str, relative_path: str) -> str:
        try:
            content = await self.read_text(path)
        except Exception:
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


def _normalize_posix_path(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raw = "."
    normalized = posixpath.normpath(raw)
    if normalized == ".":
        return ""
    return normalized.rstrip("/") or "/"


def clone_repository_in_daytona_sandbox(
    ref: WorkspaceRef,
    *,
    owner: str,
    repo: str,
    default_branch: str = "main",
    token: str | None = None,
    depth: int = 1,
) -> str:
    """Clone a GitHub repository into a Daytona sandbox workspace.

    Returns the relative path (under the workspace root) of the cloned repo.
    """
    if ref.backend != DAYTONA_BACKEND:
        raise ValueError(f"Unsupported workspace backend: {ref.backend}")
    backend = DaytonaWorkspaceBackend(ref)
    return backend.clone_repository(
        owner=owner,
        repo=repo,
        default_branch=default_branch,
        token=token,
        depth=depth,
    )


def _file_info_name(item: Any) -> str:
    raw_name = getattr(item, "name", "")
    return str(raw_name or "").strip().rstrip("/")


def _file_info_is_dir(item: Any) -> bool:
    value = getattr(item, "is_dir", None)
    if value is None:
        value = getattr(item, "is_directory", None)
    return bool(value)


def _file_info_size(item: Any) -> int:
    try:
        return int(getattr(item, "size", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _file_info_modified_at(item: Any) -> float:
    value = getattr(item, "mod_time", None)
    if value is None:
        value = getattr(item, "modified_at", None)
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    timestamp = getattr(value, "timestamp", None)
    if callable(timestamp):
        try:
            return float(timestamp())
        except Exception:
            return 0.0
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _file_info_path(item: Any, parent: str) -> str:
    path_value = str(getattr(item, "path", "") or "").strip()
    if path_value:
        return _normalize_posix_path(path_value)
    name = _file_info_name(item)
    if name == parent or name.startswith(f"{parent}/") or posixpath.isabs(name):
        return _normalize_posix_path(name)
    return _normalize_posix_path(posixpath.join(parent, name))


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


def _parse_git_status(
    output: str,
    *,
    workspace_root: str,
    git_root: str,
) -> list[dict[str, Any]]:
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
        absolute_path = _normalize_posix_path(posixpath.join(git_root, raw_path))
        if absolute_path != workspace_root and not absolute_path.startswith(
            f"{workspace_root}/"
        ):
            continue
        entry = {
            "path": absolute_path,
            "status": _status_label(code),
            "index_status": code[0],
            "working_tree_status": code[1],
        }
        if old_path:
            old_absolute = _normalize_posix_path(posixpath.join(git_root, old_path))
            if old_absolute == workspace_root or old_absolute.startswith(
                f"{workspace_root}/"
            ):
                entry["old_path"] = old_absolute
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


def _git_relative_path(git_root: str, target: str) -> str:
    target_path = _normalize_posix_path(target)
    root = _normalize_posix_path(git_root)
    if target_path == root:
        return ""
    if not target_path.startswith(f"{root}/"):
        raise ValueError("Path escapes Git repository.")
    return target_path[len(root) + 1 :]


def _truncate_diff(diff: str) -> tuple[str, bool]:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False
    return diff[:MAX_DIFF_CHARS] + "\n...[truncated]", True


__all__ = [
    "DAYTONA_BACKEND",
    "DEFAULT_DAYTONA_ROOT",
    "DaytonaWorkspaceBackend",
    "archive_daytona_workspace",
    "attach_daytona_workspace",
    "clone_repository_in_daytona_sandbox",
    "create_daytona_workspace",
    "delete_daytona_workspace",
    "ensure_daytona_workspace_active",
    "get_daytona_client",
    "resolve_daytona_path",
    "start_daytona_lifecycle_sweeper",
    "stop_daytona_lifecycle_sweeper",
    "stop_daytona_workspace",
    "sweep_idle_daytona_workspaces",
]
