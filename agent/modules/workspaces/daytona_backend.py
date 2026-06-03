from __future__ import annotations

import asyncio
import logging
import mimetypes
import posixpath
import shlex
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.modules.workspaces.backends import CommandResult
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


def _daytona_client_config() -> dict[str, Any]:
    from agent.shared.config.service import get_config_service

    service = get_config_service()
    return {
        "target": service.get_str("workspace.daytona.target", "").strip() or None,
    }


def _sandbox_create_config() -> dict[str, Any]:
    from agent.shared.config.service import get_config_service

    service = get_config_service()

    image = service.get_str("workspace.daytona.image", "").strip()
    cpu = max(0, service.get_int("workspace.daytona.cpu", 0))
    memory = max(0, service.get_int("workspace.daytona.memory", 0))
    disk = max(0, service.get_int("workspace.daytona.disk", 0))
    language = (
        service.get_str("workspace.daytona.language", "python").strip() or "python"
    )
    auto_stop = max(
        0, service.get_int("workspace.daytona.sandbox_auto_stop_minutes", 15)
    )
    auto_archive = max(
        0, service.get_int("workspace.daytona.sandbox_auto_archive_minutes", 10080)
    )
    auto_delete = service.get_int("workspace.daytona.sandbox_auto_delete_minutes", -1)
    ephemeral = service.get_bool("workspace.daytona.ephemeral", False)
    network_block_all = service.get_bool("workspace.daytona.network_block_all", False)
    network_allow_list = (
        service.get_str("workspace.daytona.network_allow_list", "").strip()
    )

    try:
        from daytona import (
            CreateSandboxFromImageParams,
            CreateSandboxFromSnapshotParams,
            Resources,
        )
    except ImportError:
        from daytona_sdk import (  # type: ignore[no-redef]
            CreateSandboxFromImageParams,
            CreateSandboxFromSnapshotParams,
            Resources,
        )

    resources = None
    if cpu > 0 or memory > 0 or disk > 0:
        resources = Resources(
            cpu=cpu if cpu > 0 else None,
            memory=memory if memory > 0 else None,
            disk=disk if disk > 0 else None,
        )

    base_kwargs: dict[str, Any] = {}
    base_kwargs["language"] = language
    if auto_stop >= 0:
        base_kwargs["auto_stop_interval"] = auto_stop
    if auto_archive >= 0:
        base_kwargs["auto_archive_interval"] = auto_archive
    base_kwargs["auto_delete_interval"] = auto_delete
    if ephemeral:
        base_kwargs["ephemeral"] = True
    if network_block_all:
        base_kwargs["network_block_all"] = True
    if network_allow_list:
        base_kwargs["network_allow_list"] = network_allow_list

    if image:
        params = CreateSandboxFromImageParams(
            image=image, resources=resources, **base_kwargs
        )
    else:
        params = CreateSandboxFromSnapshotParams(**base_kwargs)

    return {"params": params}


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
    client_cfg = _daytona_client_config()
    config_kwargs: dict[str, Any] = {"api_key": api_key}
    if client_cfg.get("target"):
        config_kwargs["target"] = client_cfg["target"]
    return Daytona(DaytonaConfig(**config_kwargs))


def create_daytona_workspace(*, label: str | None = None) -> WorkspaceRef:
    _, _, root = _config()
    client = get_daytona_client()
    create_cfg = _sandbox_create_config()
    sandbox = client.create(create_cfg["params"])
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
            root=backend.root,
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
            root=backend.root,
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
                        root=str(
                            workspace.metadata.get("root") or DEFAULT_DAYTONA_ROOT
                        ),
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


