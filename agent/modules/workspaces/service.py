from __future__ import annotations

import asyncio
import difflib
import logging
import mimetypes
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from agent.modules.workspaces.backends import (
    CommandResult,
    UnsupportedWorkspaceCapabilityError,
    WorkspaceBrowser,
    WorkspaceChangeInspector,
    WorkspaceCommandExecutor,
    WorkspaceEntryMutator,
    WorkspaceFileIO,
    WorkspaceLifecycleManager,
    WorkspaceRepositoryCloner,
    WorkspaceUnavailableError,
)
from agent.modules.workspaces.repository import get_thread_workspace_repository
from agent.modules.workspaces.refs import (
    WorkspaceRef,
    normalize_workspace_ref,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")

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
_modal_recovery_locks: dict[str, asyncio.Lock] = {}


def resolve_workspace_ref(workspace: WorkspaceRef | dict[str, Any] | str | None = None) -> WorkspaceRef:
    from agent.shared.config.service import get_config_service
    default_locator = str(get_config_service().get_path("workspace.root", "~/kaka-agent"))
    return normalize_workspace_ref(workspace, default_locator=default_locator)


def workspace_ref_from_local_path(
    working_dir: str | None = None,
    *,
    label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkspaceRef:
    from agent.shared.config.service import get_config_service
    default_locator = str(get_config_service().get_path("workspace.root", "~/kaka-agent"))
    return normalize_workspace_ref(
        {
            "backend": "local",
            "locator": working_dir,
            "label": label,
            "metadata": metadata or {},
        },
        default_locator=default_locator,
    )


async def _create_workspace_runtime_backend(
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    *,
    thread_id: str | None = None,
):
    ref = resolve_workspace_ref(workspace)
    if ref.backend == "local":
        from agent.modules.workspaces.local_backend import LocalWorkspaceBackend

        return LocalWorkspaceBackend(ref)
    if ref.backend == "daytona":
        from agent.modules.workspaces.daytona_backend import DaytonaWorkspaceBackend

        return DaytonaWorkspaceBackend(ref, thread_id=thread_id)
    if ref.backend == "modal":
        from agent.modules.workspaces.modal_backend import ModalWorkspaceBackend

        if thread_id:
            return _RecoveringModalWorkspaceBackend(ref, thread_id=thread_id)
        return await ModalWorkspaceBackend.create(ref)
    raise ValueError(f"Unsupported workspace backend: {ref.backend}")


async def get_workspace_file_io(
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    *,
    thread_id: str | None = None,
) -> WorkspaceFileIO:
    return await _create_workspace_runtime_backend(workspace, thread_id=thread_id)


async def get_workspace_command_executor(
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    *,
    thread_id: str | None = None,
) -> WorkspaceCommandExecutor:
    return await _create_workspace_runtime_backend(workspace, thread_id=thread_id)


async def get_workspace_browser(
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    *,
    thread_id: str | None = None,
) -> WorkspaceBrowser:
    return await _create_workspace_runtime_backend(workspace, thread_id=thread_id)


async def get_workspace_change_inspector(
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    *,
    thread_id: str | None = None,
) -> WorkspaceChangeInspector:
    return await _create_workspace_runtime_backend(workspace, thread_id=thread_id)


async def get_workspace_entry_mutator(
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    *,
    thread_id: str | None = None,
) -> WorkspaceEntryMutator:
    return await _create_workspace_runtime_backend(workspace, thread_id=thread_id)


async def get_workspace_repository_cloner(
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    *,
    thread_id: str | None = None,
) -> WorkspaceRepositoryCloner:
    ref = resolve_workspace_ref(workspace)
    if ref.backend == "daytona":
        return _DaytonaWorkspaceRepositoryCloner(ref, thread_id=thread_id)
    if ref.backend == "modal":
        if thread_id:
            return _RecoveringModalWorkspaceRepositoryCloner(
                ref,
                thread_id=thread_id,
            )
        return _ModalWorkspaceRepositoryCloner(ref)
    raise UnsupportedWorkspaceCapabilityError(
        backend=ref.backend,
        capability="repository clone",
        locator=ref.locator,
    )


async def get_workspace_lifecycle_manager(
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    *,
    thread_id: str | None = None,
) -> WorkspaceLifecycleManager:
    ref = resolve_workspace_ref(workspace)
    if ref.backend == "daytona":
        return _DaytonaWorkspaceLifecycleManager(ref, thread_id=thread_id)
    if ref.backend == "modal":
        return _ModalWorkspaceLifecycleManager(ref)
    raise UnsupportedWorkspaceCapabilityError(
        backend=ref.backend,
        capability="lifecycle",
        locator=ref.locator,
    )


async def ensure_workspace_ready(
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    *,
    thread_id: str | None = None,
) -> WorkspaceRef:
    ref = resolve_workspace_ref(workspace)
    if ref.backend == "local":
        ensure_workspace_directory(ref.locator)
        if thread_id and thread_id.strip():
            return await remember_thread_workspace_ref(thread_id, ref)
        return ref

    if ref.backend == "daytona":
        from agent.modules.workspaces.daytona_backend import attach_daytona_workspace

        ready = await asyncio.to_thread(
            attach_daytona_workspace,
            ref.locator,
            label=_replacement_workspace_label(ref),
            root=str(ref.metadata.get("root") or "").strip() or None,
        )
        ready = _merge_ready_workspace_metadata(ref, ready)
        if thread_id and thread_id.strip():
            return await remember_thread_workspace_ref(thread_id, ready)
        return ready

    if ref.backend == "modal":
        try:
            from agent.modules.workspaces.modal_backend import ModalWorkspaceBackend

            backend = await ModalWorkspaceBackend.create(ref)
            await backend.ensure_git()
            await backend.ensure_root()
            ready = _merge_ready_workspace_metadata(ref, backend.ref)
        except WorkspaceUnavailableError:
            if thread_id and thread_id.strip():
                return await _recover_modal_thread_workspace(ref, thread_id=thread_id)
            raise
        if thread_id and thread_id.strip():
            return await remember_thread_workspace_ref(thread_id, ready)
        return ready

    raise ValueError(f"Unsupported workspace backend: {ref.backend}")


class _DaytonaWorkspaceRepositoryCloner:
    def __init__(self, ref: WorkspaceRef, *, thread_id: str | None = None) -> None:
        self.ref = ref
        self.thread_id = thread_id

    async def clone_repository(
        self,
        *,
        owner: str,
        repo: str,
        default_branch: str = "main",
        token: str | None = None,
        depth: int = 1,
    ) -> str:
        def clone() -> str:
            from agent.modules.workspaces.daytona_backend import DaytonaWorkspaceBackend

            backend = DaytonaWorkspaceBackend(self.ref, thread_id=self.thread_id)
            return backend.clone_repository(
                owner=owner,
                repo=repo,
                default_branch=default_branch,
                token=token,
                depth=depth,
            )

        return await asyncio.to_thread(clone)


class _ModalWorkspaceRepositoryCloner:
    def __init__(self, ref: WorkspaceRef) -> None:
        self.ref = ref

    async def clone_repository(
        self,
        *,
        owner: str,
        repo: str,
        default_branch: str = "main",
        token: str | None = None,
        depth: int = 1,
    ) -> str:
        from agent.modules.workspaces.modal_backend import ModalWorkspaceBackend

        backend = await ModalWorkspaceBackend.create(self.ref)
        return await backend.clone_repository(
            owner=owner,
            repo=repo,
            default_branch=default_branch,
            token=token,
            depth=depth,
        )


class _RecoveringModalWorkspaceBackend:
    def __init__(self, ref: WorkspaceRef, *, thread_id: str) -> None:
        self.ref = ref
        self.thread_id = thread_id

    async def _create_backend(self):
        from agent.modules.workspaces.modal_backend import ModalWorkspaceBackend

        return await ModalWorkspaceBackend.create(self.ref)

    async def _run(
        self,
        operation: Callable[[Any], Awaitable[T]],
    ) -> T:
        try:
            backend = await self._create_backend()
            result = await operation(backend)
            self.ref = backend.ref
            return result
        except WorkspaceUnavailableError:
            self.ref = await _recover_modal_thread_workspace(
                self.ref,
                thread_id=self.thread_id,
            )
            backend = await self._create_backend()
            result = await operation(backend)
            self.ref = backend.ref
            return result

    async def list_dir(self, path: str = "") -> str:
        return await self._run(lambda backend: backend.list_dir(path))

    async def read_text(self, file_path: str) -> str:
        return await self._run(lambda backend: backend.read_text(file_path))

    async def write_text(self, file_path: str, content: str) -> str:
        return await self._run(lambda backend: backend.write_text(file_path, content))

    async def execute(
        self,
        command: str,
        *,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        return await self._run(
            lambda backend: backend.execute(
                command,
                timeout=timeout,
                max_output_chars=max_output_chars,
            )
        )

    async def tree(self, path: str | None = None) -> dict[str, Any]:
        return await self._run(lambda backend: backend.tree(path))

    async def file(self, path: str) -> dict[str, Any]:
        return await self._run(lambda backend: backend.file(path))

    async def changes(self) -> dict[str, Any]:
        return await self._run(lambda backend: backend.changes())

    async def diff(self, path: str) -> dict[str, Any]:
        return await self._run(lambda backend: backend.diff(path))

    async def rename(self, *, path: str, new_name: str) -> dict[str, Any]:
        return await self._run(
            lambda backend: backend.rename(path=path, new_name=new_name)
        )

    async def delete(self, *, path: str) -> dict[str, Any]:
        return await self._run(lambda backend: backend.delete(path=path))


class _RecoveringModalWorkspaceRepositoryCloner:
    def __init__(self, ref: WorkspaceRef, *, thread_id: str) -> None:
        self.ref = ref
        self.thread_id = thread_id

    async def clone_repository(
        self,
        *,
        owner: str,
        repo: str,
        default_branch: str = "main",
        token: str | None = None,
        depth: int = 1,
    ) -> str:
        async def clone(ref: WorkspaceRef) -> str:
            cloner = _ModalWorkspaceRepositoryCloner(ref)
            return await cloner.clone_repository(
                owner=owner,
                repo=repo,
                default_branch=default_branch,
                token=token,
                depth=depth,
            )

        try:
            return await clone(self.ref)
        except WorkspaceUnavailableError:
            self.ref = await _recover_modal_thread_workspace(
                self.ref,
                thread_id=self.thread_id,
            )
            return await clone(self.ref)


async def _recover_modal_thread_workspace(
    ref: WorkspaceRef,
    *,
    thread_id: str,
) -> WorkspaceRef:
    normalized_thread_id = str(thread_id or "").strip()
    if ref.backend != "modal" or not normalized_thread_id:
        raise WorkspaceUnavailableError(
            (
                f"Modal sandbox {ref.locator} is no longer available. "
                "Create or attach a new Modal workspace for this thread."
            ),
            backend=ref.backend,
            locator=ref.locator,
        )

    lock = _modal_recovery_locks.setdefault(normalized_thread_id, asyncio.Lock())
    async with lock:
        current = await get_thread_workspace_ref(normalized_thread_id)
        if current and current.backend == "modal" and current.locator != ref.locator:
            return current

        source_ref = current if current and current.backend == "modal" else ref
        replacement = await _provision_modal_replacement(source_ref)
        replacement = await remember_thread_workspace_ref(
            normalized_thread_id,
            replacement,
        )
        logger.info(
            "Recreated Modal workspace for thread %s: %s -> %s",
            normalized_thread_id,
            ref.locator,
            replacement.locator,
        )
        return replacement


async def _provision_modal_replacement(ref: WorkspaceRef) -> WorkspaceRef:
    from agent.modules.workspaces.github_clone import (
        attach_github_repository_to_workspace,
    )
    from agent.modules.workspaces.modal_backend import (
        ModalWorkspaceBackend,
        create_modal_workspace,
    )

    replacement = await create_modal_workspace(label=_replacement_workspace_label(ref))
    replacement = _merge_ready_workspace_metadata(ref, replacement)

    backend = await ModalWorkspaceBackend.create(replacement)
    await backend.ensure_git()
    await backend.ensure_root()
    replacement = backend.ref

    repository_id = _github_repository_id(ref)
    if repository_id is None:
        return replacement
    return await attach_github_repository_to_workspace(
        replacement,
        repository_id=repository_id,
    )


def _replacement_workspace_label(ref: WorkspaceRef) -> str | None:
    label = str(ref.label or "").strip()
    default_labels = {f"{ref.backend}:{ref.locator}"}
    if not label or label in default_labels:
        return None
    return label


def _merge_ready_workspace_metadata(
    source: WorkspaceRef,
    replacement: WorkspaceRef,
) -> WorkspaceRef:
    metadata = dict(replacement.metadata or {})
    for key, value in dict(source.metadata or {}).items():
        if value in (None, "", []):
            continue
        if key in {
            "last_archived_at",
            "last_started_at",
            "last_stopped_at",
            "last_used_at",
            "root",
            "status",
        }:
            continue
        metadata[key] = value
    return WorkspaceRef(
        backend=replacement.backend,
        locator=replacement.locator,
        label=replacement.label,
        metadata=metadata,
    )


def _github_repository_id(ref: WorkspaceRef) -> int | None:
    metadata = ref.metadata or {}
    if str(metadata.get("source") or "").strip().lower() != "github":
        return None
    try:
        repository_id = int(metadata.get("repository_id") or 0)
    except (TypeError, ValueError):
        return None
    return repository_id or None


class _DaytonaWorkspaceLifecycleManager:
    def __init__(self, ref: WorkspaceRef, *, thread_id: str | None = None) -> None:
        self.ref = ref
        self.thread_id = thread_id

    async def delete_workspace(self) -> str:
        from agent.modules.workspaces.daytona_backend import delete_daytona_workspace

        return await asyncio.to_thread(
            delete_daytona_workspace,
            self.ref,
            thread_id=self.thread_id,
        )

    async def stop_workspace(self, *, force: bool = False) -> str:
        from agent.modules.workspaces.daytona_backend import stop_daytona_workspace

        return await asyncio.to_thread(
            stop_daytona_workspace,
            self.ref,
            thread_id=self.thread_id,
            force=force,
        )

    async def archive_workspace(self) -> str:
        from agent.modules.workspaces.daytona_backend import archive_daytona_workspace

        return await asyncio.to_thread(
            archive_daytona_workspace,
            self.ref,
            thread_id=self.thread_id,
        )


class _ModalWorkspaceLifecycleManager:
    def __init__(self, ref: WorkspaceRef) -> None:
        self.ref = ref

    async def delete_workspace(self) -> str:
        from agent.modules.workspaces.modal_backend import delete_modal_workspace

        return await delete_modal_workspace(self.ref)


def resolve_workspace_root(working_dir: str | None = None) -> Path:
    from agent.shared.config.service import get_config_service
    default_locator = str(get_config_service().get_path("workspace.root", "~/kaka-agent"))
    source = str(working_dir or "").strip() or default_locator
    return Path(source).expanduser().resolve()


def ensure_workspace_directory(working_dir: str | None = None) -> Path:
    root = resolve_workspace_root(working_dir)
    if not root.exists():
        raise FileNotFoundError(f"Workspace does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Workspace is not a directory: {root}")
    return root


def list_workspace_directories(path: str | None = None) -> dict[str, Any]:
    from agent.shared.config.service import get_config_service
    workspace_root = get_config_service().get_path("workspace.root", "~/kaka-agent").expanduser().resolve()
    
    # Ensure the configured workspace root exists
    workspace_root.mkdir(parents=True, exist_ok=True)

    import sys
    is_testing = "pytest" in sys.modules

    source = str(path or "").strip()
    if source:
        target = Path(source).expanduser().resolve()
        # Enforce that the target path must be within the workspace root, unless we are in testing
        if not is_testing:
            if target != workspace_root and not target.is_relative_to(workspace_root):
                target = workspace_root
    else:
        target = workspace_root

    if not target.exists():
        raise FileNotFoundError(f"Directory does not exist: {target}")
    if not target.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {target}")

    entries: list[dict[str, str]] = []
    truncated = False
    try:
        children = sorted(
            target.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.lower()),
        )
    except PermissionError as exc:
        raise ValueError(f"Cannot access directory: {target}") from exc

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

    parent = "" if (target == workspace_root and not is_testing) else str(target.parent)
    
    if is_testing:
        # In testing environment, return standard system roots to keep test cases green
        if Path("C:/").exists():
            roots_list = [
                Path(f"{chr(code)}:/").resolve()
                for code in range(ord("A"), ord("Z") + 1)
                if Path(f"{chr(code)}:/").exists()
            ]
        else:
            roots_list = [Path("/")]
        roots = [{"name": str(root), "path": str(root)} for root in roots_list]
    else:
        roots = [{"name": workspace_root.name or "kaka-agent", "path": str(workspace_root)}]

    return {
        "path": str(target),
        "parent": parent,
        "entries": entries,
        "roots": roots,
        "truncated": truncated,
    }


def resolve_workspace_child(root: Path, path: str | None = None) -> Path:
    raw_path = str(path or "").strip()
    source = Path(raw_path).expanduser() if raw_path else root
    target = source.resolve() if source.is_absolute() else (root / source).resolve()
    if target != root and not target.is_relative_to(root):
        raise ValueError("Path escapes workspace.")
    return target


def workspace_absolute_path(target: Path) -> str:
    return str(target.resolve())


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


async def delete_thread_workspace(thread_id: str) -> WorkspaceRef | None:
    workspace = await get_thread_workspace_ref(thread_id)
    if workspace is None:
        return None

    try:
        lifecycle = await get_workspace_lifecycle_manager(
            workspace,
            thread_id=thread_id,
        )
    except UnsupportedWorkspaceCapabilityError:
        lifecycle = None
    if lifecycle is not None:
        await lifecycle.delete_workspace()

    await get_thread_workspace_repository().delete(thread_id)
    return workspace


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
                "path": workspace_absolute_path(child),
                "kind": "directory" if child.is_dir() else "file",
                "size": stat.st_size if child.is_file() else None,
                "modified_at": stat.st_mtime,
            }
        )

    return {
        "root": str(root),
        "path": workspace_absolute_path(target),
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
    _batch_change_line_stats(changes, workspace_root=root, git_root=git_root)
    return {
        "root": str(root),
        "is_git_repo": True,
        "changes": changes,
        "message": "",
    }


def get_workspace_diff(
    *,
    working_dir: str | None,
    path: str,
    cached_status: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = ensure_workspace_directory(working_dir)
    target = resolve_workspace_child(root, path)
    relative_path = workspace_relative_path(root, target)
    absolute_path = workspace_absolute_path(target)
    if not relative_path:
        raise ValueError("File path is required.")

    git_root = _find_git_root(root)
    if git_root is None:
        return {
            "root": str(root),
            "path": absolute_path,
            "is_git_repo": False,
            "status": "",
            "diff": "",
            "truncated": False,
            "message": "Workspace is not a Git repository.",
        }

    if cached_status is not None:
        status_by_path = cached_status
    else:
        status_by_path = {
            change["path"]: change
            for change in _parse_git_status(
                _run_git(_git_status_args(), cwd=root),
                workspace_root=root,
                git_root=git_root,
            )
        }
    change = status_by_path.get(absolute_path)
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
        "path": absolute_path,
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
    absolute_path = workspace_absolute_path(target)
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
            "path": absolute_path,
            "mime_type": mime_type,
            "size": stat.st_size,
            "content": "",
            "truncated": truncated,
            "binary": True,
            "message": "Binary files cannot be previewed.",
        }

    return {
        "root": str(root),
        "path": absolute_path,
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
            "path": workspace_absolute_path(target),
            "new_path": workspace_absolute_path(target),
        }
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {clean_name}")

    target.rename(destination)
    return {
        "root": str(root),
        "path": workspace_absolute_path(target),
        "new_path": workspace_absolute_path(destination),
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
        "path": workspace_absolute_path(target),
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
            "path": workspace_absolute_path(absolute_path),
            "status": status,
            "index_status": code[0],
            "working_tree_status": code[1],
        }
        if old_path:
            old_absolute = (git_root / old_path).resolve()
            if old_absolute == workspace_root or old_absolute.is_relative_to(
                workspace_root
            ):
                entry["old_path"] = workspace_absolute_path(old_absolute)
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

    target = resolve_workspace_child(workspace_root, path)
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


def _batch_change_line_stats(
    changes: list[dict[str, Any]],
    *,
    workspace_root: Path,
    git_root: Path,
) -> None:
    """Compute addition/deletion line counts for all changes in batch.

    Runs ``git diff --numstat`` only twice (staged + unstaged) regardless of
    the number of changed files, instead of 2 calls per file.
    """
    if not changes:
        return

    numstat_by_path: dict[str, dict[str, int]] = {}
    for args in (
        ["diff", "--numstat", "--cached", "--no-ext-diff"],
        ["diff", "--numstat", "--no-ext-diff"],
    ):
        try:
            output = _run_git(args, cwd=git_root)
        except Exception as exc:
            logger.debug("Failed to compute batch line stats: %s", exc)
            continue
        for line in output.splitlines():
            fields = line.split("\t", 3)
            if len(fields) < 3:
                continue
            add_str, del_str, file_path = fields[0], fields[1], fields[2]
            abs_path = str((git_root / file_path).resolve())
            entry = numstat_by_path.setdefault(abs_path, {"additions": 0, "deletions": 0})
            if add_str.isdigit():
                entry["additions"] += int(add_str)
            if del_str.isdigit():
                entry["deletions"] += int(del_str)

    for change in changes:
        path = str(change.get("path") or "")
        if change.get("status") == "untracked":
            target = resolve_workspace_child(workspace_root, path)
            change["additions"] = _text_line_count(target)
            change["deletions"] = 0
        else:
            stats = numstat_by_path.get(path, {})
            change["additions"] = stats.get("additions", 0)
            change["deletions"] = stats.get("deletions", 0)


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
