from __future__ import annotations

from collections.abc import Callable
import logging
import posixpath
import shlex
from datetime import datetime, timezone
from typing import Any, TypeVar

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

logger = logging.getLogger(__name__)

MODAL_BACKEND = "modal"
DEFAULT_MODAL_APP_NAME = "kaka-agent-sandboxes"
DEFAULT_MODAL_ROOT = "/workspace"
DEFAULT_MODAL_IMAGE = "python:3.13-slim"
DEFAULT_MODAL_SANDBOX_TIMEOUT_SECONDS = 3600
DEFAULT_MODAL_IDLE_TIMEOUT_SECONDS = 900
GIT_TIMEOUT_SECONDS = 10
T = TypeVar("T")


def _config() -> tuple[bool, str, str, str, str, str, int, int]:
    from agent.shared.config.service import get_config_service

    service = get_config_service()
    enabled = service.get_bool("workspace.modal.enabled", False)
    token_id = service.get_str("workspace.modal.token_id", "").strip()
    token_secret = service.get_str("workspace.modal.token_secret", "").strip()
    app_name = (
        service.get_str("workspace.modal.app_name", DEFAULT_MODAL_APP_NAME).strip()
        or DEFAULT_MODAL_APP_NAME
    )
    root = (
        service.get_str("workspace.modal.default_root", DEFAULT_MODAL_ROOT).strip()
        or DEFAULT_MODAL_ROOT
    )
    image = (
        service.get_str("workspace.modal.image", DEFAULT_MODAL_IMAGE).strip()
        or DEFAULT_MODAL_IMAGE
    )
    sandbox_timeout_seconds = max(
        60,
        service.get_int(
            "workspace.modal.sandbox_timeout_seconds",
            DEFAULT_MODAL_SANDBOX_TIMEOUT_SECONDS,
        ),
    )
    idle_timeout_seconds = max(
        0,
        service.get_int(
            "workspace.modal.idle_timeout_seconds",
            DEFAULT_MODAL_IDLE_TIMEOUT_SECONDS,
        ),
    )
    return (
        enabled,
        token_id,
        token_secret,
        app_name,
        root,
        image,
        sandbox_timeout_seconds,
        idle_timeout_seconds,
    )


def get_modal_module():
    try:
        import modal
    except ImportError as exc:
        raise RuntimeError(
            "The Modal Python SDK is not installed. Install project dependencies with uv."
        ) from exc
    return modal


async def get_modal_client():
    enabled, token_id, token_secret, *_ = _config()
    if not enabled:
        raise ValueError("Modal workspace backend is disabled.")
    modal = get_modal_module()
    if token_id or token_secret:
        if not token_id or not token_secret:
            raise ValueError(
                "Both Modal token ID and token secret must be configured."
            )
        return await modal.Client.from_credentials.aio(token_id, token_secret)
    return None


async def create_modal_workspace(*, label: str | None = None) -> WorkspaceRef:
    (
        _enabled,
        _token_id,
        _token_secret,
        app_name,
        root,
        image_ref,
        sandbox_timeout_seconds,
        idle_timeout_seconds,
    ) = _config()
    client = await get_modal_client()
    modal = get_modal_module()
    app = await modal.App.lookup.aio(app_name, create_if_missing=True, client=client)
    create_kwargs: dict[str, Any] = {
        "app": app,
        "client": client,
        "timeout": sandbox_timeout_seconds,
    }
    if idle_timeout_seconds > 0:
        create_kwargs["idle_timeout"] = idle_timeout_seconds
    if image_ref:
        create_kwargs["image"] = (
            modal.Image.from_registry(image_ref).apt_install("git")
        )
    sandbox = await modal.Sandbox.create.aio(**create_kwargs)
    sandbox_id = _sandbox_id(sandbox)
    if not sandbox_id:
        raise RuntimeError("Modal did not return a sandbox ID.")
    ref = WorkspaceRef(
        backend=MODAL_BACKEND,
        locator=sandbox_id,
        label=(label or "").strip() or f"modal:{sandbox_id}",
        metadata=modal_metadata(
            root=root, app_name=app_name, touch=True, status="started"
        ),
    )
    fs = getattr(sandbox, "filesystem", None)
    if fs is None:
        raise RuntimeError("Modal sandbox does not expose filesystem APIs.")
    backend = ModalWorkspaceBackend(ref, sandbox=sandbox, fs=fs, client=client)
    await backend.ensure_git()
    await backend.ensure_root()
    return ref