class DaytonaWorkspaceBackend(SandboxBackendBase):
    def __init__(
        self,
        ref: WorkspaceRef,
        *,
        sandbox: Any | None = None,
        thread_id: str | None = None,
    ) -> None:
        super().__init__(ref)
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
        (for example ``"repo"``).
        """
        if not owner or not repo:
            raise ValueError("Repository owner and name are required.")
        self.ensure_active()
        self.ensure_root()
        relative = repo
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
        """Return directory listing for *path* (Daytona override for sync fs API)."""
        from agent.modules.workspaces.posix_utils import resolve_remote_path

        self.ensure_active()
        target = resolve_remote_path(self.root, path or ".")
        entries: list[dict[str, Any]] = []
        truncated = False
        for item in self.fs.list_files(target):
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
        return {
            "root": self.root,
            "path": target,
            "entries": entries,
            "truncated": truncated,
        }

    async def file(self, path: str) -> dict[str, Any]:
        """Return file content and metadata (Daytona override for sync fs API)."""
        from agent.modules.workspaces.posix_utils import resolve_remote_path

        self.ensure_active()
        remote_path = resolve_remote_path(self.root, path)
        info = self.fs.get_file_info(remote_path)
        if file_info_is_dir(info):
            raise ValueError(f"Path is not a file: {path}")
        size = file_info_size(info)
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
        """Return git status for the workspace (Daytona override for sync API)."""
        self.ensure_active()
        git_root = self._find_git_root()
        if git_root is None:
            return {
                "root": self.root,
                "is_git_repo": False,
                "changes": [],
                "message": "Workspace is not a Git repository.",
            }
        output = self._run_git(git_status_args(), cwd=self.root)
        changes = parse_git_status(output, workspace_root=self.root, git_root=git_root)
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
        """Return git diff for a single file (Daytona override for sync API)."""
        from agent.modules.workspaces.posix_utils import (
            relative_remote_path,
            resolve_remote_path,
        )

        self.ensure_active()
        remote_path = resolve_remote_path(self.root, path)
        relative = relative_remote_path(self.root, remote_path)
        if not relative:
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
            for change in parse_git_status(
                self._run_git(git_status_args(), cwd=self.root),
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
            staged = self._run_git(
                ["diff", "--cached", "--no-ext-diff", "--", gpath],
                cwd=git_root,
            )
            unstaged = self._run_git(
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
        """Rename a file or directory (Daytona override for sync API)."""
        from agent.modules.workspaces.posix_utils import (
            relative_remote_path,
            resolve_remote_path,
        )

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
            posixpath.join(posixpath.dirname(relative), clean_name),
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
        """Delete a file or directory (Daytona override for sync API)."""
        from agent.modules.workspaces.posix_utils import (
            relative_remote_path,
            resolve_remote_path,
        )

        self.ensure_active()
        target = resolve_remote_path(self.root, path)
        relative = relative_remote_path(self.root, target)
        if not relative:
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

    def _make_directory(self, path: str) -> None:
        self._exec(f"mkdir -p {shlex.quote(path)}", cwd="/")

    def _stat_file(self, path: str) -> Any:
        return self.fs.get_file_info(path)

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
        """Compute addition/deletion line counts for a single change."""
        from agent.modules.workspaces.git_utils import (
            compute_change_line_stats_from_numstat,
            git_relative_path,
        )

        path = str(change.get("path") or "")
        if not path:
            return 0, 0
        if change.get("status") == "untracked":
            return self._text_line_count(path), 0
        gpath = git_relative_path(git_root, path)
        additions = 0
        deletions = 0
        for args in (
            ["diff", "--numstat", "--cached", "--no-ext-diff", "--", gpath],
            ["diff", "--numstat", "--no-ext-diff", "--", gpath],
        ):
            try:
                output = self._run_git(args, cwd=git_root)
            except Exception:
                continue
            a, d = compute_change_line_stats_from_numstat(output)
            additions += a
            deletions += d
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
        from agent.modules.workspaces.git_utils import build_untracked_diff_content

        return build_untracked_diff_content(content, relative_path)


def _normalize_posix_path(value: str) -> str:
    return normalize_posix_path(value)


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
    return file_info_name(item)


def _file_info_is_dir(item: Any) -> bool:
    return file_info_is_dir(item)


def _file_info_size(item: Any) -> int:
    return file_info_size(item)


def _file_info_modified_at(item: Any) -> float:
    return file_info_modified_at(item)


def _file_info_path(item: Any, parent: str) -> str:
    return file_info_path(item, parent)


def _git_status_args() -> list[str]:
    from agent.modules.workspaces.git_utils import git_status_args

    return git_status_args()


def _parse_git_status(
    output: str,
    *,
    workspace_root: str,
    git_root: str,
) -> list[dict[str, Any]]:
    from agent.modules.workspaces.git_utils import parse_git_status

    return parse_git_status(output, workspace_root=workspace_root, git_root=git_root)


def _status_label(code: str) -> str:
    from agent.modules.workspaces.git_utils import status_label

    return status_label(code)


def _git_relative_path(git_root: str, target: str) -> str:
    from agent.modules.workspaces.git_utils import git_relative_path

    return git_relative_path(git_root, target)


def _truncate_diff(diff: str) -> tuple[str, bool]:
    from agent.modules.workspaces.git_utils import truncate_diff

    return truncate_diff(diff)


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
