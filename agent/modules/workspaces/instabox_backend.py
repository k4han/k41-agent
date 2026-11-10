"""Workspace backend running on an Instabox machine through the REST API."""

from __future__ import annotations

import logging
import posixpath
import shlex
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from agent.modules.instabox import (
    InstaboxApiError,
    InstaboxDisabledError,
    get_instabox_service,
    normalize_machine_state,
    redact,
)
from agent.modules.workspaces.backends import CommandResult, WorkspaceUnavailableError
from agent.modules.workspaces.constants import GIT_TIMEOUT_SECONDS, MAX_LIST_FILES_ENTRIES
from agent.modules.workspaces.git_utils import WORKSPACE_STORAGE_EXCLUDE_COMMAND
from agent.modules.workspaces.posix_utils import normalize_posix_path, resolve_remote_path
from agent.modules.workspaces.refs import WorkspaceRef
from agent.modules.workspaces.sandbox_backend import SandboxBackendBase
from agent.modules.workspaces.search_utils import (
    build_sandbox_glob_command,
    build_sandbox_grep_command,
    render_sandbox_glob_output,
    render_sandbox_grep_output,
)

logger = logging.getLogger(__name__)

INSTABOX_BACKEND = "instabox"
DEFAULT_INSTABOX_ROOT = "/data/home/workspace"
HARD_MAX_TIMEOUT = 600
HARD_MIN_TIMEOUT = 1
HARD_MAX_OUTPUT_CHARS = 200_000
ACTIVE_CHECK_TTL_SECONDS = 20.0
MIN_PYTHON = (3, 11)


def _clamp_timeout(value: int | float) -> int:
    try:
        number = int(float(value))
    except Exception:
        return 30
    return max(HARD_MIN_TIMEOUT, min(number, HARD_MAX_TIMEOUT))


