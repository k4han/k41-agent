"""Download and install ACP agent binaries on explicit request.

Each version gets its own directory and a pointer file switches between them,
because a running executable cannot be overwritten on Windows. The sha256 comes
from the same registry as the URL, so it proves integrity rather than origin. The
fixed host allowlist is what ties a download to its vendor.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

from agent.modules.acp.agents import AcpAgentDefinition, get_agent_definition
from agent.modules.acp.errors import AcpError, AcpNotInstalled
from agent.modules.acp.process import AgentCommand
from agent.modules.acp.registry import AgentRelease, fetch_release
from agent.modules.acp.service import acp_data_root, not_installed_notice
from agent.modules.tools.coding.storage import atomic_json

logger = logging.getLogger(__name__)

MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 16 * 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 50_000
# Reported extraction ratios reach about 5.6 times the archive size.
DISK_SPACE_FACTOR = 7
API_KEY_FILE = "api_key"
POINTER_FILE = "current.json"
_CHUNK_BYTES = 1024 * 1024


class InstallBusyError(AcpError):
    """An install for this agent is already running."""


def agent_root(agent_id: str, data_root: Path | None = None) -> Path:
    return (data_root or acp_data_root()) / agent_id


def agent_home(agent_id: str, data_root: Path | None = None) -> Path:
    return agent_root(agent_id, data_root) / "home"


def read_pointer(agent_id: str, data_root: Path | None = None) -> dict[str, Any] | None:
    try:
        pointer = json.loads((agent_root(agent_id, data_root) / POINTER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return pointer if isinstance(pointer, dict) else None


def installed_binary(agent_id: str, data_root: Path | None = None) -> tuple[dict[str, Any], Path] | None:
    pointer = read_pointer(agent_id, data_root)
    if pointer is None or not pointer.get("version") or not pointer.get("command"):
        return None
    binary = agent_root(agent_id, data_root) / "versions" / str(pointer["version"]) / str(pointer["command"])
    return (pointer, binary) if binary.is_file() else None


def is_installed(agent_id: str, data_root: Path | None = None) -> bool:
    return installed_binary(agent_id, data_root) is not None


def read_api_key(agent_id: str, data_root: Path | None = None) -> str:
    try:
        return (agent_root(agent_id, data_root) / API_KEY_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_api_key(agent_id: str, value: str, data_root: Path | None = None) -> None:
    root = agent_root(agent_id, data_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / API_KEY_FILE
    temporary = root / f".{API_KEY_FILE}.{uuid.uuid4().hex}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
    os.replace(temporary, path)


def delete_api_key(agent_id: str, data_root: Path | None = None) -> None:
    (agent_root(agent_id, data_root) / API_KEY_FILE).unlink(missing_ok=True)


def resolve_installed_command(agent_id: str, data_root: Path | None = None) -> AgentCommand:
    definition = get_agent_definition(agent_id)
    installed = installed_binary(agent_id, data_root)
    if definition is None or installed is None:
        raise AcpNotInstalled(not_installed_notice(agent_id))
    pointer, binary = installed
    root = agent_root(agent_id, data_root)
    home = agent_home(agent_id, data_root)
    home.mkdir(parents=True, exist_ok=True)
    env = {definition.home_env: str(home)}
    api_key = read_api_key(agent_id, data_root)
    if api_key:
        for _method, variable in definition.api_key_env_by_method:
            env[variable] = api_key
    return AgentCommand(argv=[str(binary), *[str(item) for item in pointer.get("args") or []]], cwd=root / "work", env=env)


def _stream_download(url: str) -> Iterator[tuple[bytes, int | None]]:
    """Yield (chunk, total size). Redirects are refused: they would bypass the host check."""
    timeout = httpx.Timeout(connect=30, read=120, write=30, pool=30)
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=False) as response:
        if response.status_code != 200:
            raise AcpError(f"The download failed with HTTP {response.status_code}.")
        length = response.headers.get("content-length")
        total = int(length) if length and length.isdigit() else None
        for chunk in response.iter_bytes(_CHUNK_BYTES):
            yield chunk, total


def _extract(archive_path: Path, destination: Path) -> None:
    """Extract with the same member rules as skill packages, reading from disk."""
    from agent.modules.skills import safe_package_path, validate_package_member

    root = destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    total = 0
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_FILES:
            raise AcpError("The archive holds too many files.")
        for member in members:
            if member.flag_bits & 1:
                raise AcpError("The archive is encrypted.")
            name = validate_package_member(member.filename)
            folded = name.rstrip("/").casefold()
            if folded in seen:
                raise AcpError("The archive has conflicting file names.")
            seen.add(folded)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                raise AcpError("The archive holds links or special files.")
            total += member.file_size
            if total > MAX_EXTRACTED_BYTES:
                raise AcpError("The archive is too large when extracted.")
            target = safe_package_path(root, name)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            remaining = member.file_size
            with archive.open(member) as source, target.open("xb") as output:
                while block := source.read(min(_CHUNK_BYTES, remaining + 1)):
                    remaining -= len(block)
                    if remaining < 0:
                        raise AcpError("An archive member exceeds its declared size.")
                    output.write(block)
            if mode & 0o777:
                os.chmod(target, mode & 0o777)


class AcpInstaller:
    """Runs at most one background install per agent and reports its progress."""

    def __init__(
        self,
        *,
        data_root: Path | None = None,
        release_fetcher: Callable[[AcpAgentDefinition], AgentRelease] = fetch_release,
        downloader: Callable[[str], Iterator[tuple[bytes, int | None]]] = _stream_download,
        on_installed: Callable[[str], None] | None = None,
    ) -> None:
        self.data_root = data_root
        self._fetch_release = release_fetcher
        self._download = downloader
        self.on_installed = on_installed
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def status(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(agent_id)
            return dict(job) if job else None

    def is_running(self, agent_id: str) -> bool:
        job = self.status(agent_id)
        return job is not None and job["state"] == "running"

    def start(self, agent_id: str) -> dict[str, Any]:
        definition = get_agent_definition(agent_id)
        if definition is None:
            raise AcpError(f"Unknown ACP agent: {agent_id}.")
        with self._lock:
            current = self._jobs.get(agent_id)
            if current and current["state"] == "running":
                raise InstallBusyError(f"{definition.display_name} is already being installed.")
            job = {
                "state": "running", "phase": "resolving", "version": None, "downloaded_bytes": 0,
                "total_bytes": None, "error": None, "started_at": time.time(), "finished_at": None,
            }
            self._jobs[agent_id] = job
        threading.Thread(target=self._run, args=(definition,), name=f"acp-install-{agent_id}", daemon=True).start()
        return dict(job)

    def _update(self, agent_id: str, **values: Any) -> None:
        with self._lock:
            self._jobs[agent_id].update(values)

    def _run(self, definition: AcpAgentDefinition) -> None:
        agent_id = definition.id
        workspace: Path | None = None
        try:
            release = self._fetch_release(definition)
            self._update(agent_id, version=release.version)
            root = agent_root(agent_id, self.data_root)
            root.mkdir(parents=True, exist_ok=True)
            installed = installed_binary(agent_id, self.data_root)
            if installed is not None and installed[0].get("version") == release.version:
                self._update(agent_id, state="succeeded", phase="up_to_date", finished_at=time.time())
                return
            workspace = root / f".install-{uuid.uuid4().hex}"
            workspace.mkdir()
            archive_path = workspace / "archive.zip"
            self._update(agent_id, phase="downloading")
            self._fetch_archive(agent_id, release, archive_path, root)
            self._update(agent_id, phase="extracting")
            extracted = workspace / "files"
            _extract(archive_path, extracted)
            self._activate(agent_id, release, extracted, root)
            self._update(agent_id, state="succeeded", phase="installed", finished_at=time.time())
            if self.on_installed:
                self.on_installed(agent_id)
            self._remove_old_versions(root, release.version)
        except Exception as exc:
            logger.info("ACP install of %s failed: %s", agent_id, type(exc).__name__)
            message = str(exc) if isinstance(exc, (AcpError, ValueError, zipfile.BadZipFile, OSError)) else type(exc).__name__
            self._update(agent_id, state="failed", phase="failed", error=message or type(exc).__name__, finished_at=time.time())
        finally:
            if workspace is not None:
                shutil.rmtree(workspace, ignore_errors=True)

    def _fetch_archive(self, agent_id: str, release: AgentRelease, archive_path: Path, root: Path) -> None:
        digest = hashlib.sha256()
        downloaded = 0
        checked_space = False
        with archive_path.open("xb") as output:
            for chunk, total in self._download(release.archive_url):
                if not checked_space:
                    checked_space = True
                    self._update(agent_id, total_bytes=total)
                    if total is not None:
                        needed = total * DISK_SPACE_FACTOR
                        free = shutil.disk_usage(root).free
                        if free < needed:
                            raise AcpError(f"Not enough disk space: about {needed // 1024 ** 2} MB is needed and {free // 1024 ** 2} MB is free.")
                downloaded += len(chunk)
                if downloaded > MAX_ARCHIVE_BYTES:
                    raise AcpError("The download is larger than allowed.")
                digest.update(chunk)
                output.write(chunk)
                self._update(agent_id, downloaded_bytes=downloaded)
        if digest.hexdigest() != release.sha256:
            raise AcpError("The download does not match its sha256 checksum.")

    def _activate(self, agent_id: str, release: AgentRelease, extracted: Path, root: Path) -> None:
        binary = extracted / release.command
        if not binary.is_file():
            raise AcpError("The archive does not contain the expected program.")
        os.chmod(binary, binary.stat().st_mode | 0o755)
        versions = root / "versions"
        versions.mkdir(exist_ok=True)
        target = versions / release.version
        if target.exists():
            shutil.rmtree(target)
        os.replace(extracted, target)
        atomic_json(root / POINTER_FILE, {
            "version": release.version, "command": release.command, "args": list(release.args), "license": release.license,
        })

    @staticmethod
    def _remove_old_versions(root: Path, keep: str) -> None:
        for path in (root / "versions").iterdir():
            if path.name != keep:
                # A running older version stays locked on Windows; it is retried on the next install.
                shutil.rmtree(path, ignore_errors=True)

    def uninstall(self, agent_id: str) -> None:
        """Remove the binary, credentials and sessions of an agent."""
        if self.is_running(agent_id):
            raise InstallBusyError("An install is still running.")
        root = agent_root(agent_id, self.data_root)
        if root.exists():
            shutil.rmtree(root)
        with self._lock:
            self._jobs.pop(agent_id, None)
