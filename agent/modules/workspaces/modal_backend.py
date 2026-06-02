from __future__ import annotations

from collections.abc import Callable
import difflib
import logging
import mimetypes
import posixpath
import shlex
from datetime import datetime, timezone
from typing import Any, TypeVar

from agent.modules.workspaces.backends import CommandResult, WorkspaceUnavailableError
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
        metadata=modal_metadata(root=root, app_name=app_name, touch=True),
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
        metadata=modal_metadata(root=selected_root, app_name=app_name, touch=True),
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
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if root is not None:
        metadata["root"] = resolve_modal_path("/", root)
    if app_name is not None:
        metadata["app_name"] = app_name
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


class ModalWorkspaceBackend:
    """Workspace backend backed by a Modal sandbox."""

    def __init__(
        self,
        ref: WorkspaceRef,
        *,
        sandbox: Any | None = None,
        fs: Any | None = None,
        client: Any | None = None,
    ) -> None:
        if ref.backend != MODAL_BACKEND:
            raise ValueError(f"Unsupported workspace backend: {ref.backend}")
        self.ref = ref
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

    async def list_files(self, sub_dir: str = "") -> str:
        self.touch()
        target = resolve_modal_path(self.root, sub_dir or ".")
        command = (
            f"find {shlex.quote(target)} "
            "-type d \\( -name .git -o -name .venv -o -name node_modules "
            "-o -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \\) "
            "-prune -o -type f -print"
        )
        result = await self._exec(command, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Failed to list Modal files.")
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
        self.touch()
        target = resolve_modal_path(self.root, path or ".")
        entries: list[dict[str, Any]] = []
        truncated = False
        items = await self._run_remote_aio(lambda: self.fs.list_files.aio(target))
        for item in items:
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
        self.touch()
        remote_path = resolve_modal_path(self.root, path)
        info = await self._stat(remote_path)
        if _file_info_is_dir(info):
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
                "size": _file_info_size(info),
                "content": "",
                "truncated": truncated,
                "binary": True,
                "message": "Binary files cannot be previewed.",
            }
        return {
            "root": self.root,
            "path": remote_path,
            "mime_type": mime_type,
            "size": _file_info_size(info),
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
        output = await self._run_git(_git_status_args(), cwd=self.root)
        changes = _parse_git_status(output, workspace_root=self.root, git_root=git_root)
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
        self.touch()
        remote_path = resolve_modal_path(self.root, path)
        relative_path = modal_relative_path(self.root, remote_path)
        if not relative_path:
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
            for change in _parse_git_status(
                await self._run_git(_git_status_args(), cwd=self.root),
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
            staged = await self._run_git(
                ["diff", "--cached", "--no-ext-diff", "--", git_path],
                cwd=git_root,
            )
            unstaged = await self._run_git(
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
        self.touch()
        source = resolve_modal_path(self.root, path)
        relative_path = modal_relative_path(self.root, source)
        if not relative_path:
            raise ValueError("Cannot rename workspace root.")
        clean_name = str(new_name or "").strip()
        if not clean_name or clean_name in {".", ".."}:
            raise ValueError("New name is invalid.")
        if "/" in clean_name or "\\" in clean_name:
            raise ValueError("New name must not contain path separators.")
        destination = resolve_modal_path(
            self.root,
            posixpath.join(posixpath.dirname(relative_path), clean_name),
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
        target = resolve_modal_path(self.root, path)
        relative_path = modal_relative_path(self.root, target)
        if not relative_path:
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
        path = str(change.get("path") or "")
        if not path:
            return 0, 0
        if change.get("status") == "untracked":
            return await self._text_line_count(path), 0
        git_path = _git_relative_path(git_root, path)
        additions = 0
        deletions = 0
        for args in (
            ["diff", "--numstat", "--cached", "--no-ext-diff", "--", git_path],
            ["diff", "--numstat", "--no-ext-diff", "--", git_path],
        ):
            try:
                output = await self._run_git(args, cwd=git_root)
            except WorkspaceUnavailableError:
                raise
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
        try:
            content = await self.read_text(path)
        except WorkspaceUnavailableError:
            raise
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
    raw_name = getattr(item, "name", "")
    if not raw_name:
        path_value = str(getattr(item, "path", "") or "").strip()
        raw_name = path_value.rsplit("/", 1)[-1]
    return str(raw_name or "").strip().rstrip("/")


def _file_info_type(item: Any) -> str:
    value = getattr(item, "type", None)
    name = getattr(value, "name", None)
    return str(name or value or "").strip().lower()


def _file_info_is_dir(item: Any) -> bool:
    value = getattr(item, "is_dir", None)
    if value is None:
        value = getattr(item, "is_directory", None)
    if value is not None:
        return bool(value)
    info_type = _file_info_type(item)
    return info_type in {"dir", "directory"}


def _file_info_size(item: Any) -> int:
    try:
        return int(getattr(item, "size", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _file_info_modified_at(item: Any) -> float:
    value = getattr(item, "modified_time", None)
    if value is None:
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
    if root == "/":
        return target_path.lstrip("/")
    if not target_path.startswith(f"{root}/"):
        raise ValueError("Path escapes Git repository.")
    return target_path[len(root) + 1 :]


def _truncate_diff(diff: str) -> tuple[str, bool]:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False
    return diff[:MAX_DIFF_CHARS] + "\n...[truncated]", True


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