def _clamp_max_output(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except Exception:
        return None
    return max(1024, min(number, HARD_MAX_OUTPUT_CHARS))


def _truncate_tail(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    dropped = len(text) - max_chars
    return f"[...truncated {dropped} characters...]\n{text[-max_chars:]}", True


def _default_root() -> str:
    try:
        return get_instabox_service().settings().workspace_root or DEFAULT_INSTABOX_ROOT
    except Exception:
        return DEFAULT_INSTABOX_ROOT


def instabox_metadata(*, root: str | None = None, status: str | None = None, touch: bool = False) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if root is not None:
        metadata["root"] = normalize_posix_path(root if root.startswith("/") else "/" + root)
    if status is not None:
        metadata["status"] = status
    if touch:
        metadata["last_used_at"] = datetime.now(timezone.utc).isoformat()
    return metadata


def _unavailable(ref: WorkspaceRef, exc: Exception) -> WorkspaceUnavailableError:
    return WorkspaceUnavailableError(
        f"Instabox machine {ref.locator} is no longer available. Create or attach a new Instabox workspace.",
        backend=INSTABOX_BACKEND,
        locator=ref.locator,
    )


def _file_info(item: dict[str, Any], parent: str | None = None) -> SimpleNamespace:
    path = str(item.get("path") or "")
    name = str(item.get("name") or (path.rsplit("/", 1)[-1] if path else ""))
    if not path and parent is not None and name:
        path = posixpath.join(parent, name)
    kind = str(item.get("type") or "")
    return SimpleNamespace(
        name=name,
        path=normalize_posix_path(path) if path else "",
        is_dir=kind == "dir",
        type=kind,
        size=item.get("size") or 0,
        mod_time=item.get("modTime"),
    )


# ---------------------------------------------------------------------- #
#  Loaders referenced by the workspace backend descriptor
# ---------------------------------------------------------------------- #


async def create_instabox_workspace(
    *, label: str | None = None, on_created=None, snapshot_id: str | None = None
) -> WorkspaceRef:
    service = get_instabox_service()
    settings = service.settings()
    from agent.modules.workspaces.transitions import RuntimeTransitionCoordinator

    operation_id = RuntimeTransitionCoordinator.operation_id(label)
    metadata = {"k41_root": settings.workspace_root}
    if operation_id:
        metadata["k41_operation_id"] = operation_id
    if label:
        metadata["k41_label"] = str(label)[:200]
    record = await service.create_machine(
        name=None, snapshot_id=snapshot_id, metadata=metadata, wait=True
    )
    machine_id = str(record.get("machine_id") or "")
    if record.get("status") != "succeeded" or not machine_id:
        error = record.get("error") or {}
        raise RuntimeError(f"Instabox machine creation failed: {redact(str(error.get('message') or error or record.get('status')))}")
    ref = WorkspaceRef(
        backend=INSTABOX_BACKEND,
        locator=machine_id,
        label=(label or "").strip() or f"instabox:{machine_id}",
        metadata=instabox_metadata(root=settings.workspace_root, status="started", touch=True),
    )
    if on_created is not None:
        await on_created(ref)
    backend = InstaboxWorkspaceBackend(ref)
    await backend.prepare()
    return ref


async def attach_instabox_workspace(
    sandbox_id: str, *, label: str | None = None, root: str | None = None
) -> WorkspaceRef:
    machine_id = str(sandbox_id or "").strip()
    if not machine_id:
        raise ValueError("Instabox machine ID is required.")
    service = get_instabox_service()
    machine = await service.ensure_running(machine_id)
    # Accept a machine name as well and store the stable ID.
    machine_id = str(machine.get("id") or machine_id)
    selected_root = (root or "").strip() or service.settings().workspace_root
    ref = WorkspaceRef(
        backend=INSTABOX_BACKEND,
        locator=machine_id,
        label=(label or "").strip() or f"instabox:{machine.get('name') or machine_id}",
        metadata=instabox_metadata(root=selected_root, status="started", touch=True),
    )
    backend = InstaboxWorkspaceBackend(ref)
    await backend.prepare()
    return ref


async def create_instabox_backend(ref: WorkspaceRef, *, thread_id: str | None = None) -> "InstaboxWorkspaceBackend":
    return InstaboxWorkspaceBackend(ref, thread_id=thread_id)


async def delete_instabox_workspace(ref: WorkspaceRef, *, thread_id: str | None = None) -> str:
    service = get_instabox_service()
    try:
        await service.delete_machine(ref.locator, confirm=ref.locator, wait=True)
    except InstaboxApiError as exc:
        if exc.is_not_found:
            return "destroyed"
        raise
    return "destroyed"


async def stop_instabox_workspace(ref: WorkspaceRef, *, thread_id: str | None = None, force: bool = False) -> str:
    service = get_instabox_service()
    client = await service.client()
    try:
        machine = await client.get_machine(ref.locator)
    except InstaboxApiError as exc:
        if exc.is_not_found:
            return "destroyed"
        raise
    state = str(machine.get("state") or "")
    if state == "stopped":
        return "stopped"
    if state == "suspended":
        # A paused machine cannot be stopped directly; resume it first.
        await service.machine_action(ref.locator, "resume", wait=True)
    await service.machine_action(ref.locator, "stop", wait=True)
    return "stopped"


async def archive_instabox_workspace(ref: WorkspaceRef, *, thread_id: str | None = None) -> str:
    """Pause the machine; Instabox keeps memory and running programs."""
    service = get_instabox_service()
    client = await service.client()
    try:
        machine = await client.get_machine(ref.locator)
    except InstaboxApiError as exc:
        if exc.is_not_found:
            return "destroyed"
        raise
    if machine.get("state") in {"suspended", "stopped"}:
        return normalize_machine_state(machine.get("state"))
    await service.machine_action(ref.locator, "suspend", wait=True)
    return "paused"


async def probe_instabox_sandbox(sandbox_id: str, **_: Any) -> dict[str, Any] | None:
    """Read-only status probe. ``None`` means unknown: keep the last status."""
    try:
        service = get_instabox_service()
        client = await service.client()
        machine = await client.get_machine(str(sandbox_id))
    except InstaboxApiError as exc:
        if exc.is_not_found:
            return {"status": "destroyed", "on_cloud": False}
        return None
    except Exception:
        return None
    status = normalize_machine_state(machine.get("state"))
    return {"status": status, "on_cloud": status != "destroyed"}


async def list_instabox_cloud_sandboxes() -> list[dict[str, Any]] | None:
    """List machines created by this app; ``None`` signals an unreachable provider."""
    try:
        service = get_instabox_service()
        machines = await service.list_machines()
    except InstaboxDisabledError:
        return []
    except Exception as exc:
        logger.warning("Instabox machine listing failed: %s", redact(str(exc)))
        return None
    results: list[dict[str, Any]] = []
    for machine in machines:
        machine_id = str(machine.get("id") or "")
        if not machine_id:
            continue
        metadata = {str(k): str(v) for k, v in (machine.get("metadata") or {}).items()}
        status = machine.get("status") or "unknown"
        results.append(
            {
                "sandbox_id": machine_id,
                "backend": INSTABOX_BACKEND,
                "label": metadata.get("k41_label") or machine.get("name") or f"instabox:{machine_id}",
                "root": metadata.get("k41_root") or DEFAULT_INSTABOX_ROOT,
                "status": status,
                "state": machine.get("state"),
                "thread_id": metadata.get("thread_id") or None,
                "repository_full_name": metadata.get("repository_full_name") or None,
                "last_used_at": machine.get("updated_at"),
                "last_started_at": None,
                "last_stopped_at": None,
                "last_archived_at": None,
                "created_at": machine.get("created_at"),
                "updated_at": machine.get("updated_at"),
                "on_cloud": status != "destroyed",
                "is_orphan": True,
                "metadata": metadata,
                "name": machine.get("name"),
                "template": machine.get("template"),
                "snapshot": machine.get("snapshot"),
            }
        )
    return results


# ---------------------------------------------------------------------- #
#  Backend
# ---------------------------------------------------------------------- #


class InstaboxWorkspaceBackend(SandboxBackendBase):
    """Workspace backend backed by an Instabox machine."""

    def __init__(self, ref: WorkspaceRef, *, thread_id: str | None = None, service: Any = None) -> None:
        super().__init__(ref)
        self.thread_id = thread_id
        self._service = service
        root = str(ref.metadata.get("root") or _default_root())
        self.root = normalize_posix_path(root if root.startswith("/") else "/" + root)
        self._active_checked_at = 0.0
        self.ref.metadata.update(instabox_metadata(root=self.root))

    @property
    def service(self):
        if self._service is None:
            self._service = get_instabox_service()
        return self._service

    @property
    def machine_id(self) -> str:
        return self.ref.locator

    async def _client(self):
        return await self.service.client()

    # Lifecycle ------------------------------------------------------- #

    async def ensure_active(self) -> None:
        if time.monotonic() - self._active_checked_at < ACTIVE_CHECK_TTL_SECONDS:
            return
        try:
            if self.ref.metadata.get("_require_running"):
                machine = await (await self._client()).get_machine(self.machine_id)
                if machine.get("state") != "running":
                    raise WorkspaceUnavailableError(
                        "Instabox machine is not running.", backend=INSTABOX_BACKEND, locator=self.machine_id
                    )
            else:
                await self.service.ensure_running(self.machine_id)
        except InstaboxApiError as exc:
            if exc.is_not_found:
                raise _unavailable(self.ref, exc) from exc
            raise
        self.ref.metadata.update(instabox_metadata(status="started", touch=True))
        self._active_checked_at = time.monotonic()

    def touch(self) -> None:
        self.ref.metadata.update(instabox_metadata(touch=True))

    async def prepare(self) -> None:
        await self.ensure_active()
        await self.check_runtime()
        await self.ensure_root()
        await self.ensure_storage()

    async def check_runtime(self) -> None:
        """Coding tools need ``python3 >= 3.11`` and Git inside the machine."""
        python = await self._exec(
            "python3 -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])'", cwd="/", timeout=20
        )
        version = (python.output or "").strip().splitlines()[-1:] if python.exit_code == 0 else []
        try:
            major, minor = (int(part) for part in version[0].split(".")[:2])
        except Exception:
            raise RuntimeError("The Instabox machine needs python3 3.11 or newer for coding tools.") from None
        if (major, minor) < MIN_PYTHON:
            raise RuntimeError(f"The Instabox machine has Python {major}.{minor}; coding tools need 3.11 or newer.")
        git = await self._exec("git --version", cwd="/", timeout=20)
        if git.exit_code != 0:
            raise RuntimeError("The Instabox machine needs Git installed.")

    async def ensure_root(self) -> None:
        await self._make_directory(self.root)

    async def ensure_storage(self) -> None:
        try:
            await self._exec(WORKSPACE_STORAGE_EXCLUDE_COMMAND, cwd=self.root, timeout=10)
        except Exception as exc:
            logger.debug("Failed to ensure instabox git exclude: %s", exc)

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
        await self.ensure_active()
        await self.ensure_root()
        relative = repo
        destination = resolve_remote_path(self.root, relative)
        branch = (default_branch or "main").strip() or "main"
        depth_flag = f"--depth={int(depth)}" if depth and int(depth) > 0 else ""
        url = (
            f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
            if token
            else f"https://github.com/{owner}/{repo}.git"
        )
        await self._make_directory(posixpath.dirname(destination))
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
        result = await self._exec(command, cwd="/", timeout=300)
        if result.exit_code not in (0, None):
            detail = (result.output or "").lower()
            if "could not find" in detail or "not found" in detail:
                fallback = f"git clone {depth_flag} --single-branch {shlex.quote(url)} {shlex.quote(destination)}"
                result = await self._exec(fallback, cwd="/", timeout=300)
        if result.exit_code not in (0, None):
            output = result.output.replace(token, "[REDACTED]") if token else result.output
            raise RuntimeError(f"git clone failed for {owner}/{repo}: {output.strip()}")
        return relative

    # Browsing -------------------------------------------------------- #

    async def list_dir(self, path: str = "") -> str:
        self.touch()
        target = resolve_remote_path(self.root, path or ".")
        cached, value = self._get_metadata_cache("list_dir", target)
        if cached:
            return value
        result = await self._exec(f"ls -1 {shlex.quote(target)}", timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Failed to list Instabox directory.")
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

    async def glob(self, pattern: str, *, path: str = "", include_dirs: bool = False) -> str:
        self.touch()
        if not pattern:
            raise ValueError("Glob pattern must not be empty.")
        target = resolve_remote_path(self.root, path or ".")
        command = build_sandbox_glob_command(
            root=self.root, target=target, pattern=pattern, include_dirs=include_dirs
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
        from agent.modules.workspaces.posix_utils import relative_remote_path

        self.touch()
        if not pattern:
            raise ValueError("Grep pattern must not be empty.")
        target = resolve_remote_path(self.root, path or ".")
        command = build_sandbox_grep_command(
            root=self.root,
            target=target,
            relative_path=relative_remote_path(self.root, target),
            pattern=pattern,
            include=include,
            case_insensitive=case_insensitive,
            max_results=max_results,
        )
        result = await self._exec(command, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Grep failed.")
        return render_sandbox_grep_output(result.output, max_results=max_results)

    async def execute(self, command: str, *, timeout: int = 30, max_output_chars: int | None = None) -> CommandResult:
        self.touch()
        self._invalidate_workspace_caches()
        return await self._exec(
            command,
            cwd=self.root,
            timeout=_clamp_timeout(timeout),
            max_output_chars=_clamp_max_output(max_output_chars),
        )

    # Primitives ------------------------------------------------------ #

    async def _exec(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        timeout = _clamp_timeout(timeout)
        max_output_chars = _clamp_max_output(max_output_chars)
        client = await self._client()
        try:
            result = await client.exec(self.machine_id, cmd=command, cwd=cwd or self.root, timeout_seconds=timeout)
        except InstaboxApiError as exc:
            if exc.is_not_found:
                raise _unavailable(self.ref, exc) from exc
            if exc.code != "machine_not_running" or self.ref.metadata.get("_require_running"):
                raise
            # The command was refused before running, so it is safe to send again after a resume.
            self._active_checked_at = 0.0
            await self.ensure_active()
            result = await client.exec(self.machine_id, cmd=command, cwd=cwd or self.root, timeout_seconds=timeout)
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        combined = stdout + (f"\n[stderr]: {stderr}" if stderr else "")
        if result.get("truncated"):
            combined += "\n[output truncated by Instabox at 1 MiB]"
        if result.get("timedOut"):
            combined += f"\n[command timed out after {timeout} seconds]"
        truncated = bool(result.get("truncated"))
        if max_output_chars is not None and len(combined) > max_output_chars:
            combined, truncated = _truncate_tail(combined, max_output_chars)
        exit_code = result.get("exitCode")
        return CommandResult(
            output=combined,
            exit_code=int(exit_code) if exit_code is not None else None,
            truncated=truncated,
        )

    async def _download_file(self, path: str) -> bytes:
        client = await self._client()
        try:
            return await client.read_file(self.machine_id, path)
        except InstaboxApiError as exc:
            if exc.status == 404 and exc.code not in {"machine_not_found"}:
                raise FileNotFoundError(path) from exc
            if exc.is_not_found:
                raise _unavailable(self.ref, exc) from exc
            raise

    async def _upload_file(self, content: bytes, path: str) -> None:
        client = await self._client()
        try:
            await client.write_file(self.machine_id, path, content)
        except InstaboxApiError as exc:
            if exc.code == "machine_not_found":
                raise _unavailable(self.ref, exc) from exc
            raise

    async def _make_directory(self, path: str) -> None:
        result = await self._exec(f"mkdir -p {shlex.quote(path)}", cwd="/")
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or f"Failed to create directory {path}.")

    async def _stat_file(self, path: str) -> SimpleNamespace:
        client = await self._client()
        try:
            payload = await client.stat_path(self.machine_id, path)
        except InstaboxApiError as exc:
            if exc.status == 404 and exc.code != "machine_not_found":
                raise FileNotFoundError(path) from exc
            if exc.is_not_found:
                raise _unavailable(self.ref, exc) from exc
            raise
        info = _file_info(payload)
        if not info.path:
            info.path = normalize_posix_path(path)
        return info

    async def _list_remote_files(self, path: str) -> list[SimpleNamespace]:
        client = await self._client()
        try:
            payload = await client.stat_path(self.machine_id, path)
        except InstaboxApiError as exc:
            if exc.status == 404 and exc.code != "machine_not_found":
                raise FileNotFoundError(path) from exc
            raise
        if str(payload.get("type") or "") != "dir":
            raise NotADirectoryError(f"Path is not a directory: {path}")
        return [_file_info(item, path) for item in payload.get("entries") or []]


__all__ = [
    "INSTABOX_BACKEND",
    "InstaboxWorkspaceBackend",
    "archive_instabox_workspace",
    "attach_instabox_workspace",
    "create_instabox_backend",
    "create_instabox_workspace",
    "delete_instabox_workspace",
    "instabox_metadata",
    "list_instabox_cloud_sandboxes",
    "probe_instabox_sandbox",
    "stop_instabox_workspace",
]
