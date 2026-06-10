from __future__ import annotations

import json
import logging
import os
import posixpath
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.modules.workspaces.backends import CommandResult, WorkspaceUnavailableError
from agent.modules.workspaces.constants import (
    GIT_TIMEOUT_SECONDS,
    MAX_TREE_ENTRIES,
)
from agent.modules.workspaces.git_utils import git_relative_path
from agent.modules.workspaces.posix_utils import normalize_posix_path
from agent.modules.workspaces.refs import WorkspaceRef
from agent.modules.workspaces.sandbox_backend import SandboxBackendBase
from agent.modules.workspaces.search_utils import (
    build_sandbox_glob_command,
    build_sandbox_grep_command,
    render_sandbox_glob_output,
    render_sandbox_grep_output,
)
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

logger = logging.getLogger(__name__)

OPEN_SHELL_BACKEND = "openshell"
DEFAULT_OPEN_SHELL_ROOT = "/sandbox"
DEFAULT_OPEN_SHELL_IMAGE = "base"
DEFAULT_OPEN_SHELL_TIMEOUT_SECONDS = 120
DEFAULT_OPEN_SHELL_CREATE_TIMEOUT_SECONDS = 300
DEFAULT_OPEN_SHELL_DELETE_TIMEOUT_SECONDS = 120
DEFAULT_OPEN_SHELL_LIST_TIMEOUT_SECONDS = 30

OPEN_SHELL_STATUS_STARTED = "started"
OPEN_SHELL_STATUS_STARTING = "starting"
OPEN_SHELL_STATUS_STOPPED = "stopped"
OPEN_SHELL_STATUS_ERROR = "error"
OPEN_SHELL_STATUS_UNKNOWN = "unknown"


def _config() -> tuple[bool, str, str, str, int, str, int, int, int, int]:
    from agent.shared.config.service import get_config_service

    service = get_config_service()
    return (
        service.get_bool("workspace.openshell.enabled", False),
        service.get_str("workspace.openshell.cli_path", "").strip() or "openshell",
        service.get_str("workspace.openshell.default_root", DEFAULT_OPEN_SHELL_ROOT).strip()
        or DEFAULT_OPEN_SHELL_ROOT,
        service.get_str("workspace.openshell.image", DEFAULT_OPEN_SHELL_IMAGE).strip()
        or DEFAULT_OPEN_SHELL_IMAGE,
        max(0, service.get_int("workspace.openshell.cpu", 0)),
        service.get_str("workspace.openshell.memory", "").strip(),
        max(0, service.get_int("workspace.openshell.create_timeout_seconds", DEFAULT_OPEN_SHELL_CREATE_TIMEOUT_SECONDS)),
        max(1, service.get_int("workspace.openshell.exec_timeout_seconds", DEFAULT_OPEN_SHELL_TIMEOUT_SECONDS)),
        max(1, service.get_int("workspace.openshell.delete_timeout_seconds", DEFAULT_OPEN_SHELL_DELETE_TIMEOUT_SECONDS)),
        max(1, service.get_int("workspace.openshell.list_timeout_seconds", DEFAULT_OPEN_SHELL_LIST_TIMEOUT_SECONDS)),
    )


