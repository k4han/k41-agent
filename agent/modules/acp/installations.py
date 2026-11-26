"""Locate ACP executables and verify ownership of supported external installs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent.modules.acp.agents import ANTIGRAVITY_CLI_AGENT_ID, OPENCODE_AGENT_ID
from agent.modules.acp.errors import AcpError
from agent.modules.acp.process import build_agent_env
from agent.modules.tools import comparable_path
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

CLI_NAMES = {ANTIGRAVITY_CLI_AGENT_ID: "agy", OPENCODE_AGENT_ID: "opencode"}
OPENCODE_PACKAGES = {"opencode-ai": 1, "@opencode/cli": 2}
VERSION_PATTERN = re.compile(r"\bv?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?\b")
Source = Literal["managed", "google", "npm", "pnpm", "unknown"]


def version_number(value: str | None) -> tuple[int, int, int, bool] | None:
    match = VERSION_PATTERN.search(value or "")
    if not match:
        return None
    major, minor, patch, prerelease = match.groups()
    return int(major), int(minor), int(patch), prerelease is None


def acp_data_root() -> Path:
    home = os.environ.get("K41_AGENT_HOME") or os.environ.get("AGENT_HOME") or str(Path.home() / ".k41-agent")
    return Path(home) / "acp"


def managed_executable(agent_id: str) -> Path:
    name = CLI_NAMES[agent_id] + (".exe" if os.name == "nt" else "")
    return acp_data_root().resolve() / "runtimes" / agent_id / name


def find_cli(agent_id: str) -> tuple[Path | None, bool]:
    managed = managed_executable(agent_id)
    try:
        if managed.is_file() and managed.resolve() == managed:
            return managed, True
    except (OSError, RuntimeError):
        pass
    executable = shutil.which(CLI_NAMES[agent_id])
    return (Path(executable).absolute(), False) if executable else (None, False)


def any_acp_cli_installed() -> bool:
    return any(find_cli(agent_id)[0] is not None for agent_id in CLI_NAMES)


def google_default_executable() -> Path | None:
    if os.name != "nt":
        return Path.home().resolve() / ".local" / "bin" / "agy"
    local = os.environ.get("LOCALAPPDATA")
    return Path(local).resolve() / "agy" / "bin" / "agy.exe" if local else None


@dataclass(frozen=True)
class Installation:
    path: Path | None
    source: Source | None = None
    manager: str | None = None
    package: str | None = None
    package_root: Path | None = None
    package_binary: Path | None = None
    package_version: str | None = None

    @property
    def manageable(self) -> bool:
        return self.source in {"managed", "google", "npm", "pnpm"}

    def revision(self) -> str:
        identity: list[object] = [self.source, self.manager, self.package, self.package_version]
        for path in (self.path, self.package_root, self.package_binary):
            if path is not None:
                try:
                    info = path.stat()
                    identity.append((str(path), str(path.resolve()), info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size))
                except OSError:
                    identity.append((str(path), None))
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    def is_current(self, agent_id: str, revision: str) -> bool:
        path, managed = find_cli(agent_id)
        return path == self.path and managed == (self.source == "managed") and self.revision() == revision


def run_package_manager(
    executable: str, *args: str, stop: threading.Event | None = None,
) -> str:
    from agent.modules.tools import kill_process_tree, spawn_contained_process

    env = build_agent_env({
        key: value for key, value in os.environ.items()
        if key.upper() in {"PNPM_HOME", "NPM_CONFIG_PREFIX", "NPM_CONFIG_USERCONFIG", "NPM_CONFIG_GLOBALCONFIG",
                           "NPM_CONFIG_REGISTRY", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}
    })
    env.update({"CI": "true", "COREPACK_ENABLE_AUTO_PIN": "0", "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"})
    limit = time.monotonic() + (600 if stop is not None else 5)
    options = {} if os.name == "nt" else {"start_new_session": True}
    process = spawn_contained_process(
        [executable, *args], cwd=Path.home(), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **options, **hidden_subprocess_kwargs(),
    )
    try:
        with process:
            while True:
                if time.monotonic() >= limit or (stop is not None and stop.is_set()):
                    kill_process_tree(process)
                    raise AcpError("The package manager operation was interrupted or timed out.")
                try:
                    output, _ = process.communicate(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                raise AcpError("The package manager rejected the operation. Check the global installation and its permissions.")
            return output.decode("utf-8", "replace").strip()
    finally:
        if containment := getattr(process, "coding_job", None):
            containment.close()


def _pnpm_global_projects(manager: str) -> list[dict]:
    try:
        projects = json.loads(run_package_manager(manager, "list", "--global", "--depth=0", "--json"))
    except ValueError:
        raise AcpError("Could not verify the global pnpm packages.") from None
    if not isinstance(projects, list) or any(not isinstance(project, dict) for project in projects):
        raise AcpError("Could not verify the global pnpm packages.")
    return projects


def _package_locations(source: str, manager: str, command: Path) -> list[tuple[Path, str]]:
    if source == "npm":
        prefix = Path(run_package_manager(manager, "prefix", "--global"))
        shim_root = prefix if os.name == "nt" else prefix / "bin"
        if not prefix.is_absolute() or command.parent.resolve() != shim_root.resolve():
            return []
        root = prefix / ("node_modules" if os.name == "nt" else "lib/node_modules")
        return [(root / name, name) for name in OPENCODE_PACKAGES]

    locations: list[tuple[Path, str]] = []
    for group in _pnpm_global_projects(manager):
        if not isinstance(group.get("path"), str):
            continue
        root = Path(group["path"])
        if not root.is_absolute():
            continue
        root_path = comparable_path(root)
        packages = _dependency_fields(group)
        for name in packages.keys() & OPENCODE_PACKAGES.keys():
            entry = packages[name]
            path = entry.get("path") if isinstance(entry, dict) else None
            project = root
            if isinstance(path, str) and Path(path).is_absolute():
                # Recent pnpm versions report one list for all isolated installs.
                location = Path(path)
                levels = len(Path(name).parts)
                if len(location.parents) <= levels:
                    continue
                project = location.parents[levels]
                if comparable_path(location) != comparable_path(project / "node_modules" / name):
                    continue
            project_path = comparable_path(project)
            if project_path != root_path:
                if not project_path.is_relative_to(root_path):
                    continue
                manifest = json.loads((project / "package.json").read_text(encoding="utf-8"))
                group_packages = _dependency_fields(manifest) if isinstance(manifest, dict) else {}
            else:
                group_packages = packages
            # pnpm can remove an entire isolated install group in one command.
            if group_packages.keys() == {name}:
                locations.append((project / "node_modules" / name, name))
    if locations:
        bin_dir = Path(run_package_manager(manager, "bin", "--global"))
        if not bin_dir.is_absolute() or comparable_path(command.parent) != comparable_path(bin_dir):
            return []
    return locations


def _dependency_fields(manifest: dict) -> dict:
    dependencies = {}
    for field in ("dependencies", "devDependencies", "optionalDependencies"):
        if isinstance(entries := manifest.get(field), dict):
            dependencies.update(entries)
    return dependencies


def package_is_installed(installation: Installation) -> bool:
    if installation.source == "pnpm":
        # pnpm can retain files from inactive global installations in its store.
        return any(installation.package in _dependency_fields(project) for project in _pnpm_global_projects(installation.manager))
    return (installation.package_root / "package.json").is_file()


def _shim_points_to(path: Path, binary: Path) -> bool:
    binary_path = comparable_path(binary)
    if comparable_path(path) == binary_path:
        return True
    if path.suffix.lower() not in {"", ".cmd", ".ps1"} or path.stat().st_size > 32 * 1024:
        return False
    shim = path.read_text(encoding="utf-8", errors="replace")
    targets = re.findall(r'(?i)(?:%dp0%|%~dp0|\$basedir|\$\{basedir\})[\\/]([^"\r\n]+)', shim)
    return any(comparable_path(path.parent / target.replace("\\", "/")) == binary_path for target in targets)


def _opencode_owner(path: Path) -> Installation:
    matches = []
    for source in ("npm", "pnpm"):
        manager = shutil.which(source)
        if not manager:
            continue
        manager = str(Path(manager).absolute())
        try:
            candidates = _package_locations(source, manager, path)
        except (AcpError, OSError, ValueError, RuntimeError):
            continue
        for package, name in candidates:
            try:
                if source == "npm" and (package.is_symlink() or package.is_junction()):
                    continue
                metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
                if not isinstance(metadata, dict):
                    continue
                bin_field = metadata.get("bin")
                binary = bin_field.get("opencode") if isinstance(bin_field, dict) else bin_field
                version = metadata.get("version")
                if metadata.get("name") != name or not isinstance(binary, str) or not isinstance(version, str):
                    continue
                target = package / binary
                if comparable_path(target).is_relative_to(comparable_path(package)) and target.is_file() and _shim_points_to(path, target):
                    matches.append(Installation(path, source, manager, name, package, target, version))
            except (OSError, ValueError, RuntimeError):
                continue
    return matches[0] if len(matches) == 1 else Installation(path, "unknown")


def discover_installation(agent_id: str) -> Installation:
    path, managed = find_cli(agent_id)
    if path is None:
        return Installation(None)
    if managed:
        return Installation(path, "managed")
    if agent_id == ANTIGRAVITY_CLI_AGENT_ID:
        try:
            if path == google_default_executable() and path.is_file() and path.resolve() == path:
                return Installation(path, "google")
        except (OSError, RuntimeError):
            pass
        return Installation(path, "unknown")
    return _opencode_owner(path)