async def attach_modal_workspace(
    sandbox_id: str,
    *,
    label: str | None = None,
    root: str | None = None,
) -> WorkspaceRef:
    _enabled, _token_id, _token_secret, app_name, default_root, *_ = _config()
    normalized_sandbox_id = str(sandbox_id or "").strip()
    if not normalized_sandbox_id:
        raise ValueError("Modal sandbox ID is required.")
    selected_root = (root or "").strip() or default_root or DEFAULT_MODAL_ROOT
    ref = WorkspaceRef(
        backend=MODAL_BACKEND,
        locator=normalized_sandbox_id,
        label=(label or "").strip() or f"modal:{normalized_sandbox_id}",
        metadata=modal_metadata(
            root=selected_root, app_name=app_name, touch=True, status="started"
        ),
    )
    backend = await ModalWorkspaceBackend.create(ref)
    await backend.ensure_git()
    await backend.ensure_root()
    return ref


def modal_metadata(
    *,
    root: str | None = None,
    app_name: str | None = None,
    touch: bool = False,
    status: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if root is not None:
        metadata["root"] = resolve_modal_path("/", root)
    if app_name is not None:
        metadata["app_name"] = app_name
    if status is not None:
        metadata["status"] = status
    if touch:
        metadata["last_used_at"] = datetime.now(timezone.utc).isoformat()
    return metadata


def _sandbox_id(sandbox: Any) -> str:
    value = getattr(sandbox, "object_id", None)
    if value is None:
        value = getattr(sandbox, "id", None)
    return str(value or "").strip()


async def get_modal_sandbox(ref: WorkspaceRef, *, client: Any | None = None):
    if ref.backend != MODAL_BACKEND:
        raise ValueError(f"Unsupported workspace backend: {ref.backend}")
    client = client or await get_modal_client()
    modal = get_modal_module()
    return await modal.Sandbox.from_id.aio(ref.locator, client=client)


async def delete_modal_workspace(ref: WorkspaceRef) -> str:
    if ref.backend != MODAL_BACKEND:
        raise ValueError(f"Unsupported workspace backend: {ref.backend}")
    try:
        sandbox = await get_modal_sandbox(ref)
    except Exception as exc:
        if _is_modal_not_found_error(exc):
            return "terminated"
        raise
    terminator = getattr(sandbox, "terminate", None)
    if callable(terminator):
        aio_terminator = getattr(terminator, "aio", None)
        if callable(aio_terminator):
            await aio_terminator(wait=False)
        else:
            terminator(wait=False)
    detacher = getattr(sandbox, "detach", None)
    if callable(detacher):
        aio_detacher = getattr(detacher, "aio", None)
        if callable(aio_detacher):
            try:
                await aio_detacher()
            except Exception as exc:
                logger.debug("Failed to detach Modal sandbox %s: %s", ref.locator, exc)
        else:
            try:
                detacher()
            except Exception as exc:
                logger.debug("Failed to detach Modal sandbox %s: %s", ref.locator, exc)
    return "terminated"


def _is_modal_not_found_error(exc: Exception) -> bool:
    return _is_modal_unavailable_error(exc, strict_not_found=True)


def _is_modal_unavailable_error(
    exc: Exception,
    *,
    strict_not_found: bool = False,
) -> bool:
    if isinstance(exc, FileNotFoundError):
        return False
    class_name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    unavailable_markers = (
        "sandbox is unavailable",
        "already shut down",
        "idle timeout",
        "task has already finished",
    )
    if any(marker in message for marker in unavailable_markers):
        return True
    if "sandbox" in message and "not found" in message:
        return True
    if "not a valid sandbox id" in message:
        return True
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if str(status or "").strip() == "404" and (strict_not_found or "sandbox" in message):
        return True
    if strict_not_found and ("notfound" in class_name or "not_found" in class_name):
        return True
    return False


def _raise_if_modal_unavailable(
    ref: WorkspaceRef,
    exc: Exception,
    *,
    strict_not_found: bool = False,
) -> None:
    if not _is_modal_unavailable_error(exc, strict_not_found=strict_not_found):
        return
    raise WorkspaceUnavailableError(
        (
            f"Modal sandbox {ref.locator} is no longer available. "
            "Create or attach a new Modal workspace for this thread."
        ),
        backend=MODAL_BACKEND,
        locator=ref.locator,
    ) from exc


def resolve_modal_path(root: str, path: str | None = None) -> str:
    normalized_root = _normalize_posix_path(root)
    if not posixpath.isabs(normalized_root):
        normalized_root = _normalize_posix_path(f"/{normalized_root}")
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


def modal_relative_path(root: str, path: str) -> str:
    normalized_root = resolve_modal_path("/", root)
    target = resolve_modal_path(normalized_root, path)
    if target == normalized_root:
        return ""
    if normalized_root == "/":
        return target.lstrip("/")
    return target[len(normalized_root) + 1 :]


class ModalWorkspaceBackend(SandboxBackendBase):
    """Workspace backend backed by a Modal sandbox."""

    def __init__(
        self,
        ref: WorkspaceRef,
        *,
        sandbox: Any | None = None,
        fs: Any | None = None,
        client: Any | None = None,
    ) -> None:
        super().__init__(ref)
        self.sandbox = sandbox
        self.fs = fs
        self.client = client
        self.root = resolve_modal_path(
            "/",
            str(ref.metadata.get("root") or DEFAULT_MODAL_ROOT),
        )
        self.ref.metadata.update(modal_metadata(root=self.root, touch=True))

    @classmethod
    async def create(
        cls,
        ref: WorkspaceRef,
        *,
        client: Any | None = None,
    ) -> ModalWorkspaceBackend:
        """Async factory that resolves the sandbox from the workspace ref."""
        client = client or await get_modal_client()
        try:
            sandbox = await get_modal_sandbox(ref, client=client)
        except Exception as exc:
            _raise_if_modal_unavailable(ref, exc, strict_not_found=True)
            raise
        fs = getattr(sandbox, "filesystem", None)
        if fs is None:
            raise RuntimeError("Modal sandbox does not expose filesystem APIs.")
        return cls(ref, sandbox=sandbox, fs=fs, client=client)

    async def ensure_root(self) -> None:
        await self._make_directory(self.root)

    def ensure_active(self) -> None:
        """Ensure the sandbox is active (Modal uses touch for lifecycle)."""
        self.touch()

    async def ensure_git(self) -> None:
        """Install git in the sandbox if it is not already available."""
        check = await self._exec("which git", cwd="/", timeout=5)
        if check.exit_code == 0:
            return
        install = await self._exec(
            "apt-get update -qq && apt-get install -y -qq git",
            cwd="/",
            timeout=120,
        )
        if install.exit_code not in (0, None):
            logger.warning(
                "Failed to install git in Modal sandbox %s: %s",
                self.ref.locator,
                install.output.strip(),
            )

    async def clone_repository(
        self,
        *,
        owner: str,
        repo: str,
        default_branch: str = "main",
        token: str | None = None,
        depth: int= 1,
    ) -> str:
        if not owner or not repo:
            raise ValueError("Repository owner and name are required.")
        self.touch()
        await self.ensure_root()
        relative = repo
        destination = resolve_modal_path(self.root, relative)
        branch = (default_branch or "main").strip() or "main"
        depth_flag = f"--depth={int(depth)}" if depth and int(depth) > 0 else ""
        if token:
            url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
        else:
            url = f"https://github.com/{owner}/{repo}.git"
        parent = posixpath.dirname(destination)
        await self._make_directory(parent)
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

    def touch(self) -> None:
        self.ref.metadata.update(modal_metadata(root=self.root, touch=True))

    async def _run_remote(self, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except Exception as exc:
            _raise_if_modal_unavailable(self.ref, exc)
            raise

    async def _run_remote_aio(self, coro_fn: Callable[[], Any]) -> Any:
        try:
            return await coro_fn()
        except Exception as exc:
            _raise_if_modal_unavailable(self.ref, exc)
            raise

    async def list_dir(self, path: str = "") -> str:
        self.touch()
        target = resolve_modal_path(self.root, path or ".")
        command = f"ls -1 {shlex.quote(target)}"
        result = await self._exec(command, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Failed to list Modal directory.")
        entries = [line for line in result.output.splitlines() if line.strip()]
        truncated = len(entries) > MAX_LIST_FILES_ENTRIES
        entries = entries[:MAX_LIST_FILES_ENTRIES]
        if not entries:
            return "(Empty directory)"
        output = "\n".join(entries)
        if truncated:
            output += f"\n...[truncated at {MAX_LIST_FILES_ENTRIES} entries]"
        return output

    async def read_text(self, file_path: str) -> str:
        self.touch()
        return await self._run_remote_aio(
            lambda: self.fs.read_text.aio(resolve_modal_path(self.root, file_path))
        )

    async def write_text(self, file_path: str, content: str) -> str:
        self.touch()
        remote_path = resolve_modal_path(self.root, file_path)
        await self._run_remote_aio(lambda: self.fs.write_text.aio(content, remote_path))
        return f"[OK] Wrote file: {remote_path}"

    async def execute(
        self,
        command: str,
        *,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        self.touch()
        return await self._exec(
            command,
            cwd=self.root,
            timeout=timeout,
            max_output_chars=max_output_chars,
        )

    async def tree(self, path: str | None = None) -> dict[str, Any]:
        """Return directory listing for *path* (Modal override for async fs API)."""
        from agent.modules.workspaces.posix_utils import resolve_remote_path

        self.touch()
        target = resolve_remote_path(self.root, path or ".")
        info = await self._stat(target)
        if not file_info_is_dir(info):
            raise NotADirectoryError(f"Path is not a directory: {path or '.'}")
        entries: list[dict[str, Any]] = []
        truncated = False
        items = await self._run_remote_aio(lambda: self.fs.list_files.aio(target))
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
        return {
            "root": self.root,
            "path": target,
            "entries": entries,
            "truncated": truncated,
        }

    async def file(self, path: str) -> dict[str, Any]:
        """Return file content and metadata (Modal override for async fs API)."""
        import mimetypes
        from agent.modules.workspaces.posix_utils import resolve_remote_path

        self.touch()
        remote_path = resolve_remote_path(self.root, path)
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
        """Return git status for the workspace (Modal override for async API)."""
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
        for change in changes:
            additions, deletions = await self._change_line_stats(change, git_root=git_root)
            change["additions"] = additions
            change["deletions"] = deletions
        return {
            "root": self.root,
            "is_git_repo": True,
            "changes": changes,
            "message": "",
        }

    async def diff(self, path: str) -> dict[str, Any]:
        """Return git diff for a single file (Modal override for async API)."""
        from agent.modules.workspaces.posix_utils import (
            relative_remote_path,
            resolve_remote_path,
        )

        self.touch()
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
        """Rename a file or directory (Modal override for async API)."""
        from agent.modules.workspaces.posix_utils import (
            relative_remote_path,
            resolve_remote_path,
        )

        self.touch()
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
        """Delete a file or directory (Modal override for async API)."""
        from agent.modules.workspaces.posix_utils import (
            relative_remote_path,
            resolve_remote_path,
        )

        self.touch()
        target = resolve_remote_path(self.root, path)
        relative = relative_remote_path(self.root, target)
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

    async def _exec(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        try:
            try:
                process = await self.sandbox.exec.aio(
                    "bash",
                    "-lc",
                    command,
                    timeout=timeout,
                    workdir=cwd,
                    text=True,
                )
            except TypeError:
                process = await self.sandbox.exec.aio("bash", "-lc", command, timeout=timeout)
            output = await _read_stream_aio(getattr(process, "stdout", None))
            stderr = await _read_stream_aio(getattr(process, "stderr", None))
            wait_fn = getattr(process, "wait", None)
            if callable(wait_fn):
                aio_wait = getattr(wait_fn, "aio", None)
                if callable(aio_wait):
                    exit_code = await aio_wait()
                else:
                    exit_code = wait_fn()
            else:
                exit_code = getattr(process, "returncode", None)
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
        except Exception as exc:
            _raise_if_modal_unavailable(self.ref, exc)
            raise

    async def _make_directory(self, path: str) -> None:
        maker = getattr(self.fs, "make_directory", None)
        if not callable(maker):
            await self._exec(f"mkdir -p {shlex.quote(path)}", cwd="/")
            return
        aio_maker = getattr(maker, "aio", None)
        try:
            if callable(aio_maker):
                try:
                    await aio_maker(path, create_parents=True)
                except TypeError:
                    await aio_maker(path)
            else:
                try:
                    maker(path, create_parents=True)
                except TypeError:
                    maker(path)
        except Exception as exc:
            _raise_if_modal_unavailable(self.ref, exc)
            raise

    def _download_file(self, path: str) -> bytes | str:
        """Download file content from the Modal sandbox."""
        reader = getattr(self.fs, "read_bytes", None)
        if callable(reader):
            raw = reader(path)
        else:
            aio_read_text = getattr(self.fs.read_text, None)
            if callable(aio_read_text):
                raw = aio_read_text(path)
            else:
                raw = self.fs.read_text(path)
        if isinstance(raw, str):
            return raw.encode("utf-8")
        return bytes(raw or b"")

    def _upload_file(self, content: bytes, path: str) -> None:
        """Upload file content to the Modal sandbox."""
        writer = getattr(self.fs, "write_bytes", None)
        if callable(writer):
            writer(content, path)
        else:
            writer_text = getattr(self.fs, "write_text", None)
            if callable(writer_text):
                writer_text(content.decode("utf-8"), path)

    def _stat_file(self, path: str) -> Any:
        """Return a file-info object for *path*."""
        statter = getattr(self.fs, "stat", None)
        if callable(statter):
            return statter(path)
        getter = getattr(self.fs, "get_file_info", None)
        if callable(getter):
            return getter(path)
        aio_list = getattr(self.fs.list_files, None)
        if callable(aio_list):
            parent = posixpath.dirname(path) or "/"
            items = aio_list(parent)
            for item in items:
                if file_info_path(item, parent) == path:
                    return item
        raise FileNotFoundError(path)

    async def _stat(self, path: str) -> Any:
        statter = getattr(self.fs, "stat", None)
        if callable(statter):
            aio_statter = getattr(statter, "aio", None)
            if callable(aio_statter):
                return await aio_statter(path)
            return statter(path)
        getter = getattr(self.fs, "get_file_info", None)
        if callable(getter):
            aio_getter = getattr(getter, "aio", None)
            if callable(aio_getter):
                return await aio_getter(path)
            return getter(path)
        aio_list = getattr(self.fs.list_files, "aio", None)
        parent = posixpath.dirname(path) or "/"
        if callable(aio_list):
            items = await aio_list(parent)
        else:
            items = self.fs.list_files(parent)
        for item in items:
            if _file_info_path(item, parent) == path:
                return item
        raise FileNotFoundError(path)

    async def _read_bytes(self, path: str) -> bytes:
        reader = getattr(self.fs, "read_bytes", None)
        if callable(reader):
            aio_reader = getattr(reader, "aio", None)
            if callable(aio_reader):
                raw = await aio_reader(path)
            else:
                raw = reader(path)
        else:
            aio_read_text = getattr(self.fs.read_text, "aio", None)
            if callable(aio_read_text):
                raw = await aio_read_text(path)
            else:
                raw = self.fs.read_text(path)
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
                ["rev-parse", "--show-toplevel"], cwd=self.root
            )).strip()
        except WorkspaceUnavailableError:
            raise
        except Exception:
            return None
        if not output:
            return None
        return _normalize_posix_path(output.splitlines()[-1])

    async def _change_line_stats(
        self,
        change: dict[str, Any],
        *,
        git_root: str,
    ) -> tuple[int, int]:
        """Compute addition/deletion line counts for a single change."""
        from agent.modules.workspaces.git_utils import compute_change_line_stats_from_numstat

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
        """Build a unified diff for an untracked file."""
        from agent.modules.workspaces.git_utils import build_untracked_diff_content

        try:
            content = await self.read_text(path)
        except WorkspaceUnavailableError:
            raise
        except Exception:
            return ""
        return build_untracked_diff_content(content, relative_path)


def _normalize_posix_path(value: str) -> str:
    return normalize_posix_path(value)


async def clone_repository_in_modal_sandbox(
    ref: WorkspaceRef,
    *,
    owner: str,
    repo: str,
    default_branch: str = "main",
    token: str | None = None,
    depth: int = 1,
) -> str:
    if ref.backend != MODAL_BACKEND:
        raise ValueError(f"Unsupported workspace backend: {ref.backend}")
    backend = await ModalWorkspaceBackend.create(ref)
    return await backend.clone_repository(
        owner=owner,
        repo=repo,
        default_branch=default_branch,
        token=token,
        depth=depth,
    )


async def _read_stream_aio(stream: Any) -> str:
    if stream is None:
        return ""
    reader = getattr(stream, "read", None)
    if callable(reader):
        aio_reader = getattr(reader, "aio", None)
        if callable(aio_reader):
            value = await aio_reader()
        else:
            value = reader()
    elif isinstance(stream, (str, bytes, bytearray)):
        value = stream
    else:
        value = "".join(str(chunk) for chunk in stream)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value or "")


def _read_stream(stream: Any) -> str:
    if stream is None:
        return ""
    reader = getattr(stream, "read", None)
    if callable(reader):
        value = reader()
    elif isinstance(stream, (str, bytes, bytearray)):
        value = stream
    else:
        value = "".join(str(chunk) for chunk in stream)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value or "")


def _file_info_name(item: Any) -> str:
    return file_info_name(item)


def _file_info_type(item: Any) -> str:
    value = getattr(item, "type", None)
    name = getattr(value, "name", None)
    return str(name or value or "").strip().lower()


def _file_info_is_dir(item: Any) -> bool:
    return file_info_is_dir(item)


def _file_info_size(item: Any) -> int:
    return file_info_size(item)


def _file_info_modified_at(item: Any) -> float:
    return file_info_modified_at(item)


def _file_info_path(item: Any, parent: str) -> str:
    return file_info_path(item, parent)


def _git_status_args() -> list[str]:
    return git_status_args()


def _parse_git_status(
    output: str,
    *,
    workspace_root: str,
    git_root: str,
) -> list[dict[str, Any]]:
    return parse_git_status(output, workspace_root=workspace_root, git_root=git_root)


def _status_label(code: str) -> str:
    from agent.modules.workspaces.git_utils import status_label
    return status_label(code)


def _git_relative_path(git_root: str, target: str) -> str:
    return git_relative_path(git_root, target)


def _truncate_diff(diff: str) -> tuple[str, bool]:
    return truncate_diff(diff)


__all__ = [
    "DEFAULT_MODAL_APP_NAME",
    "DEFAULT_MODAL_IMAGE",
    "DEFAULT_MODAL_ROOT",
    "MODAL_BACKEND",
    "ModalWorkspaceBackend",
    "attach_modal_workspace",
    "clone_repository_in_modal_sandbox",
    "create_modal_workspace",
    "delete_modal_workspace",
    "get_modal_client",
    "get_modal_module",
    "get_modal_sandbox",
    "resolve_modal_path",
]