def _openshell_metadata(
    *,
    root: str,
    status: str = OPEN_SHELL_STATUS_UNKNOWN,
    touch: bool = False,
    started: bool = False,
    stopped: bool = False,
    archived: bool = False,
    destroyed: bool = False,
    labels: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    metadata: dict[str, Any] = {
        "root": root,
        "status": status,
        "provider": OPEN_SHELL_BACKEND,
    }
    if labels:
        metadata["labels"] = dict(labels)
    if touch:
        metadata["last_used_at"] = now
    if started:
        metadata["last_started_at"] = now
    if stopped:
        metadata["last_stopped_at"] = now
    if archived:
        metadata["last_archived_at"] = now
    if destroyed:
        metadata["last_stopped_at"] = now
    return metadata


def _cli_path() -> str:
    return _config()[1]


def _run_openshell(
    args: list[str],
    *,
    timeout: int = DEFAULT_OPEN_SHELL_TIMEOUT_SECONDS,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    cli = _cli_path()
    completed = subprocess.run(
        [cli, *args],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or f"{cli} {' '.join(args)} exited with {completed.returncode}.")
    return completed


def _parse_json_payload(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _json_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("sandboxes", "items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _first_string(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _parse_labels(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, list):
        labels: dict[str, str] = {}
        for item in value:
            if isinstance(item, dict):
                key = _first_string(item, ("key", "name", "label"))
                val = _first_string(item, ("value", "content"))
                if key:
                    labels[key] = val
            elif isinstance(item, str) and "=" in item:
                key, val = item.split("=", 1)
                labels[key.strip()] = val.strip()
        return labels
    return {}


def _normalize_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return OPEN_SHELL_STATUS_UNKNOWN
    if raw.startswith("sandboxstate."):
        raw = raw.rsplit(".", 1)[-1]
    if raw in {"ready", "running", "active", "started", "start", "running/ready"}:
        return OPEN_SHELL_STATUS_STARTED
    if raw in {"provisioning", "starting", "pending", "created", "created/starting"}:
        return OPEN_SHELL_STATUS_STARTING
    if raw in {"stopping", "stopped", "finished", "terminated", "deleting", "deleted"}:
        return OPEN_SHELL_STATUS_STOPPED
    if raw in {"error", "failed", "failed/error", "provisioning_error"}:
        return OPEN_SHELL_STATUS_ERROR
    return raw


def _normalize_sandbox_record(record: dict[str, Any]) -> dict[str, Any] | None:
    sandbox_id = _first_string(record, ("name", "sandbox_name", "id", "sandbox_id", "object_id"))
    if not sandbox_id:
        return None
    labels = _parse_labels(record.get("labels") or record.get("metadata", {}).get("labels"))
    status = _normalize_status(_first_string(record, ("phase", "status", "state")) or record.get("status"))
    created_at = _first_string(record, ("created_at", "createdAt", "created"))
    updated_at = _first_string(record, ("updated_at", "updatedAt", "updated"))
    return {
        "sandbox_id": sandbox_id,
        "backend": OPEN_SHELL_BACKEND,
        "label": labels.get("name") or labels.get("workspace") or sandbox_id,
        "root": labels.get("root") or DEFAULT_OPEN_SHELL_ROOT,
        "status": status,
        "thread_id": None,
        "repository_full_name": labels.get("repository") or labels.get("repository_full_name") or None,
        "last_used_at": None,
        "last_started_at": created_at or None,
        "last_stopped_at": None,
        "last_archived_at": None,
        "created_at": created_at or None,
        "updated_at": updated_at or None,
        "on_cloud": True,
        "is_orphan": True,
        "metadata": {
            "root": labels.get("root") or DEFAULT_OPEN_SHELL_ROOT,
            "status": status,
            "labels": labels,
            "created_at": created_at or None,
            "updated_at": updated_at or None,
        },
    }


def list_openshell_cloud_sandboxes() -> list[dict[str, Any]]:
    enabled, *_ = _config()
    if not enabled:
        raise ValueError("OpenShell workspace backend is disabled.")
    try:
        completed = _run_openshell(
            ["sandbox", "list", "-o", "json"],
            timeout=_config()[9],
            check=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenShell sandbox list failed: %s", exc)
        return []
    payload = _parse_json_payload(completed.stdout)
    records = [_normalize_sandbox_record(item) for item in _json_list(payload)]
    return [record for record in records if record is not None]


def _find_sandbox_record(locator: str) -> dict[str, Any] | None:
    records = list_openshell_cloud_sandboxes()
    normalized = locator.strip()
    for record in records:
        if record["sandbox_id"] == normalized:
            return record
        labels = record.get("metadata", {}).get("labels", {})
        if isinstance(labels, dict) and labels.get("name") == normalized:
            return record
    return None


def _new_sandbox_name(label: str | None) -> str:
    base = "k41"
    if label:
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in label)
        cleaned = cleaned.strip("-").lower()
        if cleaned:
            base = f"k41-{cleaned[:32]}"
    return f"{base}-{os.urandom(3).hex()}"


def _create_args(image: str, cpu: int, memory: str, name: str, label: str | None) -> list[str]:
    args = [
        "sandbox",
        "create",
        "--from",
        image,
        "--keep",
        "--no-auto-providers",
        "--name",
        name,
        "--label",
        "k41-agent=true",
    ]
    if label:
        args.extend(["--label", f"workspace={label[:80]}"])
    if cpu > 0:
        args.extend(["--cpu", str(cpu)])
    if memory:
        args.extend(["--memory", memory])
    args.append("--")
    return args


def create_openshell_workspace(*, label: str | None = None) -> WorkspaceRef:
    enabled, _cli, root, image, cpu, memory, create_timeout, *_ = _config()
    del _cli
    if not enabled:
        raise ValueError("OpenShell workspace backend is disabled.")
    normalized_root = normalize_posix_path(root or DEFAULT_OPEN_SHELL_ROOT)
    name = _new_sandbox_name(label)
    _run_openshell(
        _create_args(image, cpu, memory, name, label),
        timeout=create_timeout,
        check=True,
    )
    record = _find_sandbox_record(name)
    status = OPEN_SHELL_STATUS_STARTED
    if record:
        status = str(record.get("status") or OPEN_SHELL_STATUS_STARTED)
    metadata = _openshell_metadata(
        root=normalized_root,
        status=status,
        touch=True,
        started=True,
        labels={"name": name, "workspace": label or name, "root": normalized_root},
    )
    return WorkspaceRef(
        backend=OPEN_SHELL_BACKEND,
        locator=name,
        label=(label or "").strip() or f"{OPEN_SHELL_BACKEND}:{name}",
        metadata=metadata,
    )


def attach_openshell_workspace(
    sandbox_id: str,
    *,
    label: str | None = None,
    root: str | None = None,
) -> WorkspaceRef:
    enabled, *_ = _config()
    if not enabled:
        raise ValueError("OpenShell workspace backend is disabled.")
    normalized_id = str(sandbox_id or "").strip()
    if not normalized_id:
        raise ValueError("OpenShell sandbox ID is required.")
    record = _find_sandbox_record(normalized_id)
    if record is None:
        raise WorkspaceUnavailableError(
            f"OpenShell sandbox {normalized_id!r} is not available.",
            backend=OPEN_SHELL_BACKEND,
            locator=normalized_id,
        )
    labels = record.get("metadata", {}).get("labels", {})
    if not isinstance(labels, dict):
        labels = {}
    normalized_root = normalize_posix_path(
        (root or str(labels.get("root") or "")).strip()
        or DEFAULT_OPEN_SHELL_ROOT,
    )
    status = str(record.get("status") or OPEN_SHELL_STATUS_UNKNOWN)
    metadata = _openshell_metadata(
        root=normalized_root,
        status=status,
        touch=True,
        labels={**labels, "root": normalized_root},
    )
    return WorkspaceRef(
        backend=OPEN_SHELL_BACKEND,
        locator=normalized_id,
        label=(label or "").strip() or f"{OPEN_SHELL_BACKEND}:{normalized_id}",
        metadata=metadata,
    )


def delete_openshell_workspace(ref: WorkspaceRef) -> str:
    enabled, *_ = _config()
    if not enabled:
        raise ValueError("OpenShell workspace backend is disabled.")
    if ref.backend != OPEN_SHELL_BACKEND:
        raise ValueError(f"Expected OpenShell workspace, got {ref.backend!r}.")
    timeout = _config()[8]
    try:
        _run_openshell(["sandbox", "delete", ref.locator], timeout=timeout, check=True)
    except RuntimeError as exc:
        if "not found" in str(exc).lower() or "no sandbox" in str(exc).lower():
            ref.metadata.update(_openshell_metadata(root=ref.metadata.get("root", DEFAULT_OPEN_SHELL_ROOT), status=OPEN_SHELL_STATUS_STOPPED, destroyed=True))
            return OPEN_SHELL_STATUS_STOPPED
        raise
    ref.metadata.update(
        _openshell_metadata(
            root=ref.metadata.get("root", DEFAULT_OPEN_SHELL_ROOT),
            status=OPEN_SHELL_STATUS_STOPPED,
            destroyed=True,
        )
    )
    return OPEN_SHELL_STATUS_STOPPED


class OpenShellWorkspaceBackend(SandboxBackendBase):
    def __init__(self, ref: WorkspaceRef) -> None:
        super().__init__(ref)
        enabled, _cli, root, *_ = _config()
        if not enabled:
            raise ValueError("OpenShell workspace backend is disabled.")
        self.root = normalize_posix_path(
            str(ref.metadata.get("root") or root).strip() or DEFAULT_OPEN_SHELL_ROOT,
        )
        status = str(ref.metadata.get("status") or OPEN_SHELL_STATUS_UNKNOWN)
        self.ref.metadata.update(
            _openshell_metadata(root=self.root, status=status, touch=True)
        )

    def ensure_active(self) -> None:
        record = _find_sandbox_record(self.ref.locator)
        if record is None:
            raise WorkspaceUnavailableError(
                f"OpenShell sandbox {self.ref.locator!r} is no longer available.",
                backend=OPEN_SHELL_BACKEND,
                locator=self.ref.locator,
            )
        status = str(record.get("status") or OPEN_SHELL_STATUS_UNKNOWN)
        self.ref.metadata.update(
            _openshell_metadata(root=self.root, status=status, touch=True)
        )
        if status in {OPEN_SHELL_STATUS_ERROR, OPEN_SHELL_STATUS_STOPPED}:
            raise WorkspaceUnavailableError(
                f"OpenShell sandbox {self.ref.locator!r} is {status}.",
                backend=OPEN_SHELL_BACKEND,
                locator=self.ref.locator,
            )

    def ensure_root(self) -> None:
        self.ensure_active()
        self._exec(f"mkdir -p {shlex.quote(self.root)}", cwd="/")

    def _exec(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        self.ensure_active()
        exec_timeout = _config()[7]
        timeout_value = max(0, int(timeout or exec_timeout))
        args = [
            "sandbox",
            "exec",
            "-n",
            self.ref.locator,
            "--timeout",
            str(timeout_value),
            "--no-tty",
        ]
        if cwd:
            args.extend(["--workdir", cwd])
        args.extend(["--", "/bin/bash", "-lc", command])
        completed = _run_openshell(args, timeout=max(exec_timeout + 10, timeout_value + 10), check=False)
        output = (completed.stdout or "") + (completed.stderr or "")
        if max_output_chars is not None and len(output) > max_output_chars:
            return CommandResult(
                output=output[:max_output_chars],
                exit_code=completed.returncode,
                truncated=True,
            )
        return CommandResult(output=output, exit_code=completed.returncode)

    def _download_file(self, path: str) -> bytes | str:
        with tempfile.TemporaryDirectory(prefix="openshell-download-") as tmpdir:
            destination = Path(tmpdir)
            completed = _run_openshell(
                ["sandbox", "download", self.ref.locator, path, str(destination)],
                timeout=_config()[7],
                check=False,
            )
            if completed.returncode not in (0, None):
                detail = (completed.stderr or completed.stdout or "").strip()
                raise FileNotFoundError(f"Failed to download {path}: {detail}")
            basename = posixpath.basename(path.rstrip("/"))
            target = destination / basename
            if not target.exists():
                children = list(destination.glob("*"))
                if len(children) == 1:
                    target = children[0]
            if not target.exists():
                raise FileNotFoundError(path)
            return target.read_bytes()

    def _upload_file(self, content: bytes, path: str) -> None:
        parent = path.rsplit("/", 1)[0] or "/"
        self._make_directory(parent)
        basename = posixpath.basename(path.rstrip("/")) or "file"
        with tempfile.TemporaryDirectory(prefix="openshell-upload-") as tmpdir:
            source = Path(tmpdir) / basename
            source.write_bytes(content)
            completed = _run_openshell(
                ["sandbox", "upload", self.ref.locator, str(source), path],
                timeout=_config()[7],
                check=False,
            )
            if completed.returncode not in (0, None):
                detail = (completed.stderr or completed.stdout or "").strip()
                raise RuntimeError(detail or f"Failed to upload {path}.")

    def _make_directory(self, path: str) -> None:
        result = self._exec(f"mkdir -p {shlex.quote(path)}", cwd="/")
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or f"Failed to create directory {path}.")

    def _stat_file(self, path: str) -> Any:
        quoted = shlex.quote(path)
        result = self._exec(
            f"if [ -d {quoted} ]; then printf 'directory\\n'; elif [ -f {quoted} ]; then stat -c '%n|%s|%Y' {quoted}; else exit 2; fi",
            cwd="/",
        )
        if result.exit_code == 2:
            raise FileNotFoundError(path)
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or f"Failed to stat {path}.")
        lines = [line for line in result.output.splitlines() if line]
        if not lines:
            raise FileNotFoundError(path)
        if lines[-1] == "directory":
            return SimpleNamespace(
                name=posixpath.basename(path.rstrip("/")) or path,
                path=path,
                is_dir=True,
                size=0,
                mod_time=None,
            )
        name, size, modified = lines[-1].split("|", 2)
        return SimpleNamespace(
            name=name,
            path=path,
            is_dir=False,
            size=int(size),
            mod_time=datetime.fromtimestamp(float(modified), timezone.utc).isoformat(),
        )

    def _list_remote_files(self, path: str) -> list[Any]:
        quoted = shlex.quote(path)
        result = self._exec(
            f"find {quoted} -mindepth 1 -maxdepth 1 -printf '%y\\t%f\\t%p\\t%s\\t%T@\\n'",
            cwd="/",
        )
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or f"Failed to list {path}.")
        entries: list[Any] = []
        for line in result.output.splitlines():
            parts = line.split("\t", 4)
            if len(parts) != 5:
                continue
            kind, name, full_path, size, modified = parts
            entries.append(
                SimpleNamespace(
                    name=name,
                    path=full_path,
                    is_dir=kind == "d",
                    size=int(size or 0),
                    mod_time=datetime.fromtimestamp(float(modified), timezone.utc).isoformat(),
                )
            )
        return entries

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
        self.ensure_active()
        self.ensure_root()
        relative = repo
        destination = resolve_openshell_path(self.root, relative)
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
            raise RuntimeError(f"git clone failed for {owner}/{repo}: {result.output.strip()}")
        return relative

    async def list_dir(self, path: str = "") -> str:
        self.ensure_active()
        target = resolve_openshell_path(self.root, path or ".")
        cached, value = self._get_metadata_cache("list_dir", target)
        if cached:
            return value
        command = f"ls -1 {shlex.quote(target)}"
        result = await self._exec_async(command, timeout=GIT_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "Failed to list OpenShell directory.")
        entries = [line for line in result.output.splitlines() if line.strip()]
        truncated = len(entries) > MAX_TREE_ENTRIES
        entries = entries[:MAX_TREE_ENTRIES]
        if not entries:
            return "(Empty directory)"
        output = "\n".join(entries)
        if truncated:
            output += f"\n...[truncated at {MAX_TREE_ENTRIES} entries]"
        self._set_metadata_cache("list_dir", output, target)
        return output

    async def glob(
        self,
        pattern: str,
        *,
        path: str = "",
        include_dirs: bool = False,
    ) -> str:
        self.ensure_active()
        if not pattern:
            raise ValueError("Glob pattern must not be empty.")
        target = resolve_openshell_path(self.root, path or ".")
        command = build_sandbox_glob_command(
            root=self.root,
            target=target,
            pattern=pattern,
            include_dirs=include_dirs,
        )
        result = await self._exec_async(command, timeout=GIT_TIMEOUT_SECONDS)
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
        self.ensure_active()
        if not pattern:
            raise ValueError("Grep pattern must not be empty.")
        target = resolve_openshell_path(self.root, path or ".")
        command = build_sandbox_grep_command(
            root=self.root,
            target=target,
            relative_path=openshell_relative_path(self.root, target),
            pattern=pattern,
            include=include,
            case_insensitive=case_insensitive,
            max_results=max_results,
        )
        result = await self._exec_async(command, timeout=GIT_TIMEOUT_SECONDS)
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
        self.ensure_active()
        self._invalidate_workspace_caches()
        return await self._exec_async(
            command,
            cwd=self.root,
            timeout=timeout,
            max_output_chars=max_output_chars,
        )


def resolve_openshell_path(root: str, path: str) -> str:
    raw = str(path or ".").strip().replace("\\", "/")
    if not raw:
        raw = "."
    root_path = normalize_posix_path(root or DEFAULT_OPEN_SHELL_ROOT)
    if raw.startswith("/"):
        candidate = normalize_posix_path(raw)
    else:
        candidate = normalize_posix_path(posixpath.join(root_path, raw))
    if candidate != root_path and root_path != "/" and not candidate.startswith(root_path.rstrip("/") + "/"):
        raise ValueError("Path escapes workspace.")
    return candidate


def openshell_relative_path(root: str, path: str) -> str:
    return git_relative_path(normalize_posix_path(root), normalize_posix_path(path))


def create_open_shell_backend(ref: WorkspaceRef, *, thread_id: str | None = None):
    del thread_id
    return OpenShellWorkspaceBackend(ref)


__all__ = [
    "DEFAULT_OPEN_SHELL_IMAGE",
    "DEFAULT_OPEN_SHELL_ROOT",
    "OPEN_SHELL_BACKEND",
    "OpenShellWorkspaceBackend",
    "attach_openshell_workspace",
    "create_open_shell_backend",
    "create_openshell_workspace",
    "delete_openshell_workspace",
    "list_openshell_cloud_sandboxes",
    "openshell_relative_path",
    "resolve_openshell_path",
]
